"""Route tests with fakes: no FalkorDB, no score artifact on disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import (
    FakeGraphTools,
    FakeScoreRepository,
    assert_no_labels,
)

from api import schemas
from api.app import create_app, get_graph_tools, get_score_repository
from api.scores import (
    ScoreArtifactInvalid,
    ScoreRepository,
    load_scores,
)
from api.settings import REPOSITORY_ROOT, Settings

THRESHOLD = 0.5


def write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the decision threshold at an isolated config file."""
    config = tmp_path / "baseline_config.json"
    config.write_text(json.dumps({"thresholds": {"lgbm": THRESHOLD}}), encoding="utf-8")
    monkeypatch.setenv("TRACEAML_BASELINE_CONFIG_PATH", str(config))


def make_client(
    scores: dict[str, tuple[float, str]] | None = None,
    tools: FakeGraphTools | None = None,
) -> TestClient:
    """Build the app with fake score and graph dependencies."""
    app = create_app()
    app.dependency_overrides[get_score_repository] = lambda: FakeScoreRepository(scores)
    app.dependency_overrides[get_graph_tools] = lambda: tools or FakeGraphTools()
    return TestClient(app, raise_server_exceptions=False)


def investigation_payload(transaction_id: str = "tx_1", **overrides: object) -> dict[str, object]:
    """Default bounded investigation request with optional overrides."""
    payload: dict[str, object] = {
        "transaction_id": transaction_id,
        "max_hops": 2,
        "time_window_seconds": 3600,
        "result_limit": 10,
    }
    payload.update(overrides)
    return payload


def test_alerts_ranked_with_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alerts arrive ranked by risk with queue timestamps and decisions."""
    write_config(tmp_path, monkeypatch)
    scores = {
        "tx_low": (0.1, "2022-09-01 00:02"),
        "tx_high": (0.9, "2022-09-01 00:00"),
        "tx_tie_b": (0.5, "2022-09-01 00:03"),
        "tx_tie_a": (0.5, "2022-09-01 00:01"),
    }
    with make_client(scores) as client:
        response = client.get("/api/alerts?limit=10&offset=0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert [item["transaction_id"] for item in payload["items"]] == [
        "tx_high",
        "tx_tie_a",
        "tx_tie_b",
        "tx_low",
    ]
    assert [item["timestamp"] for item in payload["items"]] == [
        "2022-09-01 00:00",
        "2022-09-01 00:01",
        "2022-09-01 00:03",
        "2022-09-01 00:02",
    ]
    decisions = {item["transaction_id"]: item["decision"] for item in payload["items"]}
    assert decisions == {
        "tx_high": "review",
        "tx_tie_a": "review",
        "tx_tie_b": "review",
        "tx_low": "allow",
    }
    assert_no_labels(payload)


def test_alerts_paging_and_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Paging slices the ranked queue; out-of-range paging is a 422."""
    write_config(tmp_path, monkeypatch)
    with make_client() as client:
        first = client.get("/api/alerts?limit=1&offset=0").json()
        second = client.get("/api/alerts?limit=1&offset=1").json()
        assert client.get("/api/alerts?limit=500").status_code == 422
        assert client.get("/api/alerts?offset=-1").status_code == 422
    assert first["items"][0]["transaction_id"] == "tx_1"
    assert second["items"][0]["transaction_id"] == "tx_2"


def test_alerts_missing_artifact_is_explicit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing score artifact surfaces as 503 without raw exceptions."""
    write_config(tmp_path, monkeypatch)
    monkeypatch.setenv("TRACEAML_SCORE_ARTIFACT", str(tmp_path / "absent.parquet"))
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/alerts")
    assert response.status_code == 503
    assert response.json() == {"detail": "score artifact unavailable"}
    assert_no_labels(response.json())


def test_score_repository_ranking_and_cache(tmp_path: Path) -> None:
    """Real loader sorts by risk with id tie-breaks and caches per path."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    artifact = tmp_path / "scores.parquet"
    pq.write_table(
        pa.table(
            {
                "transaction_id": ["b", "a", "c"],
                "timestamp": ["t-b", "t-a", "t-c"],
                "score": [0.4, 0.9, 0.4],
            }
        ),
        artifact,
    )
    first = load_scores(str(artifact))
    assert load_scores(str(artifact)) is first
    assert [item[0] for item in first.ranked(10, 0)] == ["a", "b", "c"]
    assert first.ranked(1, 1)[0] == ("b", 0.4, "t-b")


