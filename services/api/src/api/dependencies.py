"""FastAPI dependencies shared by the routes.

Settings are built once per application (``create_app``) and read from
``app.state``. Score, threshold, and benchmark artifacts are cached per path
and file modification time, so hot endpoints do no repeated file IO while a
regenerated artifact is still picked up.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api import schemas
from api.database import CaseDatabase
from api.exceptions import GraphUnavailableError, ServiceUnavailableError
from api.graph import GraphUnavailable, get_graph_pool
from api.investigate import FalkorGraphTools, GraphTools
from api.llm import SessionFactory, create_session
from api.scores import (
    ScoreArtifactInvalid,
    ScoreArtifactUnavailable,
    ScoreRepository,
    load_scores,
)
from api.settings import REPOSITORY_ROOT, Settings


def resolve_path(value: str) -> Path:
    """Resolve a configured path relative to the repository root."""
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _mtime_ns(path: Path) -> int:
    """File modification time, or -1 when the file is absent."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


@lru_cache(maxsize=32)
def _read_json(path_str: str, _mtime_ns: int) -> Any:
    """Parse a JSON file; the mtime argument invalidates stale entries."""
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def read_json_cached(path: Path) -> Any:
    """Read JSON through the per-path, per-mtime cache."""
    return _read_json(str(path), _mtime_ns(path))


def app_settings(request: Request) -> Settings:
    """Settings captured when the application was created."""
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(app_settings)]


def get_score_repository(settings: SettingsDep) -> ScoreRepository:
    """Load the target-free score artifact or raise an explicit service error."""
    try:
        return load_scores(str(resolve_path(settings.score_artifact)))
    except (ScoreArtifactUnavailable, ScoreArtifactInvalid) as exc:
        raise ServiceUnavailableError("score artifact unavailable") from exc


def read_threshold(settings: Settings) -> float:
    """Read the locked validation threshold for the configured score model."""
    try:
        config = read_json_cached(resolve_path(settings.baseline_config_path))
        return float(config["thresholds"][settings.score_model])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ServiceUnavailableError("decision threshold unavailable") from exc


def get_graph_tools(settings: SettingsDep) -> GraphTools:
    """Return pooled graph tools or raise an explicit service error."""
    try:
        pool = get_graph_pool(
            settings.falkordb_host,
            settings.falkordb_port,
            settings.graph_name,
            max_connections=settings.graph_pool_size,
            query_timeout_ms=settings.graph_query_timeout_ms,
        )
    except GraphUnavailable as exc:
        raise GraphUnavailableError() from exc
    return FalkorGraphTools(pool.graph, timeout_ms=settings.graph_query_timeout_ms)


def get_llm_session_factory() -> SessionFactory:
    """Production LLM session factory; tests replace it with a fake."""
    return create_session


def _load_benchmarks(
    baseline_path: Path, graph_metrics_path: Path, performance_path: Path
) -> schemas.BenchmarksResponse:
    # Copy before merging: parsed JSON is shared through the read cache.
    payload: dict[str, Any] = json.loads(json.dumps(read_json_cached(baseline_path)))
    if "models" not in payload:
        payload = {"models": payload}
    if graph_metrics_path.is_file():
        graph_payload = read_json_cached(graph_metrics_path)
        graph_models = graph_payload.get("models", graph_payload)
        if not isinstance(graph_models, dict):
            raise ValueError("graph benchmark artifact must contain a models object")
        for model_name, metrics in graph_models.items():
            payload["models"].setdefault(model_name, metrics)
    if performance_path.is_file():
        payload["performance"] = read_json_cached(performance_path)
    return schemas.BenchmarksResponse.model_validate(payload)


@lru_cache(maxsize=8)
def _cached_benchmarks(
    baseline: str, graph_metrics: str, performance: str, _mtimes: tuple[int, int, int]
) -> schemas.BenchmarksResponse:
    return _load_benchmarks(Path(baseline), Path(graph_metrics), Path(performance))


def read_benchmarks(settings: Settings) -> schemas.BenchmarksResponse:
    """Load benchmark metrics (cached per file version) or raise a 503."""
    paths = (
        resolve_path(settings.baseline_metrics_path),
        resolve_path(settings.graph_metrics_path),
        resolve_path(settings.performance_path),
    )
    try:
        mtimes = (_mtime_ns(paths[0]), _mtime_ns(paths[1]), _mtime_ns(paths[2]))
        return _cached_benchmarks(str(paths[0]), str(paths[1]), str(paths[2]), mtimes)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ServiceUnavailableError("benchmark artifact unavailable") from exc


def get_case_database(request: Request) -> CaseDatabase:
    """The application's case database."""
    database: CaseDatabase = request.app.state.case_db
    return database


async def get_case_session(
    database: Annotated[CaseDatabase, Depends(get_case_database)],
) -> AsyncIterator[AsyncSession]:
    """Yield a case database session, mapping storage failures to 503."""
    try:
        await database.initialize()
    except Exception as exc:
        raise ServiceUnavailableError("case store unavailable") from exc
    async with database.sessions() as session:
        yield session
