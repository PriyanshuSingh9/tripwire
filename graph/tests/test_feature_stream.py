"""Focused feature-stream tests at the point-in-time seam (fake graph).

No Docker, no dataset: minute-group fixtures are tiny synthetic row lists,
and the fake feature graph emulates FLOW-edge state with the same
prior-only visibility rules as the real queries.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from graph import feature_stream

EPOCH_BASE = 1_661_990_400  # 2022-09-01 00:00 UTC, minute-aligned


def row(txid: str, sender: str, receiver: str, *, minute_offset: int = 0) -> dict[str, object]:
    """Build one normalized streaming row (test fixture only, not dataset)."""
    return {
        "transaction_id": txid,
        "epoch": EPOCH_BASE + minute_offset * 60,
        "sender_key": sender,
        "receiver_key": receiver,
    }


class FakeResult:
    """Minimal stand-in for a FalkorDB query result."""

    def __init__(self, result_set: list[list[object]]):
        self.result_set = result_set


class FakeFeatureGraph:
    """In-memory FLOW-edge store emulating prior-only visibility."""

    def __init__(self) -> None:
        self.name = "feature-test"
        self.accounts: set[str] = set()
        self.edges: dict[tuple[str, str], int] = {}
        self.calls: list[tuple[str, dict[str, object] | None, int | None]] = []
        self.deleted = False
        self.indexes: list[tuple[str, str]] = []

    def delete(self) -> None:
        self.accounts.clear()
        self.edges.clear()
        self.deleted = True

    def create_node_range_index(self, label: str, prop: str) -> None:
        self.indexes.append((label, prop))

    def query(self, cypher, params=None, timeout=None):
        self.calls.append((cypher, params, timeout))
        assert params is not None
        if "UNWIND $keys" in cypher:
            rows = [self._neighborhood(key, params, cypher) for key in params["keys"]]
            return FakeResult([row for row in rows if row is not None])
        if "UNWIND $pairs" in cypher and "IS NOT NULL" in cypher:
            return FakeResult([self._reverse(pair, params) for pair in params["pairs"]])
        if "UNWIND $pairs" in cypher:
            for pair in params["pairs"]:
                self.accounts.add(pair["sender_key"])
                self.accounts.add(pair["receiver_key"])
                self.edges[(pair["sender_key"], pair["receiver_key"])] = pair["epoch"]
            return FakeResult([])
        raise AssertionError(f"unexpected cypher: {cypher[:80]}")

    def _eligible(self, last_epoch: int, params) -> bool:
        return params["min_epoch"] <= last_epoch < params["epoch"]

    def _neighborhood(self, key: str, params, cypher: str) -> list[object] | None:
        if key not in self.accounts:
            return None
        out_keys = sorted(
            dst for (src, dst), last in self.edges.items()
            if src == key and self._eligible(last, params)
        )
        in_keys = sorted(
            src for (src, dst), last in self.edges.items()
            if dst == key and self._eligible(last, params)
        )
        if cypher == feature_stream.OUT_NEIGHBORHOOD_Q:
            return [key, len(out_keys)]
        if cypher == feature_stream.IN_NEIGHBORHOOD_Q:
            return [key, len(in_keys)]
        raise AssertionError("unexpected neighborhood query")

    def _reverse(self, pair, params) -> list[object]:
        last = self.edges.get((pair["receiver_key"], pair["sender_key"]))
        return [pair["sender_key"], pair["receiver_key"], last is not None and last < params["epoch"]]


def featurize(rows: list[dict[str, object]], graph=None, window=86_400):
    """Run minute groups through featurize_minute on a fake graph."""
    graph = graph if graph is not None else FakeFeatureGraph()
    grouper = feature_stream.MinuteGrouper()
    out = []
    for minute, group in grouper.group(rows):
        out.extend(
            feature_stream.featurize_minute(
                graph, group, epoch=minute, window_seconds=window, timeout_ms=1000
            )
        )
    return graph, out


def test_same_minute_peers_are_invisible() -> None:
    graph, features = featurize(
        [row("t1", "A::1", "B::2", minute_offset=0),
         row("t2", "B::2", "C::3", minute_offset=0)]
    )
    by_id = {f["transaction_id"]: f for f in features}
    assert by_id["t2"]["gfeat_sender_in_24h"] == 0
    assert by_id["t2"]["gfeat_sender_out_24h"] == 0
    assert by_id["t2"]["gfeat_prior_reverse_flow"] == 0
    # The minute applied only after every row was featurized.
    assert graph.edges == {("A::1", "B::2"): EPOCH_BASE, ("B::2", "C::3"): EPOCH_BASE}


def test_previous_minute_contributes() -> None:
    _, features = featurize(
        [row("t1", "A::1", "B::2", minute_offset=0),
         row("t2", "B::2", "C::3", minute_offset=1)]
    )
    by_id = {f["transaction_id"]: f for f in features}
    assert by_id["t1"]["gfeat_sender_out_24h"] == 0
    assert by_id["t2"]["gfeat_sender_in_24h"] == 1
    assert by_id["t2"]["gfeat_receiver_in_24h"] == 0
    assert by_id["t2"]["gfeat_prior_reverse_flow"] == 0


def test_prior_reverse_flow_and_degree_features() -> None:
    _, features = featurize(
        [row("t1", "A::1", "B::2", minute_offset=0),
         row("t2", "B::2", "X::9", minute_offset=0),
         row("t3", "A::1", "Y::8", minute_offset=1),
         row("t4", "B::2", "A::1", minute_offset=2)]
    )
    by_id = {f["transaction_id"]: f for f in features}
    assert by_id["t3"]["gfeat_sender_out_24h"] == 1
    assert by_id["t3"]["gfeat_receiver_out_24h"] == 0
    assert by_id["t4"]["gfeat_prior_reverse_flow"] == 1
    assert by_id["t4"]["gfeat_sender_in_24h"] == 1


def test_window_expiry_removes_stale_edges_but_keeps_reverse() -> None:
    _, features = featurize(
        [row("t1", "A::1", "B::2", minute_offset=0),
         row("t2", "B::2", "C::3", minute_offset=180)],
        window=60,
    )
    by_id = {f["transaction_id"]: f for f in features}
    # The m0 edge is outside the 60 s window at m180.
    assert by_id["t2"]["gfeat_sender_in_24h"] == 0
    # Reverse flow is all-time prior, unaffected by the window.
    _, features = featurize(
        [row("t1", "A::1", "B::2", minute_offset=0),
         row("t2", "B::2", "A::1", minute_offset=180)],
        window=60,
    )
    assert features[1]["gfeat_prior_reverse_flow"] == 1


def test_reset_gives_identical_rebuild() -> None:
    rows = [row("t1", "A::1", "B::2", minute_offset=0),
            row("t2", "B::2", "C::3", minute_offset=1)]
    graph = FakeFeatureGraph()
    _, first = featurize(rows, graph=graph)
    feature_stream.reset_feature_graph(graph)
    assert graph.edges == {}
    _, second = featurize(rows, graph=graph)
    assert first == second


def test_reset_skips_missing_graph() -> None:
    class FakeClient:
        def list_graphs(self) -> list[str]:
            return []

    graph = FakeFeatureGraph()
    graph.client = FakeClient()
    feature_stream.reset_feature_graph(graph)
    assert not graph.deleted


def test_reset_deletes_existing_graph() -> None:
    class FakeClient:
        def list_graphs(self) -> list[str]:
            return ["feature-test"]

    graph = FakeFeatureGraph()
    graph.client = FakeClient()
    feature_stream.reset_feature_graph(graph)
    assert graph.deleted


def test_feature_schema_indexes_account_keys() -> None:
    graph = FakeFeatureGraph()
    assert feature_stream.ensure_feature_schema(graph) == "created"
    assert graph.indexes == [("FAccount", "account_key")]


def test_projection_is_label_free() -> None:
    assert feature_stream.FEATURE_COLUMNS == (
        "transaction_id", "timestamp", "sender_account_key", "receiver_account_key",
    )
    assert set(feature_stream.FEATURE_COLUMNS).isdisjoint(feature_stream.EXCLUDED_COLUMNS)
    assert "target_is_laundering" in feature_stream.EXCLUDED_COLUMNS
    assert "split" in feature_stream.EXCLUDED_COLUMNS
    for name in feature_stream.OUTPUT_SCHEMA_NAMES:
        assert "target" not in name and "split" not in name
    for query in (
        feature_stream.OUT_NEIGHBORHOOD_Q,
        feature_stream.IN_NEIGHBORHOOD_Q,
        feature_stream.REVERSE_Q,
        feature_stream.APPLY_Q,
    ):
        lowered = query.lower()
        assert "target_is_laundering" not in lowered
        assert "split" not in lowered


def test_grouper_yields_immediately_with_one_group_lookahead() -> None:
    grouper = feature_stream.MinuteGrouper()
    pulled: list[int] = []

    def records():
        for minute in (0, 0, 1, 1, 2):
            pulled.append(minute)
            yield {"epoch": EPOCH_BASE + minute * 60, "n": minute}

    stream = grouper.group(records())
    first_minute, first_rows = next(stream)
    assert first_minute == EPOCH_BASE
    assert len(first_rows) == 2
    # Only the first group plus one lookahead row were consumed.
    assert pulled == [0, 0, 1]
    rest = list(stream)
    assert [minute for minute, _ in rest] == [EPOCH_BASE + 60, EPOCH_BASE + 120]
    assert grouper.groups_yielded == 3
    assert grouper.rows_seen == 5
    assert grouper.dropped == 0


def test_grouper_drops_only_final_group_in_smoke() -> None:
    grouper = feature_stream.MinuteGrouper(drop_trailing_partial=True)
    records = [
        {"epoch": EPOCH_BASE + minute * 60, "n": i}
        for i, minute in enumerate((0, 0, 1, 2, 2, 2))
    ]
    groups = list(grouper.group(records))
    assert [minute for minute, _ in groups] == [EPOCH_BASE, EPOCH_BASE + 60]
    assert grouper.dropped == 3
    assert grouper.rows_seen == 6


def test_grouper_rejects_decreasing_minutes() -> None:
    grouper = feature_stream.MinuteGrouper()
    records = [{"epoch": EPOCH_BASE + 60}, {"epoch": EPOCH_BASE}]
    with pytest.raises(ValueError, match="monotonicity"):
        list(grouper.group(records))


def write_input_parquet(path, entries: list[tuple[str, datetime, str, str]]) -> None:
    """Write a tiny unsorted 4-column input table (test fixture only)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(
        [
            {"transaction_id": tx, "timestamp": ts,
             "sender_account_key": s, "receiver_account_key": r}
            for tx, ts, s, r in entries
        ]
    )
    pq.write_table(table, path)