def test_benchmarks_reads_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Benchmarks echo the offline metrics file with no label leakage."""
    metrics = tmp_path / "metrics.json"
    metrics.write_text(
        json.dumps(
            {
                "lgbm": {
                        "average_precision": {"test": 0.5},
                        "val_threshold_max_f1": 0.2,
                        "test_at_val_threshold": {
                            "precision": 0.3,
                            "recall": 0.4,
                            "f1": 0.34,
                            "true_positives": 3,
                            "false_positives": 7,
                        },
                        "test_at_alert_budget": {
                            "budget": 0.001,
                            "alerts": 10,
                            "true_positives": 3,
                            "precision": 0.3,
                            "recall": 0.05,
                        },
                        "splits": {
                            "test": {
                                "rows": 100,
                                "prevalence": 0.05,
                                "alerts": 10,
                                "precision": 0.3,
                                "recall": 0.6,
                            }
                        },
                    }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRACEAML_BASELINE_METRICS_PATH", str(metrics))
    monkeypatch.setenv(
        "TRACEAML_PERFORMANCE_PATH", str(REPOSITORY_ROOT / "docs/benchmarks/iteration-1-performance.json")
    )
    with make_client() as client:
        response = client.get("/api/benchmarks")
    assert response.status_code == 200
    assert response.json()["models"]["lgbm"]["test_at_val_threshold"]["true_positives"] == 3
    assert response.json()["performance"]["api"]["investigation"]["p95_ms"] == pytest.approx(
        3581.872193
    )
    assert_no_labels(response.json())


def test_benchmarks_merges_graph_ablation_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Graph metrics are added to the baseline response when a run exists."""
    baseline = tmp_path / "baseline-metrics.json"
    metrics = tmp_path / "graph-metrics.json"
    model_metrics = {
                    "average_precision": {"test": 0.6},
                    "val_threshold_max_f1": 0.2,
                    "test_at_val_threshold": {
                        "precision": 0.5,
                        "recall": 0.6,
                        "f1": 0.55,
                        "true_positives": 6,
                        "false_positives": 6,
                    },
                    "test_at_alert_budget": {
                        "budget": 0.001,
                        "alerts": 10,
                        "true_positives": 4,
                        "precision": 0.4,
                        "recall": 0.06,
                    },
                    "splits": {
                        "test": {
                            "rows": 100,
                            "prevalence": 0.05,
                            "alerts": 10,
                            "precision": 0.4,
                            "recall": 0.8,
                        }
                    },
                }
    baseline.write_text(json.dumps({"lgbm": model_metrics}), encoding="utf-8")
    graph_lgbm_metrics = {**model_metrics, "average_precision": {"test": 0.99}}
    metrics.write_text(
        json.dumps({"lgbm": graph_lgbm_metrics, "lgbm_graph": model_metrics}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRACEAML_BASELINE_METRICS_PATH", str(baseline))
    monkeypatch.setenv("TRACEAML_GRAPH_METRICS_PATH", str(metrics))
    with make_client() as client:
        response = client.get("/api/benchmarks")
    assert response.status_code == 200
    assert response.json()["models"]["lgbm"]["average_precision"]["test"] == 0.6
    assert response.json()["models"]["lgbm_graph"]["average_precision"]["test"] == 0.6
    assert_no_labels(response.json())


def test_investigation_uses_all_three_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trace, counterparties, and all three pattern searches are called."""
    write_config(tmp_path, monkeypatch)
    tools = FakeGraphTools()
    with make_client(tools=tools) as client:
        response = client.post("/api/investigations", json=investigation_payload())
    assert response.status_code == 200
    kinds = [kind for kind, _ in tools.calls]
    # Patterns start as soon as either stage-one tool returns the anchor keys,
    # so their start order relative to the slower stage-one tool is a race by
    # design. Assert the set of calls; result order is checked via durations.
    assert sorted(kinds) == [
        "inspect_counterparties",
        "pattern:fan_in",
        "pattern:fan_out",
        "pattern:pass_through",
        "trace_funds",
    ]
    durations = response.json()["evidence"]["tool_durations_ms"]
    assert [entry["tool"] for entry in durations] == [
        "trace_funds",
        "inspect_counterparties",
        "detect_flow_patterns.fan_out",
        "detect_flow_patterns.fan_in",
        "detect_flow_patterns.pass_through",
    ]
    assert [entry["query_duration_ms"] for entry in durations] == [1.0, 0.5, 0.25, 0.25, 0.25]
    for kind, params in tools.calls:
        assert params["result_limit"] == 10
        assert params["time_window_seconds"] == 3600
    assert_no_labels(response.json())


def test_investigation_merges_pattern_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fan matches land in the shared evidence sets and report."""
    write_config(tmp_path, monkeypatch)
    matches = {
        "fan_out": [
            {
                "counterparty_key": "3::c",
                "transfers": [
                    {
                        "transaction_id": "tx_fan",
                        "timestamp": "2022-09-01 00:05",
                        "amount_paid": 5.0,
                        "payment_currency": "US Dollar",
                        "amount_received": 5.0,
                        "receiving_currency": "US Dollar",
                    }
                ],
                "transfer_count": 1,
            }
        ]
    }
    with make_client(tools=FakeGraphTools(pattern_matches=matches)) as client:
        response = client.post("/api/investigations", json=investigation_payload())
    assert response.status_code == 200
    body = response.json()
    evidence_ids = {tx["transaction_id"] for tx in body["evidence"]["transactions"]}
    assert {"tx_1", "tx_fan"} <= evidence_ids
    account_keys = {acc["account_key"] for acc in body["evidence"]["accounts"]}
    assert {"1::a", "2::b", "3::c"} <= account_keys
    pattern_paths = [
        path for path in body["evidence"]["paths"]
        if path["direction"] == "pattern:fan_out"
    ]
    assert len(pattern_paths) == 1
    assert pattern_paths[0]["transaction_ids"] == ["tx_fan"]
    assert pattern_paths[0]["anchor_transaction_id"] == "tx_1"
    assert pattern_paths[0]["depth"] == 1
    assert pattern_paths[0]["account_keys"] == ["1::a", "3::c"]
    assert set(body["report"]["supporting_transaction_ids"]) == evidence_ids
    assert_no_labels(body)


def test_pass_through_path_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass-through findings anchor on the investigated tx with depth 2."""
    write_config(tmp_path, monkeypatch)
    hop = {
        "transaction_id": "tx_p1",
        "timestamp": "2022-09-01 00:06",
        "amount_paid": 7.0,
        "payment_currency": "US Dollar",
        "amount_received": 7.0,
        "receiving_currency": "US Dollar",
    }
    matches = {
        "pass_through": [
            {
                "account_keys": ["1::a", "2::b", "4::d"],
                "transaction_ids": ["tx_p1", "tx_p2"],
                "first_hop": hop,
                "second_hop": {**hop, "transaction_id": "tx_p2"},
            }
        ]
    }
    with make_client(tools=FakeGraphTools(pattern_matches=matches)) as client:
        response = client.post("/api/investigations", json=investigation_payload())
    assert response.status_code == 200
    body = response.json()
    pattern_paths = [
        path for path in body["evidence"]["paths"]
        if path["direction"] == "pattern:pass_through"
    ]
    assert len(pattern_paths) == 1
    assert pattern_paths[0]["anchor_transaction_id"] == "tx_1"
    assert pattern_paths[0]["depth"] == 2
    assert pattern_paths[0]["account_keys"] == ["1::a", "2::b", "4::d"]
    assert pattern_paths[0]["transaction_ids"] == ["tx_p1", "tx_p2"]
    evidence_ids = {tx["transaction_id"] for tx in body["evidence"]["transactions"]}
    assert {"tx_p1", "tx_p2"} <= evidence_ids
    assert_no_labels(body)


def test_investigation_allow_and_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk and decision derive from score vs the locked threshold."""
    write_config(tmp_path, monkeypatch)
    with make_client() as client:
        high = client.post("/api/investigations", json=investigation_payload("tx_1"))
        low = client.post(
            "/api/investigations",
            json=investigation_payload("tx_2", max_hops=1,
                                       time_window_seconds=60, result_limit=5),
        )
    assert high.status_code == 200
    body = high.json()
    assert body["risk_score"] == 0.9
    assert body["decision"] == "review"
    assert body["report"]["validation"] == "validated"
    assert body["report"]["supporting_transaction_ids"] == ["tx_1"]
    assert_no_labels(body)
    assert low.json()["decision"] == "allow"


def test_investigation_unknown_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unscored IDs are explicit 404s."""
    write_config(tmp_path, monkeypatch)
    with make_client() as client:
        response = client.post(
            "/api/investigations", json=investigation_payload("tx_missing")
        )
    assert response.status_code == 404
    assert_no_labels(response.json())


def test_investigation_bounds_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract bounds on hops/limit/window are enforced as 422s."""
    write_config(tmp_path, monkeypatch)
    bad = [
        investigation_payload(max_hops=5),
        investigation_payload(result_limit=500),
        investigation_payload(time_window_seconds=99_999_999),
    ]
    with make_client() as client:
        for payload in bad:
            assert client.post("/api/investigations", json=payload).status_code == 422


def test_trace_path_mismatch_is_explicit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Traversal paths citing unevidenced IDs fail as explicit 500s, no leak."""
    write_config(tmp_path, monkeypatch)
    evidence = {
        "transactions": [],
        "accounts": [],
        "paths": [
            {
                "anchor_transaction_id": "tx_1",
                "direction": "both",
                "depth": 1,
                "account_keys": ["9::ghost"],
                "transaction_ids": ["tx_ghost"],
            }
        ],
        "query_duration_ms": 1.0,
    }
    with make_client(tools=FakeGraphTools(evidence)) as client:
        response = client.post("/api/investigations", json=investigation_payload())
    assert response.status_code == 500
    assert response.json() == {"detail": "evidence validation failed"}
    assert_no_labels(response.json())


def test_malformed_pattern_match_is_explicit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pattern matches missing required keys fail without raw exceptions."""
    write_config(tmp_path, monkeypatch)
    matches = {"fan_out": [{"transfers": []}]}
    with make_client(tools=FakeGraphTools(pattern_matches=matches)) as client:
        response = client.post("/api/investigations", json=investigation_payload())
    assert response.status_code == 500
    assert response.json() == {"detail": "graph evidence unavailable"}
    assert_no_labels(response.json())


def test_score_artifact_with_labels_fails_closed(tmp_path: Path) -> None:
    """An artifact carrying label columns is rejected, never served."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    artifact = tmp_path / "scores.parquet"
    pq.write_table(
        pa.table(
            {
                "transaction_id": ["tx_1"],
                "timestamp": ["2022-09-01 00:00"],
                "score": [0.5],
                "target_is_laundering": [0],
            }
        ),
        artifact,
    )
    with pytest.raises(ScoreArtifactInvalid):
        ScoreRepository.load(artifact)


def test_schemas_reject_label_keys() -> None:
    """Response/request models refuse unknown (label) fields outright."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        schemas.AlertItem(
            transaction_id="tx_1",
            timestamp="2022-09-01 00:00",
            score=0.5,
            decision="allow",
            target_is_laundering=0,  # type: ignore[call-arg]
        )
    with pytest.raises(pydantic.ValidationError):
        schemas.InvestigationRequest(
            transaction_id="tx_1",
            max_hops=2,
            time_window_seconds=60,
            result_limit=5,
            is_illicit=1,  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    "snapshot",
    ["iteration-1-performance.json", "iteration-2-performance.json"],
)
def test_versioned_performance_snapshots_match_contract(snapshot: str) -> None:
    """Every committed snapshot validates against the served schema."""
    path = REPOSITORY_ROOT / "docs" / "benchmarks" / snapshot
    summary = schemas.PerformanceSummary.model_validate_json(path.read_text(encoding="utf-8"))
    assert summary.cost.external_llm_calls == 0
    assert "Synthetic" in summary.dataset_label


def test_default_performance_snapshot_exists() -> None:
    """The default TRACEAML_PERFORMANCE_PATH points at a committed snapshot."""
    assert (REPOSITORY_ROOT / Settings.model_fields["performance_path"].default).is_file()
