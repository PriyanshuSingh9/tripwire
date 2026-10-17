"""Typed FastAPI application for the TraceAML API service.

This module is the single service entrypoint (``api.app:app``), used by
``make dev-api``, the README commands, and the container image.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import anyio.to_thread
from fastapi import Depends, FastAPI, Query, Request

from api import schemas
from api.actions import apply_case_action
from api.cases.router import router as cases_router
from api.database import CaseDatabase
from api.dependencies import (
    SettingsDep,
    get_graph_tools,
    get_llm_session_factory,
    get_score_repository,
    read_benchmarks,
    read_threshold,
    resolve_path,
)
from api.exceptions import (
    ServiceUnavailableError,
    TransactionNotFoundError,
    setup_exception_handlers,
)
from api.graph import SOCKET_TIMEOUT_MARGIN_SECONDS, close_graph_pools, probe_graph
from api.investigate import GraphTools, build_investigation
from api.llm import SessionFactory, run_llm_investigation
from api.mcp_graph import McpGraphTools
from api.scores import (
    ScoreArtifactInvalid,
    ScoreArtifactUnavailable,
    ScoreRepository,
    load_scores,
)
from api.settings import Settings
from api.tooltrace import TracedTools, TracingTools

__all__ = ["app", "create_app", "get_graph_tools", "get_score_repository"]

# Sequential database calls per tool, worst case: stage one tools run two
# queries, pattern tools three. Used to bound the whole evidence phase.
QUERIES_PER_INVESTIGATION_PATH = 5


def investigation_wait_seconds(settings: Settings) -> float:
    """Upper bound for the evidence phase derived from the per-query timeout."""
    per_query = settings.graph_query_timeout_ms / 1000.0
    return QUERIES_PER_INVESTIGATION_PATH * per_query + 2 * SOCKET_TIMEOUT_MARGIN_SECONDS


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with settings captured once."""
    resolved = settings if settings is not None else Settings()
    case_db = CaseDatabase(resolve_path(resolved.case_db_path))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Warm the cached score artifact so the first queue read is not the
        # one paying for the parquet load; absence is reported per request.
        try:
            await anyio.to_thread.run_sync(
                load_scores, str(resolve_path(resolved.score_artifact))
            )
        except (ScoreArtifactUnavailable, ScoreArtifactInvalid):
            pass
        yield
        await case_db.close()
        close_graph_pools()

    app = FastAPI(title="TraceAML API", version=resolved.app_version, lifespan=lifespan)
    app.state.settings = resolved
    app.state.case_db = case_db
    setup_exception_handlers(app)

    @app.get("/api/health", response_model=schemas.HealthResponse)
    def get_health(settings: SettingsDep) -> schemas.HealthResponse:
        return schemas.HealthResponse(
            status="ok",
            falkordb=probe_graph(settings.falkordb_host, settings.falkordb_port),
            version=settings.app_version,
            llm="configured" if settings.llm_configured else "not_configured",
        )

    @app.get("/api/alerts", response_model=schemas.AlertsResponse)
    def get_alerts(
        settings: SettingsDep,
        repo: Annotated[ScoreRepository, Depends(get_score_repository)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> schemas.AlertsResponse:
        threshold = read_threshold(settings)
        items = [
            schemas.AlertItem(
                transaction_id=transaction_id,
                timestamp=timestamp,
                score=score,
                decision="review" if score >= threshold else "allow",
            )
            for transaction_id, score, timestamp in repo.ranked(limit, offset)
        ]
        return schemas.AlertsResponse(items=items, total=len(repo), limit=limit, offset=offset)

    @app.get("/api/benchmarks", response_model=schemas.BenchmarksResponse)
    def get_benchmarks(settings: SettingsDep) -> schemas.BenchmarksResponse:
        return read_benchmarks(settings)

    @app.post("/api/investigations", response_model=schemas.InvestigationResponse)
    async def post_investigation(
        request: schemas.InvestigationRequest,
        http_request: Request,
        settings: SettingsDep,
        repo: Annotated[ScoreRepository, Depends(get_score_repository)],
        tools: Annotated[GraphTools, Depends(get_graph_tools)],
        session_factory: Annotated[SessionFactory, Depends(get_llm_session_factory)],
    ) -> schemas.InvestigationResponse:
        score = repo.score_for(request.transaction_id)
        if score is None:
            raise TransactionNotFoundError()
        threshold = read_threshold(settings)

        def investigate() -> schemas.InvestigationResponse:
            # The LLM agent reaches graph tools through the MCP boundary; the
            # deterministic path calls them directly. Both record a trace.
            traced: TracedTools
            if request.use_llm and settings.agent_tool_transport == "mcp":
                traced = McpGraphTools(tools)
            else:
                traced = TracingTools(tools)
            try:
                if request.use_llm:
                    return run_llm_investigation(
                        request.transaction_id,
                        score,
                        threshold,
                        traced,
                        max_hops=request.max_hops,
                        time_window_seconds=request.time_window_seconds,
                        result_limit=request.result_limit,
                        settings=settings,
                        session_factory=session_factory,
                        wait_timeout_seconds=investigation_wait_seconds(settings),
                    )
                return build_investigation(
                    request.transaction_id,
                    score,
                    threshold,
                    traced,
                    max_hops=request.max_hops,
                    time_window_seconds=request.time_window_seconds,
                    result_limit=request.result_limit,
                    wait_timeout_seconds=investigation_wait_seconds(settings),
                )
            finally:
                traced.close()

        response = await anyio.to_thread.run_sync(investigate)
        if not request.act:
            return response
        database: CaseDatabase = http_request.app.state.case_db
        try:
            await database.initialize()
        except Exception as exc:
            raise ServiceUnavailableError("case store unavailable") from exc
        async with database.sessions() as session:
            action = await apply_case_action(response, session)
        return response.model_copy(update={"action": action})

    app.include_router(cases_router)
    return app


app = create_app()
