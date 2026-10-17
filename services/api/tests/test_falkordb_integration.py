"""End-to-end investigation against a tiny real FalkorDB graph.

The graph is written with the production loader's own Cypher and row
conversion (``graph.load_graph``), so any drift between stored property names
and the properties the investigation queries read fails here instead of
silently returning nulls. Skips when FalkorDB is not reachable. Uses only the
``agentapi-test`` graph and drops it afterwards.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import FakeScoreRepository, assert_no_labels

from api.app import create_app, get_score_repository

GRAPH_NAME = "agentapi-test"
HOST = os.environ.get("TRACEAML_TEST_FALKORDB_HOST", "localhost")
PORT = int(os.environ.get("TRACEAML_TEST_FALKORDB_PORT", "6379"))
BASE_TIME = datetime(2022, 9, 1, 8, 0)

# (transaction_id, sender, receiver, minutes after BASE_TIME, amount)
# A fans out to B, C, D; B fans in from A, F, G; B passes funds on to E.
TRANSFERS: tuple[tuple[str, str, str, int, float], ...] = (
    ("agt-1", "A", "B", 0, 1000.0),
    ("agt-2", "A", "C", 5, 900.0),
    ("agt-3", "A", "D", 10, 800.0),
    ("agt-4", "F", "B", 15, 700.0),
    ("agt-5", "G", "B", 20, 600.0),
    ("agt-6", "B", "E", 30, 950.0),
)


def record(
    transaction_id: str, sender: str, receiver: str, minutes: int, amount: float, row: int
) -> dict[str, Any]:
    return {
        "transaction_id": transaction_id,
        "timestamp": BASE_TIME + timedelta(minutes=minutes),
        "sender_bank": "10",
        "sender_account": sender,
        "receiver_bank": "20" if receiver != "B" else "10",
        "receiver_account": receiver,
        "sender_account_key": f"10::{sender}",
        "receiver_account_key": f"{'20' if receiver != 'B' else '10'}::{receiver}",
        "amount_paid": amount,
        "payment_currency": "US Dollar",
        "amount_received": amount * 0.9,
        "receiving_currency": "Euro",
        "payment_format": "Wire",
        "source_row_number": row,
    }


@pytest.fixture(scope="module")
def tiny_graph() -> Iterator[None]:
    try:
        from falkordb import FalkorDB

        client = FalkorDB(host=HOST, port=PORT, socket_connect_timeout=1.0, socket_timeout=5.0)
        client.execute_command("PING")
    except Exception:  # noqa: BLE001 -- any connection failure means skip
        pytest.skip("FalkorDB is not reachable")
    from graph.load_graph import BATCH_CYPHER, ensure_schema, row_to_params

    graph = client.select_graph(GRAPH_NAME)
    try:
        graph.delete()
    except Exception:  # noqa: BLE001, S110 -- absent graph is fine
        pass
    ensure_schema(graph)
    rows = [
        row_to_params(record(*transfer, row=index))
        for index, transfer in enumerate(TRANSFERS)
    ]
    graph.query(BATCH_CYPHER, params={"rows": rows})
    try:
        yield
    finally:
        try:
            graph.delete()
        finally:
            client.close()


def test_investigation_against_real_graph(
    tiny_graph: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "baseline_config.json"
    config.write_text(json.dumps({"thresholds": {"lgbm": 0.5}}), encoding="utf-8")
    monkeypatch.setenv("TRACEAML_BASELINE_CONFIG_PATH", str(config))
    monkeypatch.setenv("TRACEAML_FALKORDB_HOST", HOST)
    monkeypatch.setenv("TRACEAML_FALKORDB_PORT", str(PORT))
    monkeypatch.setenv("TRACEAML_GRAPH_NAME", GRAPH_NAME)
    scores = {"agt-1": (0.8, "2022-09-01 08:00"), "agt-missing": (0.7, "2022-09-01 09:00")}
    app = create_app()
    app.dependency_overrides[get_score_repository] = lambda: FakeScoreRepository(scores)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/investigations",
            json={"transaction_id": "agt-1", "max_hops": 2,
                  "time_window_seconds": 86_400, "result_limit": 50},
        )
        missing = client.post(
            "/api/investigations", json={"transaction_id": "agt-missing"}
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert_no_labels(body)

    evidence = {tx["transaction_id"]: tx for tx in body["evidence"]["transactions"]}
    assert set(evidence) == {tx[0] for tx in TRANSFERS}
    anchor = evidence["agt-1"]
    # Every stored property must come back populated under its real name.
    assert anchor == {
        "transaction_id": "agt-1",
        "timestamp": "2022-09-01 08:00",
        "source_account_key": "10::A",
        "destination_account_key": "10::B",
        "amount_paid": 1000.0,
        "payment_currency": "US Dollar",
        "amount_received": 900.0,
        "receiving_currency": "Euro",
        "payment_format": "Wire",
    }
    for tx in evidence.values():
        assert tx["amount_paid"] is not None
        assert tx["payment_currency"] == "US Dollar"
        assert tx["amount_received"] is not None
        assert tx["receiving_currency"] == "Euro"

    directions = {path["direction"] for path in body["evidence"]["paths"]}
    assert {"both", "pattern:fan_in", "pattern:pass_through"} <= directions
    pass_through = [
        (path["account_keys"], path["transaction_ids"])
        for path in body["evidence"]["paths"]
        if path["direction"] == "pattern:pass_through"
    ]
    assert (["10::A", "10::B", "20::E"], ["agt-1", "agt-6"]) in pass_through
    fan_in = [
        path["account_keys"][0]
        for path in body["evidence"]["paths"]
        if path["direction"] == "pattern:fan_in"
    ]
    assert sorted(fan_in) == ["10::A", "10::F", "10::G"]

    tools = [entry["tool"] for entry in body["evidence"]["tool_durations_ms"]]
    assert len(tools) == 5
    assert body["evidence"]["query_duration_ms"] > 0
    assert body["report"]["validation"] == "validated"

    # Scored but absent from the graph: explicit 404, not a 500.
    assert missing.status_code == 404
    assert missing.json() == {"detail": "transaction not found in graph"}


def test_fan_out_window_ends_at_latest_activity(tiny_graph: None) -> None:
    from falkordb import FalkorDB
    from graph.queries import detect_flow_patterns

    client = FalkorDB(host=HOST, port=PORT)
    try:
        found = detect_flow_patterns(
            client.select_graph(GRAPH_NAME), account_key="10::A", pattern="fan_out"
        )
    finally:
        client.close()
    assert found["distinct_counterparties"] == 3
