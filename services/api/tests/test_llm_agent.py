"""LLM report agent tests: scripted fake model, fake graph, no network.

Every failure path must return a normal 200 deterministic report with a
coarse reason code; every success path must pass deterministic verification.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import FakeScoreRepository, assert_no_labels
from test_falkordb_integration import GRAPH_NAME as GRAPH
from test_falkordb_integration import HOST as GRAPH_HOST
from test_falkordb_integration import PORT as GRAPH_PORT
from test_falkordb_integration import tiny_graph  # noqa: F401 -- pytest fixture

from api.app import create_app, get_graph_tools, get_score_repository
from api.dependencies import get_llm_session_factory
from api.llm.provider import ProviderError, ToolCall, Turn
from api.llm.report import report_json_schema
from api.settings import Settings

ANCHOR = "hi8-tx-00000001"
SECOND = "hi8-tx-00000002"
FAN = "hi8-tx-00000005"
SECRET = "SECRET-KEY-123"


def transfer(n: int, source: str, target: str, amount: float, stamp: str) -> dict[str, Any]:
    return {
        "transaction_id": f"hi8-tx-{n:08d}",
        "timestamp": stamp,
        "source_account_key": source,
        "destination_account_key": target,
        "amount_paid": amount,
        "payment_currency": "US Dollar",
        "amount_received": amount,
        "receiving_currency": "US Dollar",
        "payment_format": "Wire",
    }


class LlmFakeTools:
    """Graph with an anchor transfer, a second hop, and one fan-out match."""

    def __init__(self, *, inspect_error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._inspect_error = inspect_error
        self.anchor = transfer(1, "1::a", "2::b", 12345.67, "2022-09-01 00:00")
        self.second = transfer(2, "2::b", "3::c", 12000.0, "2022-09-01 00:10")

    def _accounts(self) -> list[dict[str, str]]:
        return [{"account_key": key} for key in ("1::a", "2::b", "3::c")]

    def trace_funds(
        self, transaction_id: str, max_hops: int, time_window_seconds: int, result_limit: int
    ) -> dict[str, Any]:
        self.calls.append(("trace_funds", {"max_hops": max_hops}))
        return {
            "transactions": [self.anchor, self.second],
            "accounts": self._accounts(),
            "paths": [
                {
                    "anchor_transaction_id": ANCHOR,
                    "direction": "forward",
                    "depth": 2,
                    "account_keys": ["1::a", "2::b", "3::c"],
                    "transaction_ids": [ANCHOR, SECOND],
                }
            ],
            "query_duration_ms": 2.0,
        }

    def inspect_counterparties(
        self, transaction_id: str, time_window_seconds: int, result_limit: int
    ) -> dict[str, Any]:
        self.calls.append(("inspect_counterparties", {}))
        if self._inspect_error is not None:
            raise self._inspect_error
        return {
            "transactions": [self.anchor, self.second],
            "accounts": self._accounts(),
            "query_duration_ms": 1.0,
        }

    def detect_flow_patterns(
        self, account_key: str, pattern: str, time_window_seconds: int, result_limit: int
    ) -> dict[str, Any]:
        self.calls.append((f"pattern:{pattern}", {"account_key": account_key}))
        matches: list[dict[str, Any]] = []
        if pattern == "fan_out":
            matches = [
                {
                    "counterparty_key": "5::e",
                    "transfers": [
                        {
                            "transaction_id": FAN,
                            "timestamp": "2022-09-01 00:05",
                            "amount_paid": 50.0,
                            "payment_currency": "US Dollar",
                            "amount_received": 50.0,
                            "receiving_currency": "US Dollar",
                        }
                    ],
                }
            ]
        return {"matches": matches, "query_duration_ms": 0.5}

    def tools_called(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeSession:
    """Scripted model: yields prepared turns, then returns the final text."""

    def __init__(
        self,
        turns: list[Turn] | None = None,
        final: str | None = None,
        *,
        step_error: Exception | None = None,
        final_error: Exception | None = None,
        final_tokens: tuple[int, int] = (100, 40),
    ) -> None:
        self.turns = list(turns or [])
        self.final = final
        self.step_error = step_error
        self.final_error = final_error
        self.final_tokens = final_tokens
        self.tool_results: list[Any] = []
        self.final_prompt = ""

    def step(self, tool_results: Any, *, deadline: float) -> Turn:
        self.tool_results.append(tool_results)
        if self.step_error is not None:
            raise self.step_error
        return self.turns.pop(0) if self.turns else Turn()

    def final_report(self, prompt: str, schema: dict[str, Any], *, deadline: float) -> Turn:
        self.final_prompt = prompt
        if self.final_error is not None:
            raise self.final_error
        return Turn(
            text=self.final, input_tokens=self.final_tokens[0], output_tokens=self.final_tokens[1]
        )


def draft(**overrides: Any) -> str:
    """A valid draft citing the anchor, its path, and one verified claim."""
    body: dict[str, Any] = {
        "hypothesis": f"Funds from {ANCHOR} are forwarded by {SECOND} through 2::b to 3::c.",
        "supporting_transaction_ids": [ANCHOR, SECOND],
        "counterevidence_ids": [],
        "cited_claims": [
            {
                "transaction_id": ANCHOR,
                "timestamp": "2022-09-01 00:00",
                "amount_paid": 12345.67,
                "payment_currency": "US Dollar",
                "source_account_key": "1::a",
                "destination_account_key": "2::b",
            }
        ],
        "cited_paths": [
            {
                "transaction_ids": [ANCHOR, SECOND],
                "account_keys": ["1::a", "2::b", "3::c"],
            }
        ],
    }
    body.update(overrides)
    return json.dumps(body)


def calls(*items: tuple[str, dict[str, Any]]) -> Turn:
    return Turn(tool_calls=[ToolCall(name, args) for name, args in items])


def make_client(
    tmp_path: Path,
    session: FakeSession | None,
    tools: LlmFakeTools | None = None,
    **settings_overrides: Any,
) -> tuple[TestClient, LlmFakeTools, list[int]]:
    config = tmp_path / "baseline_config.json"
    config.write_text(json.dumps({"thresholds": {"lgbm": 0.5}}), encoding="utf-8")
    options: dict[str, Any] = {
        "baseline_config_path": str(config),
        "llm_provider": "gemini",
        "llm_api_key": SECRET,
    }
    options.update(settings_overrides)
    app = create_app(Settings(_env_file=None, **options))
    fake_tools = tools or LlmFakeTools()
    factory_calls: list[int] = []

    def factory(*_args: Any) -> FakeSession | None:
        factory_calls.append(1)
        return session

    app.dependency_overrides[get_score_repository] = lambda: FakeScoreRepository(
        {ANCHOR: (0.9, "2022-09-01 00:00"), SECOND: (0.1, "2022-09-01 00:10")}
    )
    app.dependency_overrides[get_graph_tools] = lambda: fake_tools
    app.dependency_overrides[get_llm_session_factory] = lambda: factory
    return TestClient(app, raise_server_exceptions=False), fake_tools, factory_calls


def post(client: TestClient, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "transaction_id": ANCHOR,
        "max_hops": 2,
        "time_window_seconds": 3600,
        "result_limit": 10,
        "use_llm": True,
    }
    payload.update(overrides)
    return client.post("/api/investigations", json=payload)


def two_tool_session(final: str | None = None) -> FakeSession:
    return FakeSession(
        turns=[
            calls(("trace_funds", {"max_hops": 2}), ("detect_flow_patterns", {"pattern": "fan_out"})),
            Turn(),
        ],
        final=final if final is not None else draft(),
    )


def test_completed_llm_report_is_verified(tmp_path: Path) -> None:
    client, tools, _ = make_client(tmp_path, two_tool_session())
    response = post(client)
    assert response.status_code == 200
    body = response.json()
    report = body["report"]
    assert report["source"] == "llm"
    assert report["llm"]["status"] == "completed"
    assert report["llm"]["tool_calls"] == 2
    assert report["llm"]["input_tokens"] == 100
    assert report["llm"]["estimated_cost_usd"] is None  # no prices configured: none assumed
    assert report["recommendation"] == "review"
    assert report["validation"] == "validated"
    assert body["decision"] == "review"
    assert body["risk_score"] == 0.9
    assert report["cited_paths"][0]["transaction_ids"] == [ANCHOR, SECOND]
    # Only the model-selected tools ran, plus the mandatory context lookup.
    assert tools.tools_called() == ["inspect_counterparties", "trace_funds", "pattern:fan_out"]
    names = [d["tool"] for d in body["evidence"]["tool_durations_ms"]]
    assert names == ["inspect_counterparties", "trace_funds", "detect_flow_patterns.fan_out"]
    assert FAN in {t["transaction_id"] for t in body["evidence"]["transactions"]}


@pytest.mark.parametrize(
    ("final", "reason"),
    [
        (draft(supporting_transaction_ids=["hi8-tx-00009999"]), "verification_failed"),
        (draft(counterevidence_ids=["hi8-tx-00009999"]), "verification_failed"),
        (
            draft(cited_claims=[{"transaction_id": ANCHOR, "amount_paid": 99.0}]),
            "verification_failed",
        ),
        (
            draft(cited_claims=[{"transaction_id": ANCHOR, "timestamp": "2030-01-01 00:00"}]),
            "verification_failed",
        ),
        (
            draft(cited_claims=[{"transaction_id": ANCHOR, "payment_currency": "Euro"}]),
            "verification_failed",
        ),
        (
            draft(cited_paths=[{"transaction_ids": [ANCHOR], "account_keys": ["1::a", "9::z"]}]),
            "verification_failed",
        ),
        (draft(hypothesis="See hi8-tx-00000999 for the onward leg."), "verification_failed"),
        (draft(hypothesis="Funds reach 9::zzz quickly."), "verification_failed"),
        (draft(hypothesis="A total of 99,999.00 moved onward."), "verification_failed"),
        (draft(supporting_transaction_ids=[]), "invalid_output"),
        (json.dumps({"hypothesis": "x", "recommendation": "block"}), "invalid_output"),
        ("this is not json", "invalid_output"),
        ("", "invalid_output"),
    ],
)
def test_bad_drafts_fall_back_to_deterministic(tmp_path: Path, final: str, reason: str) -> None:
    client, _, _ = make_client(tmp_path, two_tool_session(final=final))
    response = post(client)
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["source"] == "deterministic"
    assert report["validation"] == "validated"
    assert report["llm"]["status"] == "fallback"
    assert report["llm"]["fallback_reason"] == reason


def test_prose_with_evidenced_ids_and_counts_is_accepted(tmp_path: Path) -> None:
    text = f"{ANCHOR} and {SECOND} form a 2-hop chain; {FAN} is a separate fan-out leg."
    client, _, _ = make_client(tmp_path, two_tool_session(final=draft(hypothesis=text)))
    assert post(client).json()["report"]["source"] == "llm"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ProviderError("provider_error"), "provider_error"),
        (ProviderError("timeout"), "timeout"),
        (RuntimeError(f"upstream said {SECRET}"), "provider_error"),
    ],
)
def test_provider_failure_in_tool_loop_falls_back(
    tmp_path: Path, error: Exception, reason: str
) -> None:
    client, _, _ = make_client(tmp_path, FakeSession(step_error=error))
    response = post(client)
    assert response.status_code == 200
    assert response.json()["report"]["llm"]["fallback_reason"] == reason
    assert SECRET not in response.text


def test_provider_failure_in_final_report_falls_back(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path, FakeSession(final_error=ProviderError("timeout")))
    report = post(client).json()["report"]
    assert report["source"] == "deterministic"
    assert report["llm"]["fallback_reason"] == "timeout"


def test_unknown_tool_is_never_executed(tmp_path: Path) -> None:
    session = FakeSession(
        turns=[calls(("run_cypher", {"query": "MATCH (n) DETACH DELETE n"})), Turn()],
        final=draft(cited_paths=[]),  # no trace ran, so no path may be cited
    )
    client, tools, _ = make_client(tmp_path, session)
    body = post(client).json()
    assert body["report"]["source"] == "llm"
    assert tools.tools_called() == ["inspect_counterparties"]
    assert "unknown tool" in json.dumps(
        [m.payload for m in session.tool_results[1]]
    )


def test_tool_arguments_are_validated_and_clamped(tmp_path: Path) -> None:
    session = FakeSession(
        turns=[
            calls(
                ("trace_funds", {"max_hops": 3}),
                ("detect_flow_patterns", {"pattern": "'; DROP"}),
                ("detect_flow_patterns", {"pattern": "fan_in"}),
            ),
            Turn(),
        ],
        final=draft(),
    )
    client, tools, _ = make_client(tmp_path, session)
    post(client, max_hops=1)
    assert ("trace_funds", {"max_hops": 1}) in tools.calls  # clamped to the request bound
    assert tools.tools_called().count("pattern:fan_in") == 1
    assert not any("DROP" in str(args) for _, args in tools.calls)


def test_tool_call_budget_is_enforced(tmp_path: Path) -> None:
    session = FakeSession(
        turns=[
            calls(
                ("trace_funds", {"max_hops": 2}),
                ("detect_flow_patterns", {"pattern": "fan_out"}),
                ("detect_flow_patterns", {"pattern": "fan_in"}),
            ),
            Turn(),
        ],
        final=draft(),
    )
    client, tools, _ = make_client(tmp_path, session, llm_max_tool_calls=2)
    report = post(client).json()["report"]
    assert report["llm"]["tool_calls"] == 2
    assert "pattern:fan_in" not in tools.tools_called()


def test_round_budget_stops_a_looping_model(tmp_path: Path) -> None:
    looping = [calls(("trace_funds", {"max_hops": h})) for h in (1, 2, 1, 2, 1, 2)]
    client, tools, _ = make_client(
        tmp_path, FakeSession(turns=looping, final=draft()), llm_max_rounds=2
    )
    report = post(client).json()["report"]
    assert report["llm"]["rounds"] <= 3  # two tool rounds plus the final report
    assert tools.tools_called().count("trace_funds") <= 2


def test_duplicate_tool_calls_run_once(tmp_path: Path) -> None:
    same = ("trace_funds", {"max_hops": 2})
    client, tools, _ = make_client(
        tmp_path, FakeSession(turns=[calls(same, same), Turn()], final=draft())
    )
    post(client)
    assert tools.tools_called().count("trace_funds") == 1


def test_use_llm_false_never_touches_the_provider(tmp_path: Path) -> None:
    client, _, factory_calls = make_client(tmp_path, two_tool_session())
    body = post(client, use_llm=False).json()
    assert factory_calls == []
    assert body["report"]["source"] == "deterministic"
    assert body["report"]["llm"] is None


def test_not_configured_returns_deterministic_report(tmp_path: Path) -> None:
    client, _, factory_calls = make_client(
        tmp_path, two_tool_session(), llm_provider="none", llm_api_key=None
    )
    response = post(client)
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["source"] == "deterministic"
    assert report["llm"]["status"] == "unavailable"
    assert report["llm"]["fallback_reason"] == "not_configured"
    assert factory_calls == []


def test_missing_session_falls_back_as_not_configured(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path, None)
    report = post(client).json()["report"]
    assert report["llm"]["fallback_reason"] == "not_configured"


def test_graph_failure_is_not_masked_by_fallback(tmp_path: Path) -> None:
    client, _, _ = make_client(
        tmp_path, two_tool_session(), tools=LlmFakeTools(inspect_error=ConnectionError("boom"))
    )
    response = post(client)
    assert response.status_code == 503
    assert response.json() == {"detail": "graph unavailable"}


def test_unscored_transaction_is_404_before_any_model_call(tmp_path: Path) -> None:
    client, _, factory_calls = make_client(tmp_path, two_tool_session())
    assert post(client, transaction_id="hi8-tx-00004242").status_code == 404
    assert factory_calls == []


def test_cost_is_estimated_only_when_prices_are_configured(tmp_path: Path) -> None:
    client, _, _ = make_client(
        tmp_path,
        two_tool_session(),
        llm_price_input_per_mtok=1.0,
        llm_price_output_per_mtok=4.0,
    )
    llm = post(client).json()["report"]["llm"]
    assert llm["input_tokens"] == 100
    assert llm["output_tokens"] == 40
    assert llm["estimated_cost_usd"] == pytest.approx(100 / 1e6 * 1.0 + 40 / 1e6 * 4.0)


def test_secret_never_appears_in_any_response(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path, two_tool_session())
    texts = [post(client).text, client.get("/api/health").text]
    for text in texts:
        assert SECRET not in text
    assert client.get("/api/health").json()["llm"] == "configured"


def test_health_reports_unconfigured_llm(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path, None, llm_provider="none", llm_api_key=None)
    assert client.get("/api/health").json()["llm"] == "not_configured"


def test_context_prompt_excludes_label_and_secret_material(tmp_path: Path) -> None:
    session = two_tool_session()
    client, _, _ = make_client(tmp_path, session)
    post(client)
    lowered = session.final_prompt.lower()
    for forbidden in ("laundering", "typology", SECRET.lower()):
        assert forbidden not in lowered
    assert "read_only_policy_context" in session.final_prompt


def test_settings_accept_documented_and_prefixed_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_API_KEY", "k1")
    settings = Settings(_env_file=None)
    assert settings.llm_configured
    assert settings.llm_model == "gemini-flash-latest"
    assert "k1" not in repr(settings)
    monkeypatch.delenv("LLM_PROVIDER")
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.setenv("TRACEAML_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k2")
    assert Settings(_env_file=None).llm_configured


def test_blank_env_lines_mean_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "gemini-flash-latest")
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "none"
    assert not settings.llm_configured


def test_provider_schema_uses_only_portable_json_schema() -> None:
    text = json.dumps(report_json_schema())
    for unsupported in ("$ref", "$defs", "anyOf", "maxLength", "maxItems", "additionalProperties"):
        assert unsupported not in text


def test_agent_over_a_real_falkordb_graph(
    tiny_graph: None,  # noqa: F811 -- imported pytest fixture
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scripted model + real FalkorDB tools: evidence shapes and property names are real."""
    config = tmp_path / "baseline_config.json"
    config.write_text(json.dumps({"thresholds": {"lgbm": 0.5}}), encoding="utf-8")
    monkeypatch.setenv("TRACEAML_BASELINE_CONFIG_PATH", str(config))
    monkeypatch.setenv("TRACEAML_FALKORDB_HOST", GRAPH_HOST)
    monkeypatch.setenv("TRACEAML_FALKORDB_PORT", str(GRAPH_PORT))
    monkeypatch.setenv("TRACEAML_GRAPH_NAME", GRAPH)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    final = json.dumps(
        {
            "hypothesis": "agt-1 is one of several transfers fanning out from the same sender.",
            "supporting_transaction_ids": ["agt-1", "agt-2", "agt-3"],
            "counterevidence_ids": [],
            "cited_claims": [
                {
                    "transaction_id": "agt-1",
                    "timestamp": "2022-09-01 08:00",
                    "amount_paid": 1000.0,
                    "payment_currency": "US Dollar",
                    "source_account_key": "10::A",
                    "destination_account_key": "10::B",
                }
            ],
            "cited_paths": [],
        }
    )
    session = FakeSession(
        turns=[
            calls(
                ("trace_funds", {"max_hops": 2}),
                ("detect_flow_patterns", {"pattern": "fan_out"}),
                ("detect_flow_patterns", {"pattern": "fan_in"}),
                ("detect_flow_patterns", {"pattern": "pass_through"}),
            ),
            Turn(),
        ],
        final=final,
    )
    app = create_app()
    app.dependency_overrides[get_score_repository] = lambda: FakeScoreRepository(
        {"agt-1": (0.8, "2022-09-01 08:00")}
    )
    app.dependency_overrides[get_llm_session_factory] = lambda: lambda *_args: session
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/investigations",
            json={
                "transaction_id": "agt-1",
                "max_hops": 2,
                "time_window_seconds": 86_400,
                "result_limit": 50,
                "use_llm": True,
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["report"]["source"] == "llm", body["report"]["llm"]
    assert body["report"]["llm"]["status"] == "completed"
    tools = [entry["tool"] for entry in body["evidence"]["tool_durations_ms"]]
    assert tools == [
        "inspect_counterparties",
        "trace_funds",
        "detect_flow_patterns.fan_out",
        "detect_flow_patterns.fan_in",
        "detect_flow_patterns.pass_through",
    ]
    assert {"agt-1", "agt-2", "agt-3"} <= {
        t["transaction_id"] for t in body["evidence"]["transactions"]
    }
    assert_no_labels(body)