def test_end_to_end_reconciliation_and_manifest(tmp_path, monkeypatch, capsys) -> None:
    parquet = tmp_path / "in.parquet"
    write_input_parquet(parquet, [
        ("hi8-tx-00000003", datetime(2022, 9, 1, 0, 2), "B::2", "C::3"),
        ("hi8-tx-00000001", datetime(2022, 9, 1, 0, 1), "A::1", "B::2"),
        ("hi8-tx-00000002", datetime(2022, 9, 1, 0, 1), "A::1", "C::3"),
    ])
    features_out = tmp_path / "feat.parquet"
    manifest_out = tmp_path / "manifest.json"
    graph = FakeFeatureGraph()
    monkeypatch.setattr(feature_stream, "connect", lambda *args: graph)
    code = feature_stream.main([
        "--parquet", str(parquet),
        "--features-out", str(features_out),
        "--manifest-out", str(manifest_out),
        "--spill-dir", str(tmp_path / "spill"),
        "--window-seconds", "86400",
    ])
    assert code == 0

    import pyarrow.parquet as pq

    table = pq.read_table(features_out)
    assert table.column_names == list(feature_stream.OUTPUT_SCHEMA_NAMES)
    assert table.num_rows == 3
    assert sorted(table.column("transaction_id").to_pylist()) == [
        "hi8-tx-00000001", "hi8-tx-00000002", "hi8-tx-00000003",
    ]
    # Same-minute pair shares prior-only state; the later minute sees both.
    rows = {r["transaction_id"]: r for r in table.to_pylist()}
    assert rows["hi8-tx-00000002"]["gfeat_sender_out_24h"] == 0
    assert rows["hi8-tx-00000003"]["gfeat_sender_in_24h"] == 1

    manifest = json.loads(manifest_out.read_text(encoding="utf-8"))
    assert manifest["output"]["rows_written"] == 3
    assert manifest["feature_account_index"] == "created"
    assert manifest["output"]["distinct_minutes"] == 2
    assert manifest["columns_read"] == list(feature_stream.FEATURE_COLUMNS)
    assert manifest["columns_excluded_proof"]["never_selected"] == ["target_is_laundering", "split"]
    assert manifest["window_seconds"] == 86400
    assert "UTC" in manifest["utc_assumption"]
    assert manifest["source"]["rows"] == 3


def test_end_to_end_smoke_limit_drops_trailing_partial(tmp_path, monkeypatch) -> None:
    parquet = tmp_path / "in.parquet"
    write_input_parquet(parquet, [
        ("t1", datetime(2022, 9, 1, 0, 1), "A::1", "B::2"),
        ("t2", datetime(2022, 9, 1, 0, 1), "A::1", "C::3"),
        ("t3", datetime(2022, 9, 1, 0, 2), "B::2", "C::3"),
        ("t4", datetime(2022, 9, 1, 0, 2), "C::3", "A::1"),
    ])
    monkeypatch.setattr(feature_stream, "connect", lambda *args: FakeFeatureGraph())
    code = feature_stream.main([
        "--parquet", str(parquet),
        "--features-out", str(tmp_path / "feat.parquet"),
        "--manifest-out", str(tmp_path / "manifest.json"),
        "--spill-dir", str(tmp_path / "spill"),
        "--limit", "3",
    ])
    assert code == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["output"]["rows_written"] == 2
    assert manifest["output"]["dropped_trailing_partial"] == 1
