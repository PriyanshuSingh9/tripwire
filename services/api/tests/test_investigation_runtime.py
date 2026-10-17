"""Concurrency of graph tool calls and typed error mapping at the boundary."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import FakeGraphTools, FakeScoreRepository, make_evidence

from api.app import create_app, get_graph_tools, get_score_repository
from api.exceptions import GraphTimeoutError, GraphUnavailableError, TransactionNotFoundError
from api.investigate import build_investigation, classify_tool_error

TOOL_SLEEP_SECONDS = 0.3


class SleepyTools(FakeGraphTools):
    """Fake tools that block like real queries and record peak overlap."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._active = 0
        self.peak = 0

    def _sleep(self) -> None:
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)
        time.sleep(TOOL_SLEEP_SECONDS)
        with self._lock:
            self._active -= 1

    def trace_funds(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._sleep()
        return super().trace_funds(*args, **kwargs)

    def inspect_counterparties(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._sleep()
        return super().inspect_counterparties(*args, **kwargs)

    def detect_flow_patterns(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._sleep()
        return super().detect_flow_patterns(*args, **kwargs)


def test_tool_calls_run_concurrently() -> None:
    """Five blocking tools finish in about two stage maxima, not their sum."""
    tools = SleepyTools()
    with ThreadPoolExecutor(max_workers=8) as executor:
        started = time.perf_counter()
        response = build_investigation(
            "tx_1", 0.9, 0.5, tools, 2, 3600, 10, executor=executor
        )
        elapsed = time.perf_counter() - started
    serial = 5 * TOOL_SLEEP_SECONDS
    assert elapsed < serial * 0.6, f"{elapsed:.3f}s is not concurrent (serial {serial}s)"
    assert tools.peak >= 3
    assert len(tools.calls) == 5
    # Wall time is what is reported; per-tool durations are kept separately.
    assert response.evidence.query_duration_ms == pytest.approx(elapsed * 1000.0, rel=0.25)
    assert len(response.evidence.tool_durations_ms) == 5


class SlowTraceTools(SleepyTools):
    """Counterparty lookup returns the anchor at once; traversal is slow."""

    def trace_funds(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._sleep()
        self._sleep()
        return FakeGraphTools.trace_funds(self, *args, **kwargs)

    def inspect_counterparties(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        FakeGraphTools.inspect_counterparties(self, *args, **kwargs)
        return {**make_evidence(), "paths": []}


def test_patterns_overlap_slow_traversal() -> None:
    """Pattern queries start once the anchor is known, not after the trace."""
    tools = SlowTraceTools()
    with ThreadPoolExecutor(max_workers=8) as executor:
        started = time.perf_counter()
        response = build_investigation(
            "tx_1", 0.9, 0.5, tools, 2, 3600, 10, executor=executor
        )
        elapsed = time.perf_counter() - started
    # Trace takes 2 sleeps; patterns (1 sleep) run alongside it.
    assert elapsed < 3 * TOOL_SLEEP_SECONDS * 0.9, f"{elapsed:.3f}s did not overlap"
    assert tools.peak == 4
    assert response.report is not None
    assert response.report.supporting_transaction_ids == ["tx_1"]


class RaisingTools(FakeGraphTools):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    def trace_funds(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._exc


def write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "baseline_config.json"
    config.write_text(json.dumps({"thresholds": {"lgbm": 0.5}}), encoding="utf-8")
    monkeypatch.setenv("TRACEAML_BASELINE_CONFIG_PATH", str(config))


def post(app_tools: FakeGraphTools | None) -> Any:
    app = create_app()
    app.dependency_overrides[get_score_repository] = lambda: FakeScoreRepository()
    if app_tools is not None:
        app.dependency_overrides[get_graph_tools] = lambda: app_tools
    with TestClient(app, raise_server_exceptions=False) as client:
        return client.post(
            "/api/investigations",
            json={"transaction_id": "tx_1", "max_hops": 2,
                  "time_window_seconds": 3600, "result_limit": 10},
        )


def test_unconfigured_graph_is_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(tmp_path, monkeypatch)
    monkeypatch.setenv("TRACEAML_FALKORDB_HOST", "")
    response = post(None)
    assert response.status_code == 503
    assert response.json() == {"detail": "graph unavailable"}


def test_unreachable_graph_is_503_without_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused connection maps to 503 and never echoes host, port, or errors."""
    write_config(tmp_path, monkeypatch)
    monkeypatch.setenv("TRACEAML_FALKORDB_HOST", "127.0.0.1")
    monkeypatch.setenv("TRACEAML_FALKORDB_PORT", "1")
    monkeypatch.setenv("TRACEAML_GRAPH_NAME", "agentapi-unreachable")
    response = post(None)
    assert response.status_code == 503
    assert response.json() == {"detail": "graph unavailable"}
    assert "127.0.0.1" not in response.text
    assert "Error" not in response.text


@pytest.mark.parametrize(
    ("exc", "status", "detail"),
    [
        (ValueError("unknown transaction_id: tx_1"), 404, "transaction not found in graph"),
        (TimeoutError("socket"), 504, "graph query timed out"),
        (ConnectionError("refused 10.0.0.1:6379"), 503, "graph unavailable"),
        (RuntimeError("secret internals"), 500, "graph evidence unavailable"),
    ],
)
def test_tool_errors_map_to_typed_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    status: int,
    detail: str,
) -> None:
    write_config(tmp_path, monkeypatch)
    response = post(RaisingTools(exc))
    assert response.status_code == status
    assert response.json() == {"detail": detail}


def test_redis_errors_are_classified() -> None:
    from redis import exceptions as redis_errors

    assert isinstance(
        classify_tool_error(redis_errors.ConnectionError("x")), GraphUnavailableError
    )
    assert isinstance(classify_tool_error(redis_errors.TimeoutError("x")), GraphTimeoutError)
    assert isinstance(
        classify_tool_error(redis_errors.ResponseError("Query timed out")), GraphTimeoutError
    )
    assert isinstance(
        classify_tool_error(ValueError("unknown transaction_id: t")), TransactionNotFoundError
    )


def test_unexpected_errors_never_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything unclassified outside the tools still returns a generic 500."""
    write_config(tmp_path, monkeypatch)

    class BrokenScores(FakeScoreRepository):
        def score_for(self, transaction_id: str) -> float | None:
            raise RuntimeError("disk path C:/secret")

    app = create_app()
    app.dependency_overrides[get_score_repository] = lambda: BrokenScores()
    app.dependency_overrides[get_graph_tools] = lambda: FakeGraphTools(make_evidence())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/investigations", json={"transaction_id": "tx_1"})
    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
