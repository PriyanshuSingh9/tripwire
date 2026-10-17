"""Focused loader tests at the idempotent-ingest seam (fake graph client).

No Docker, no dataset: Parquet fixtures here are tiny synthetic tables, and
the fake graph emulates MERGE idempotency in memory.
"""

from __future__ import annotations

import calendar
import json
import os
import time
from datetime import datetime, timedelta

import pytest

from graph import load_graph


class FakeResult:
    """Minimal stand-in for a FalkorDB query result."""

    def __init__(self, result_set: list[list[object]]):
        self.result_set = result_set


class FakeGraph:
    """In-memory graph emulating MERGE (setdefault) idempotency."""

    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, object]] = {}
        self.transactions: dict[str, dict[str, object]] = {}
        self.sent: set[tuple[str, str]] = set()
        self.received: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, dict[str, object] | None, int | None]] = []
        self.schema_log: list[str] = []

    def query(self, cypher: str, params: dict[str, object] | None = None, timeout: int | None = None):
        self.calls.append((cypher, params, timeout))
        if params is not None and "accounts" in params:
            for acc in params["accounts"]:
                assert isinstance(acc, dict)
                entry = self.accounts.setdefault(
                    acc["key"], {"bank": acc["bank"], "account_id": acc["account"],
                                 "last_epoch": acc["last_epoch"]},
                )
                # ON MATCH: last_epoch only increases.
                entry["last_epoch"] = max(entry["last_epoch"], acc["last_epoch"])
            return FakeResult([])
        if params is not None and "rows" in params:
            rows = params["rows"]
            assert isinstance(rows, list)
            for row in rows:
                assert isinstance(row, dict)
                # MATCH semantics: accounts must already exist.
                assert row["sender_key"] in self.accounts
                assert row["receiver_key"] in self.accounts
                # MERGE + ON CREATE SET: first write wins, re-runs change nothing.
                self.transactions.setdefault(row["transaction_id"], dict(row))
                self.sent.add((row["sender_key"], row["transaction_id"]))
                self.received.add((row["transaction_id"], row["receiver_key"]))
            return FakeResult([])
        if ":SENT" in cypher:
            return FakeResult([[len(self.sent)]])
        if ":RECEIVED" in cypher:
            return FakeResult([[len(self.received)]])
        if "(t:Transaction)" in cypher:
            return FakeResult([[len(self.transactions)]])
        if "(a:Account)" in cypher:
            return FakeResult([[len(self.accounts)]])
        raise AssertionError(f"unexpected cypher: {cypher[:80]}")

    def create_node_range_index(self, label: str, prop: str) -> None:
        self.schema_log.append(f"index:{label}({prop})")

    def create_node_unique_constraint(self, label: str, prop: str) -> None:
        self.schema_log.append(f"unique:{label}({prop})")


def synthetic_record(n: int, **overrides: object) -> dict[str, object]:
    """Build one projected record with valid composite keys."""
    record = {
        "transaction_id": f"hi8-tx-{n:08d}",
        "timestamp": datetime(2022, 9, 1, 0, n % 60),
        "sender_bank": "BANKA",
        "sender_account": f"{1000 + n:08d}",
        "receiver_bank": "BANKB",
        "receiver_account": f"{2000 + n:08d}",
        "sender_account_key": f"BANKA::{1000 + n:08d}",
        "receiver_account_key": f"BANKB::{2000 + n:08d}",
        "amount_paid": 100.0 + n,
        "payment_currency": "US Dollar",
        "amount_received": 99.0 + n,
        "receiving_currency": "US Dollar",
        "payment_format": "Wire",
        "source_row_number": n,
    }
    record.update(overrides)
    return record


