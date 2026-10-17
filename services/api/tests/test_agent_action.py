"""MCP boundary, tool trace and the deterministic case-action gate.

The agent is scripted (no network) and the graph is a fake, but every tool
call goes through a real MCP server and client session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import assert_no_labels
from test_llm_agent import (
    ANCHOR,
    FAN,
    SECOND,
    FakeSession,
    LlmFakeTools,
    calls,
    draft,
    make_client,
    post,
)

from api import schemas
from api.actions import gate
from api.exceptions import (
    GraphTimeoutError,
    GraphUnavailableError,
    InvestigationError,
    TransactionNotFoundError,
)
from api.llm.provider import Turn
from api.mcp_graph import McpGraphTools


class NoPathTools(LlmFakeTools):
    """Graph that answers every query successfully but finds no path or pattern."""

    def trace_funds(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = super().trace_funds(*args, **kwargs)
        return {**result, "paths": []}

    def detect_flow_patterns(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = super().detect_flow_patterns(*args, **kwargs)
        return {**result, "matches": []}


def proposing(action: str, reason: str = "", **overrides: Any) -> str:
    return draft(
        proposed_action=action,
        action_reason=reason or f"Onward leg {SECOND} supports analyst review.",
        **overrides,
    )


def agent_session(final: str, runs: int = 4) -> FakeSession:
    """Scripted model that repeats the same two-tool investigation per request."""
    turns: list[Turn] = []
    for _ in range(runs):
        turns.append(
            calls(
                ("trace_funds", {"max_hops": 2}),
                ("detect_flow_patterns", {"pattern": "fan_out"}),
            )
        )
        turns.append(Turn())
    return FakeSession(turns=turns, final=final)


def client_for(
    tmp_path: Path, final: str, tools: LlmFakeTools | None = None, **overrides: Any
) -> TestClient:
    client, _, _ = make_client(
        tmp_path,
        agent_session(final),
        tools,
        case_db_path=str(tmp_path / "cases.db"),
        **overrides,
    )
    return client


def act(client: TestClient, **overrides: Any) -> Any:
    return post(client, act=True, **overrides)


# --- MCP boundary -----------------------------------------------------------


def test_server_advertises_exactly_the_three_graph_tools() -> None:
    bridge = McpGraphTools(LlmFakeTools())
    try:
        assert sorted(bridge.list_tool_names()) == [
            "detect_flow_patterns",
            "inspect_counterparties",
            "trace_funds",
        ]
    finally:
        bridge.close()


def test_agent_tool_calls_run_through_mcp_with_a_trace(tmp_path: Path) -> None:
    client = client_for(tmp_path, draft())
    body = post(client).json()
    trace = body["tool_trace"]
    assert [(e["tool"], e["origin"]) for e in trace] == [
        ("inspect_counterparties", "policy"),
        ("trace_funds", "model"),
        ("detect_flow_patterns", "model"),
    ]
    assert {e["transport"] for e in trace} == {"mcp"}
    assert trace[1]["arguments"]["max_hops"] == 2
    assert trace[1]["outcome"] == "evidence_found"
    assert trace[1]["path_count"] == 1
    assert len({e["tool_run_id"] for e in trace}) == 3
    assert body["outcome"] == "evidence_found"
    assert_no_labels(body)


def test_direct_transport_is_selectable_and_traced(tmp_path: Path) -> None:
    client = client_for(tmp_path, draft(), agent_tool_transport="direct")
    trace = post(client).json()["tool_trace"]
    assert {e["transport"] for e in trace} == {"direct"}


def test_deterministic_path_records_a_direct_trace(tmp_path: Path) -> None:
    client = client_for(tmp_path, draft())
    body = post(client, use_llm=False).json()
    assert {e["transport"] for e in body["tool_trace"]} == {"direct"}
    assert {e["origin"] for e in body["tool_trace"]} == {"policy"}
    assert body["report"]["source"] == "deterministic"


def test_out_of_range_input_is_rejected_before_any_graph_query() -> None:
    tools = LlmFakeTools()
    bridge = McpGraphTools(tools)
    try:
        with pytest.raises(InvestigationError):
            bridge.trace_funds(ANCHOR, max_hops=9, time_window_seconds=3600, result_limit=10)
        with pytest.raises(InvestigationError):
            bridge.detect_flow_patterns("1::a", "typology", 3600, 10)
        with pytest.raises(InvestigationError):
            bridge.inspect_counterparties(ANCHOR, 3600, 1000)
        assert tools.calls == []
        assert [e.outcome for e in bridge.trace_snapshot()] == ["invalid_input"] * 3
    finally:
        bridge.close()


@pytest.mark.parametrize(
    ("error", "expected", "outcome"),
    [
        (ValueError("unknown transaction_id: x"), TransactionNotFoundError, "unknown_anchor"),
        (ConnectionError("down"), GraphUnavailableError, "inconclusive"),
        (TimeoutError("slow"), GraphTimeoutError, "inconclusive"),
        (RuntimeError("boom"), InvestigationError, "inconclusive"),
    ],
)
def test_graph_failures_keep_their_typed_errors_over_mcp(
    error: Exception, expected: type[Exception], outcome: str
) -> None:
    bridge = McpGraphTools(LlmFakeTools(inspect_error=error))
    try:
        with pytest.raises(expected):
            bridge.inspect_counterparties(ANCHOR, 3600, 10)
        entry = bridge.trace_snapshot()[0]
        assert (entry.outcome, entry.status) == (outcome, "error")
    finally:
        bridge.close()


def test_graph_timeout_maps_to_a_gateway_timeout() -> None:
    from redis import exceptions as redis_errors

    bridge = McpGraphTools(LlmFakeTools(inspect_error=redis_errors.TimeoutError("t")))
    try:
        with pytest.raises(GraphTimeoutError):
            bridge.inspect_counterparties(ANCHOR, 3600, 10)
    finally:
        bridge.close()


# --- typed outcomes ---------------------------------------------------------


def test_no_matching_path_is_reported_and_never_escalates(tmp_path: Path) -> None:
    client = client_for(tmp_path, proposing("open_case", cited_paths=[]), NoPathTools())
    body = act(client).json()
    assert body["outcome"] == "no_matching_path"
    # The model asked to open a case; the gate refuses without graph evidence.
    assert body["action"]["status"] == "denied"
    assert body["action"]["denial_code"] == "outcome_not_actionable"
    assert client.get("/api/cases").json()["total"] == 0


def test_capped_result_without_paths_is_inconclusive_not_negative(tmp_path: Path) -> None:
    client = client_for(tmp_path, draft(), NoPathTools())
    body = post(client, result_limit=1).json()
    assert body["outcome"] == "inconclusive"


# --- controlled case action -------------------------------------------------


def test_agent_opens_a_case_with_evidence_refs_and_tool_runs(tmp_path: Path) -> None:
    client = client_for(tmp_path, proposing("open_case"))
    body = act(client).json()
    action = body["action"]
    assert (action["proposed_action"], action["proposed_by"]) == ("open_case", "agent")
    assert action["status"] == "executed"
    assert set(action["evidence_refs"]) == {ANCHOR, SECOND}
    assert set(action["tool_run_ids"]) <= {e["tool_run_id"] for e in body["tool_trace"]}
    assert action["tool_run_ids"]

    case = client.get(f"/api/cases/{action['case_id']}").json()
    assert case["status"] == "suspected"
    assert case["opened_by"] == "traceaml-agent"
    assert case["risk_score"] == body["risk_score"] == 0.9
    assert [h["action"] for h in case["history"]] == ["open"]
    [evidence] = case["evidence"]
    assert evidence["evidence_refs"] == action["evidence_refs"]
    assert evidence["tool_run_ids"] == action["tool_run_ids"]
    assert evidence["decision"] == "review"
    assert evidence["investigation_id"] == body["investigation_id"]
    assert_no_labels(case)


def test_second_open_for_the_same_transaction_is_denied(tmp_path: Path) -> None:
    client = client_for(tmp_path, proposing("open_case"))
    first = act(client).json()["action"]
    second = act(client).json()["action"]
    assert first["status"] == "executed"
    assert (second["status"], second["denial_code"]) == ("denied", "case_already_active")
    assert client.get("/api/cases").json()["total"] == 1


def test_enrich_requires_an_open_case_then_is_not_repeated(tmp_path: Path) -> None:
    client = client_for(tmp_path, proposing("enrich_case"))
    missing = act(client).json()["action"]
    assert (missing["status"], missing["denial_code"]) == ("denied", "no_open_case")

    opened = client.post("/api/cases", json={"transaction_id": ANCHOR, "opened_by": "analyst-a"})
    assert opened.status_code == 201

    enriched = act(client).json()["action"]
    assert enriched["status"] == "executed", enriched
    repeated = act(client).json()["action"]
    assert (repeated["status"], repeated["denial_code"]) == ("denied", "duplicate_enrichment")

    case = client.get(f"/api/cases/{enriched['case_id']}").json()
    assert [h["action"] for h in case["history"]] == ["open", "enrich"]
    assert case["status"] == "suspected"
    assert [e["action"] for e in case["evidence"]][-1] == "enrich_case"


def test_no_proposal_means_no_action(tmp_path: Path) -> None:
    client = client_for(tmp_path, draft())
    action = act(client).json()["action"]
    assert (action["proposed_action"], action["status"]) == ("none", "no_action")
    assert client.get("/api/cases").json()["total"] == 0


def test_action_is_opt_in(tmp_path: Path) -> None:
    client = client_for(tmp_path, proposing("open_case"))
    body = post(client).json()
    assert body["action"] is None
    assert client.get("/api/cases").json()["total"] == 0


def test_policy_opens_a_case_without_an_llm(tmp_path: Path) -> None:
    client = client_for(tmp_path, draft())
    body = post(client, use_llm=False, act=True).json()
    action = body["action"]
    assert (action["proposed_by"], action["status"]) == ("policy", "executed")
    case = client.get(f"/api/cases/{action['case_id']}").json()
    assert case["opened_by"] == "traceaml-policy"
    assert body["decision"] == "review"


def test_allow_decision_is_never_opened_as_a_case(tmp_path: Path) -> None:
    client = client_for(tmp_path, draft())
    # SECOND scores 0.1, under the 0.5 threshold, so its decision is allow.
    body = post(client, use_llm=False, act=True, transaction_id=SECOND).json()
    assert body["decision"] == "allow"
    assert body["action"]["status"] == "no_action"


def test_action_never_changes_score_or_decision(tmp_path: Path) -> None:
    client = client_for(tmp_path, proposing("open_case"))
    before = post(client, use_llm=False).json()
    after = act(client).json()
    assert (before["risk_score"], before["decision"]) == (after["risk_score"], after["decision"])


# --- gate (stateless) -------------------------------------------------------


def _response(**overrides: Any) -> schemas.InvestigationResponse:
    base: dict[str, Any] = {
        "investigation_id": "inv-1",
        "transaction_id": ANCHOR,
        "risk_score": 0.9,
        "decision": "review",
        "policy_version": "v1",
        "evidence": {
            "transactions": [
                {
                    "transaction_id": ANCHOR,
                    "timestamp": "2022-09-01 00:00",
                    "source_account_key": "1::a",
                    "destination_account_key": "2::b",
                },
                {
                    "transaction_id": FAN,
                    "timestamp": "2022-09-01 00:05",
                    "source_account_key": "1::a",
                    "destination_account_key": "5::e",
                },
            ],
            "accounts": [],
            "paths": [],
            "query_duration_ms": 1.0,
        },
        "report": None,
        "created_at": "2022-09-01T00:00:00Z",
        "outcome": "evidence_found",
        "tool_trace": [],
    }
    base.update(overrides)
    return schemas.InvestigationResponse.model_validate(base)


def test_gate_allows_verified_refs() -> None:
    assert gate("open_case", _response(), [ANCHOR, FAN]) is None


@pytest.mark.parametrize(
    ("action", "overrides", "refs", "code"),
    [
        ("open_case", {"outcome": "no_matching_path"}, [ANCHOR], "outcome_not_actionable"),
        ("open_case", {"outcome": "inconclusive"}, [ANCHOR], "outcome_not_actionable"),
        ("open_case", {"decision": "allow"}, [ANCHOR], "decision_not_review"),
        ("open_case", {}, [], "evidence_refs_missing"),
        ("open_case", {}, [ANCHOR, "hi8-tx-00009999"], "evidence_ref_unverified"),
        ("enrich_case", {}, ["1::a"], "evidence_ref_unverified"),
    ],
)
def test_gate_refuses(action: Any, overrides: dict[str, Any], refs: list[str], code: str) -> None:
    assert gate(action, _response(**overrides), refs) == code
