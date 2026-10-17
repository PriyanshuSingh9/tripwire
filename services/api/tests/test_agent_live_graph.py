"""Agent -> MCP -> FalkorDB -> gate -> case, against the tiny real graph.

Skips when FalkorDB is not reachable (see ``tiny_graph``). The model is
scripted; everything from the MCP call down to the case store is real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from helpers import FakeScoreRepository, assert_no_labels
from test_falkordb_integration import (
    GRAPH_NAME,
    HOST,
    PORT,
    tiny_graph,  # noqa: F401 -- pytest fixture
)
from test_llm_agent import FakeSession, calls

from api.app import create_app, get_score_repository
from api.dependencies import get_llm_session_factory
from api.llm.provider import Turn

ANCHOR = "agt-1"
FIRST_PATH_TX = "agt-6"


def live_client(tmp_path: Path, session: FakeSession) -> TestClient:
    config = tmp_path / "baseline_config.json"
    config.write_text(json.dumps({"thresholds": {"lgbm": 0.5}}), encoding="utf-8")
    from api.settings import Settings

    app = create_app(
        Settings(
            _env_file=None,
            baseline_config_path=str(config),
            falkordb_host=HOST,
            falkordb_port=PORT,
            graph_name=GRAPH_NAME,
            case_db_path=str(tmp_path / "cases.db"),
            llm_provider="gemini",
            llm_api_key="not-used",
        )
    )
    app.dependency_overrides[get_score_repository] = lambda: FakeScoreRepository(
        {ANCHOR: (0.8, "2022-09-01 08:00"), "agt-missing": (0.7, "2022-09-01 09:00")}
    )
    app.dependency_overrides[get_llm_session_factory] = lambda: lambda *_args: session
    return TestClient(app, raise_server_exceptions=False)


def final_draft(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "hypothesis": f"Funds from {ANCHOR} are forwarded onward in {FIRST_PATH_TX}.",
        "supporting_transaction_ids": [ANCHOR, FIRST_PATH_TX],
        "proposed_action": "open_case",
        "action_reason": f"Onward transfer {FIRST_PATH_TX} supports analyst review.",
    }
    body.update(overrides)
    return json.dumps(body)


def test_agent_investigates_through_mcp_and_opens_a_case(
    tiny_graph: None, tmp_path: Path  # noqa: F811 -- imported pytest fixture
) -> None:
    session = FakeSession(
        turns=[
            calls(
                ("trace_funds", {"max_hops": 2}),
                ("detect_flow_patterns", {"pattern": "pass_through"}),
            ),
            Turn(),
        ],
        final=final_draft(),
    )
    with live_client(tmp_path, session) as client:
        response = client.post(
            "/api/investigations",
            json={"transaction_id": ANCHOR, "use_llm": True, "act": True, "max_hops": 2},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert_no_labels(body)
        assert body["report"]["source"] == "llm"
        assert body["outcome"] == "evidence_found"
        assert {e["transport"] for e in body["tool_trace"]} == {"mcp"}
        assert [e["origin"] for e in body["tool_trace"]] == ["policy", "model", "model"]
        assert all(e["status"] == "ok" for e in body["tool_trace"])
        assert all(e["duration_ms"] > 0 for e in body["tool_trace"])

        action = body["action"]
        assert action["status"] == "executed", action
        case = client.get(f"/api/cases/{action['case_id']}").json()
        assert case["evidence"][0]["evidence_refs"] == [ANCHOR, FIRST_PATH_TX]
        assert set(case["evidence"][0]["tool_run_ids"]) <= {
            e["tool_run_id"] for e in body["tool_trace"]
        }


def test_unknown_anchor_in_graph_does_not_traverse_or_open_a_case(
    tiny_graph: None, tmp_path: Path  # noqa: F811 -- imported pytest fixture
) -> None:
    session = FakeSession(turns=[Turn()], final=final_draft())
    with live_client(tmp_path, session) as client:
        response = client.post(
            "/api/investigations",
            json={"transaction_id": "agt-missing", "use_llm": True, "act": True},
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "transaction not found in graph"}
        assert client.get("/api/cases").json()["total"] == 0