def write_parquet(path, records: list[dict[str, object]]) -> None:
    """Write a tiny synthetic Parquet table (test fixture only, not dataset)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    columns: dict[str, list[object]] = {name: [] for name in load_graph.LOAD_COLUMNS}
    for record in records:
        for name in load_graph.LOAD_COLUMNS:
            columns[name].append(record[name])
    pq.write_table(pa.Table.from_pydict(columns), path)


def test_projection_excludes_offline_columns() -> None:
    for column in ("target_is_laundering", "split"):
        assert column not in load_graph.LOAD_COLUMNS
        assert column not in load_graph.TRANSACTION_PROPERTIES
    for column in ("target_is_laundering", "split", "source_row_number"):
        assert column not in load_graph.TRANSACTION_PROPERTIES
        assert column not in load_graph.ACCOUNT_PROPERTIES
    for text in (
        load_graph.BATCH_CYPHER,
        load_graph.ACCOUNT_UPSERT_CYPHER,
        load_graph.ACCOUNT_CREATE_CYPHER,
        load_graph.TRANSACTION_MERGE_CYPHER,
        load_graph.TRANSACTION_CREATE_CYPHER,
        " ".join(load_graph.LOAD_COLUMNS),
    ):
        lowered = text.lower()
        assert "target_is_laundering" not in lowered
        assert "is laundering" not in lowered
        assert "typology" not in lowered


def test_batch_cypher_is_parameterized_idempotent_merge() -> None:
    cypher = load_graph.BATCH_CYPHER
    assert "UNWIND $rows AS r" in cypher
    assert "MERGE" in cypher
    # CREATE appears only as ON CREATE SET (no bare CREATE of nodes/edges).
    assert cypher.count("CREATE") == cypher.count("ON CREATE SET")
    assert "SENT" in cypher and "RECEIVED" in cypher


def test_validate_account_key_rejects_bad_keys() -> None:
    load_graph.validate_account_key(
        bank="B1", account="000123", key="B1::000123", source_row_number=7
    )
    with pytest.raises(ValueError, match="source row 7"):
        load_graph.validate_account_key(
            bank="", account="000123", key="::000123", source_row_number=7
        )
    with pytest.raises(ValueError, match="source row 8"):
        load_graph.validate_account_key(
            bank="B::1", account="2", key="B::1::2", source_row_number=8
        )
    with pytest.raises(ValueError, match="source row 9"):
        load_graph.validate_account_key(
            bank="B1", account="000123", key="WRONG::000123", source_row_number=9
        )


def test_row_to_params_preserves_strings_and_epoch() -> None:
    params = load_graph.row_to_params(synthetic_record(42))
    assert params["transaction_id"] == "hi8-tx-00000042"
    assert params["sender_key"] == "BANKA::00001042"
    assert params["sender_account"] == "00001042"
    assert isinstance(params["timestamp_epoch"], int)
    assert params["payment_currency"] == "US Dollar"
    assert set(params) == {
        "transaction_id", "timestamp", "timestamp_epoch",
        "sender_key", "receiver_key", "sender_bank", "sender_account",
        "receiver_bank", "receiver_account", "amount_paid", "payment_currency",
        "amount_received", "receiving_currency", "payment_format",
        "source_row_number",
    }


def test_duplicate_source_rows_become_distinct_transactions() -> None:
    graph = FakeGraph()
    base = synthetic_record(1)
    twin = synthetic_record(2)
    for field in (
        "sender_bank", "sender_account", "receiver_bank", "receiver_account",
        "sender_account_key", "receiver_account_key",
        "amount_paid", "payment_currency", "amount_received", "receiving_currency",
    ):
        twin[field] = base[field]
    batch = [load_graph.row_to_params(base), load_graph.row_to_params(twin)]
    load_graph.load_batch(graph, batch, mode="merge", timeout_ms=1000)
    assert len(graph.transactions) == 2
    assert len(graph.accounts) == 2
    assert len(graph.sent) == 2 and len(graph.received) == 2


def test_timestamp_epoch_is_utc_independent_of_host_timezone(monkeypatch) -> None:
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is unavailable on this platform")
    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    time.tzset()
    try:
        if time.timezone == 0:
            pytest.skip("host tzdata cannot represent a non-UTC zone")
        params = load_graph.row_to_params(synthetic_record(1))
    finally:
        if previous_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", previous_tz)
        time.tzset()
    assert params["timestamp_epoch"] == calendar.timegm(
        datetime(2022, 9, 1, 0, 1).timetuple()
    )


def test_double_load_is_idempotent() -> None:
    graph = FakeGraph()
    batch = [load_graph.row_to_params(synthetic_record(n)) for n in (1, 2, 3)]
    for _ in range(2):
        load_graph.load_batch(graph, batch, mode="merge", timeout_ms=1000)
    assert len(graph.transactions) == 3
    assert len(graph.sent) == 3 and len(graph.received) == 3
    counts = load_graph.reconcile_counts(graph, rows_attempted=3, timeout_ms=1000)
    assert counts["transactions"] == 3


def test_on_create_semantics_keep_first_write() -> None:
    graph = FakeGraph()
    original = load_graph.row_to_params(synthetic_record(1))
    load_graph.load_batch(graph, [original], mode="merge", timeout_ms=1000)
    altered = dict(original, amount_paid=999999.0)
    load_graph.load_batch(graph, [altered], mode="merge", timeout_ms=1000)
    assert graph.transactions[original["transaction_id"]]["amount_paid"] == original["amount_paid"]


def test_schema_creates_indexes_before_constraints() -> None:
    graph = FakeGraph()
    status = load_graph.ensure_schema(graph)
    kinds = [entry.split(":")[0] for entry in graph.schema_log]
    assert kinds == ["index"] * 2 + ["unique"] * 2
    assert set(status) == set(graph.schema_log)


def test_schema_reports_already_indexed_as_existing() -> None:
    class ExistingIndexGraph(FakeGraph):
        def create_node_range_index(self, label: str, prop: str) -> None:
            raise RuntimeError("Attribute is already indexed")

    status = load_graph.ensure_schema(ExistingIndexGraph())
    assert status["index:Account(account_key)"] == "exists"
    assert status["unique:Transaction(transaction_id)"] == "created"


def test_reconcile_counts_fails_on_mismatch() -> None:
    graph = FakeGraph()
    batch = [load_graph.row_to_params(synthetic_record(1))]
    load_graph.load_batch(graph, batch, mode="merge", timeout_ms=1000)
    with pytest.raises(ValueError, match="reconciliation failed"):
        load_graph.reconcile_counts(graph, rows_attempted=2, timeout_ms=1000)


def test_limit_loads_file_order_prefix(tmp_path) -> None:
    parquet = tmp_path / "tiny.parquet"
    write_parquet(parquet, [synthetic_record(n) for n in range(1, 6)])
    batches = list(load_graph.iter_param_batches(parquet, batch_size=10, limit=2))
    assert len(batches) == 1
    assert [row["transaction_id"] for row in batches[0]] == ["hi8-tx-00000001", "hi8-tx-00000002"]


def test_main_dry_run_reports_aggregates(tmp_path, capsys) -> None:
    parquet = tmp_path / "tiny.parquet"
    write_parquet(parquet, [synthetic_record(n) for n in range(1, 4)])
    assert load_graph.main(["--parquet", str(parquet), "--dry-run"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["rows"] == 3
    assert summary["batches"] == 1
    assert summary["dry_run"] is True
    assert summary["elapsed_seconds"] >= 0.0
    assert summary["peak_rss_mb"] > 0.0


def test_main_missing_file_returns_usage_error(capsys) -> None:
    assert load_graph.main(["--parquet", "does-not-exist.parquet"]) == 2
    assert "error" in json.loads(capsys.readouterr().out)


def test_main_loads_and_reconciles_via_fake(tmp_path, monkeypatch, capsys) -> None:
    parquet = tmp_path / "tiny.parquet"
    write_parquet(parquet, [synthetic_record(n) for n in range(1, 4)])
    graph = FakeGraph()
    monkeypatch.setattr(load_graph, "connect", lambda *args: graph)
    argv = ["--parquet", str(parquet), "--batch-size", "2"]
    assert load_graph.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["rows"] == 3 and first["batches"] == 2
    assert first["reconciliation"]["transactions"] == 3
    # Idempotent re-run against the same graph still reconciles.
    assert load_graph.main(argv) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["reconciliation"]["transactions"] == 3
    assert len(graph.transactions) == 3


def test_stored_properties_match_allowlists() -> None:
    import re

    created = set(re.findall(r"^\s+(\w+): r\.", load_graph.TRANSACTION_CREATE_CYPHER, re.MULTILINE))
    merged = set(re.findall(r"t\.(\w+) = r\.", load_graph.TRANSACTION_MERGE_CYPHER))
    assert created == set(load_graph.TRANSACTION_PROPERTIES)
    assert merged | {"transaction_id"} == set(load_graph.TRANSACTION_PROPERTIES)
    account_props = set(re.findall(r"(\w+): acc\.", load_graph.ACCOUNT_CREATE_CYPHER))
    assert account_props == set(load_graph.ACCOUNT_PROPERTIES)


def test_split_batch_dedupes_accounts_with_max_epoch() -> None:
    first = load_graph.row_to_params(synthetic_record(1))
    later = load_graph.row_to_params(
        synthetic_record(30, sender_account="00001001", sender_account_key="BANKA::00001001")
    )
    accounts, rows = load_graph.split_batch([first, later])
    by_key = {a["key"]: a for a in accounts}
    assert len(rows) == 2
    assert len(by_key) == 3
    assert by_key[first["sender_key"]]["last_epoch"] == max(
        first["timestamp_epoch"], later["timestamp_epoch"]
    )
    assert "source_row_number" not in rows[0]


def test_last_epoch_only_increases_across_batches() -> None:
    graph = FakeGraph()
    late = load_graph.row_to_params(synthetic_record(30))
    early = dict(load_graph.row_to_params(synthetic_record(1)),
                 sender_key=late["sender_key"], sender_bank=late["sender_bank"],
                 sender_account=late["sender_account"])
    load_graph.load_batch(graph, [late], mode="merge", timeout_ms=1000)
    load_graph.load_batch(graph, [early], mode="merge", timeout_ms=1000)
    assert graph.accounts[late["sender_key"]]["last_epoch"] == late["timestamp_epoch"]


def test_aggregate_accounts_matches_rowwise_max(tmp_path) -> None:
    parquet = tmp_path / "tiny.parquet"
    records = [synthetic_record(n) for n in range(1, 6)]
    records[4]["sender_bank"] = records[0]["sender_bank"]
    records[4]["sender_account"] = records[0]["sender_account"]
    records[4]["sender_account_key"] = records[0]["sender_account_key"]
    write_parquet(parquet, records)
    accounts = load_graph.aggregate_accounts(parquet, limit=None, window=None)
    expected: dict[str, int] = {}
    for record in records:
        params = load_graph.row_to_params(record)
        for key in (params["sender_key"], params["receiver_key"]):
            expected[key] = max(expected.get(key, 0), params["timestamp_epoch"])
    assert {a["key"]: a["last_epoch"] for a in accounts} == expected
    limited = load_graph.aggregate_accounts(parquet, limit=2, window=None)
    assert len(limited) == 4


def test_window_keeps_inclusive_range_around_anchor(tmp_path) -> None:
    parquet = tmp_path / "tiny.parquet"
    write_parquet(parquet, [synthetic_record(n) for n in range(1, 11)])
    center = load_graph.find_anchor_timestamp(parquet, "hi8-tx-00000005")
    window = (center - timedelta(seconds=120), center + timedelta(seconds=120))
    batches = list(load_graph.iter_param_batches(parquet, batch_size=4, limit=None, window=window))
    ids = [row["transaction_id"] for batch in batches for row in batch]
    assert ids == [f"hi8-tx-{n:08d}" for n in range(3, 8)]
    with pytest.raises(ValueError, match="not found"):
        load_graph.find_anchor_timestamp(parquet, "hi8-tx-99999999")


def test_create_mode_loads_and_refuses_non_empty_graph(tmp_path, monkeypatch, capsys) -> None:
    parquet = tmp_path / "tiny.parquet"
    write_parquet(parquet, [synthetic_record(n) for n in range(1, 4)])
    graph = FakeGraph()
    monkeypatch.setattr(load_graph, "connect", lambda *args: graph)
    argv = ["--parquet", str(parquet), "--mode", "create"]
    assert load_graph.main(argv) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["reconciliation"]["transactions"] == 3
    assert summary["account_phase"]["accounts"] == 6
    assert load_graph.main(argv) == 1
    assert "requires an empty graph" in json.loads(capsys.readouterr().out)["error"]
