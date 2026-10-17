"""Focused query tests at the bounded-tool seam (fake graph client).

Asserts validation bounds, timeout passthrough, label isolation, and result
shaping without Docker or the dataset.
"""

from __future__ import annotations

import re

import pytest

from graph import queries

FORBIDDEN_RE = re.compile(r"target_is_laundering|is[_\s]laundering|typology|\bsplit\b", re.IGNORECASE)


class FakeResult:
    """Minimal stand-in for a FalkorDB query result."""

    def __init__(self, result_set: list[list[object]]):
        self.result_set = result_set


ANCHOR_ROW = [
    "hi8-tx-00000001", "2022-09-01 00:01", "BANKA::00001001", "BANKB::00002001",
    100.0, "US Dollar", 99.0, "US Dollar", "Wire",
]


def trace_row(anchor="hi8-tx-00000001", depth=1):
    """Build one 30-column UNION branch row for the trace parser."""
    hops: list[object] = []
    for hop in range(1, 4):
        if hop <= depth:
            hops += [f"hi8-tx-0000000{hop + 1}", "2022-09-01 00:02",
                     50.0, "Euro", 49.0, "Euro",
                     "BANKB::00002001", "BANKC::00003001"]
        else:
            hops += [None] * 8
    keys = ["BANKB::00002001", "BANKC::00003001"] + [None] * 2
    return [anchor, depth] + hops + keys


class FakeGraph:
    """Dispatch canned bounded results by query fragment; record timeouts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None, int | None]] = []

    def query(self, cypher, params=None, timeout=None):
        self.calls.append((cypher, params, timeout))
        if "count(DISTINCT t." in cypher:
            # Combined fan gate + detail: 3 counterparties, 5 transfers; the
            # server returns details only when the threshold is met.
            if "(a)<-[:RECEIVED]-(t" in cypher:
                details = [["BANKY::00008001", "hi8-tx-00000008",
                            "2022-09-01 00:08", 20.0, "Euro", 19.0, "Euro"]]
            else:
                details = [["BANKZ::00009001", "hi8-tx-00000009",
                            "2022-09-01 00:09", 10.0, "US Dollar", 9.0, "US Dollar"],
                           ["BANKZ::00009001", "hi8-tx-00000010",
                            "2022-09-01 00:10", 11.0, "US Dollar", 10.0, "US Dollar"],
                           ["BANKY::00008001", "hi8-tx-00000011",
                            "2022-09-01 00:11", 12.0, "US Dollar", 11.0, "US Dollar"]]
            if params and params.get("min_fanout", 0) > 3:
                details = []
            return FakeResult([[3, 5, details]])
        if "AS histogram" in cypher:
            # One outgoing leg 60 s after the window start.
            assert params is not None
            return FakeResult([[[60 * queries.HISTOGRAM_SHIFT + 1]]])
        if "AS incoming" in cypher:
            assert params is not None
            return FakeResult([[[["hi8-tx-00000001", params["min_epoch"]]]]])
        if "AS ordinal" in cypher:
            return FakeResult([[0, "BANKA::00001001", "BANKB::00002001", "BANKC::00003001",
                                "hi8-tx-00000001", "2022-09-01 00:01", 100.0, "US Dollar",
                                99.0, "US Dollar",
                                "hi8-tx-00000002", "2022-09-01 00:02", 100.0, "US Dollar",
                                99.0, "US Dollar"]])
        if "RETURN a.last_epoch" in cypher:
            if params and params.get("account_key") == "MISSING::X":
                return FakeResult([])
            return FakeResult([[1661990400]])
        if "'sent' AS involvement" in cypher:
            return FakeResult([["hi8-tx-00000003", "2022-09-01 00:03",
                                "BANKA::00001001", "BANKD::00004001",
                                30.0, "US Dollar", 29.0, "US Dollar", "Wire", "sent"]])
        if "'received' AS involvement" in cypher:
            return FakeResult([["hi8-tx-00000004", "2022-09-01 00:04",
                                "BANKE::00005001", "BANKA::00001001",
                                40.0, "US Dollar", 39.0, "US Dollar", "Wire", "received"]])
        if "t.transaction_id <> anchor.transaction_id" in cypher:
            return FakeResult([["hi8-tx-00000005", "2022-09-01 00:05",
                                "BANKA::00001001", "BANKF::00006001",
                                60.0, "US Dollar", 59.0, "US Dollar", "Wire"]])
        if "anchor_id" in cypher:
            return FakeResult([trace_row()])
        if "MATCH (sender:Account)-[:SENT]->(anchor)" in cypher:
            if params and params.get("transaction_id") == "missing-tx":
                return FakeResult([])
            return FakeResult([list(ANCHOR_ROW)])
        raise AssertionError(f"unexpected cypher: {cypher[:100]}")

    @property
    def timeouts(self) -> list[int | None]:
        return [timeout for _, _, timeout in self.calls]


def test_validators_reject_out_of_bounds() -> None:
    with pytest.raises(ValueError):
        queries.validate_hops(0)
    with pytest.raises(ValueError):
        queries.validate_hops(4)
    with pytest.raises(TypeError):
        queries.validate_hops(True)
    with pytest.raises(TypeError):
        queries.validate_hops("2")
    with pytest.raises(ValueError):
        queries.validate_limit(0)
    with pytest.raises(ValueError):
        queries.validate_limit(101)
    with pytest.raises(TypeError):
        queries.validate_limit(50.0)
    with pytest.raises(ValueError):
        queries.validate_time_window(0)
    with pytest.raises(ValueError):
        queries.validate_time_window(queries.MAX_TIME_WINDOW_SECONDS + 1)
    with pytest.raises(TypeError):
        queries.validate_time_window("86400")
    with pytest.raises(ValueError):
        queries.validate_timeout(0)
    with pytest.raises(ValueError):
        queries.validate_timeout(queries.MAX_TIMEOUT_MS + 1)
    with pytest.raises(TypeError):
        queries.validate_timeout(None)
    with pytest.raises(ValueError):
        queries.validate_direction("upstream")
    with pytest.raises(ValueError):
        queries.validate_pattern("cycle")
    with pytest.raises(ValueError):
        queries.validate_transaction_id("  ")
    with pytest.raises(ValueError):
        queries.validate_account_key("no-separator")
    with pytest.raises(ValueError):
        queries.validate_fanout(1)
    with pytest.raises(ValueError):
        queries.validate_fanout(11)


def test_inspect_requires_exactly_one_anchor() -> None:
    graph = FakeGraph()
    with pytest.raises(ValueError, match="exactly one"):
        queries.inspect_counterparties(graph)
    with pytest.raises(ValueError, match="exactly one"):
        queries.inspect_counterparties(
            graph, account_key="B1::A1", transaction_id="hi8-tx-00000001"
        )


def test_trace_query_variants_are_bounded_and_read_only() -> None:
    single = queries.build_trace_query("outgoing", 1)
    assert "UNION" not in single
    double = queries.build_trace_query("outgoing", 2)
    assert double.count("UNION") == 1
    both = queries.build_trace_query("both", 3)
    assert both.count("UNION") == 5
    for query in (single, double, both):
        assert "LIMIT $result_limit" in query
        assert "$time_window_seconds" in query
        assert "$transaction_id" in query
        assert "MERGE" not in query
        assert "CREATE" not in query


def test_trace_query_has_unique_return_aliases() -> None:
    query = queries.build_trace_query("both", 3)
    aliases = [
        "anchor_id", "depth",
        *(f"{name}_{hop}" for hop in (1, 2, 3) for name in (
            "transaction_id", "timestamp", "amount_paid", "payment_currency",
            "amount_received", "receiving_currency", "sender_account_key",
            "receiver_account_key",
        )),
        *(f"account_key_{hop}" for hop in range(4)),
    ]
    for branch in query.split("\nUNION\n"):
        for alias in aliases:
            assert branch.count(f"AS {alias}") == 1, alias


def test_pass_through_bounds_both_transfer_times_to_the_requested_window() -> None:
    window = "t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch"
    assert window in queries.TRACE_PASS_THROUGH_OUT_HISTOGRAM
    assert window in queries.TRACE_PASS_THROUGH_FIRST_INCOMING
    assert "LIMIT $result_limit" in queries.TRACE_PASS_THROUGH_FIRST_INCOMING
    # Qualifying t1 intervals never extend past the window on either side.
    intervals = queries.qualifying_intervals(
        [100, 150, 1000], dwell_seconds=60, min_epoch=50, max_epoch=1000
    )
    assert intervals == [[50, 150], [940, 1000]]


def test_all_statements_avoid_labels_and_enforce_limits() -> None:
    statements = list(queries.ALL_QUERIES)
    statements.append(queries.build_trace_query("both", 3))
    assert statements
    for statement in statements:
        assert not FORBIDDEN_RE.search(statement), statement[:120]
        # Row-bounded: explicit LIMIT, single-row anchor lookup, or
        # single-row aggregation gate.
        assert (
            "$result_limit" in statement
            or "LIMIT 1" in statement
            or "RETURN count(" in statement
            or "RETURN collect(" in statement
        )


def test_timeout_is_passed_to_every_call() -> None:
    graph = FakeGraph()
    queries.trace_funds(graph, "hi8-tx-00000001", timeout_ms=1234)
    assert graph.calls
    assert set(graph.timeouts) == {1234}


def test_trace_funds_shapes_paths_and_duration() -> None:
    result = queries.trace_funds(
        FakeGraph(), "hi8-tx-00000001", direction="outgoing", max_hops=1
    )
    assert result["anchor_transaction_id"] == "hi8-tx-00000001"
    assert result["anchor"]["payment_currency"] == "US Dollar"
    assert len(result["paths"]) == 1
    path = result["paths"][0]
    assert path["transaction_ids"] == ["hi8-tx-00000002"]
    assert path["account_keys"] == ["BANKB::00002001", "BANKC::00003001"]
    assert result["transactions"][0]["receiving_currency"] == "Euro"
    assert result["transactions"][0]["source_account_key"] == "BANKB::00002001"
    assert result["transactions"][0]["destination_account_key"] == "BANKC::00003001"
    assert {a["account_key"] for a in result["accounts"]} == {
        "BANKB::00002001", "BANKC::00003001",
    }
    assert result["query_duration_ms"] >= 0.0


def test_trace_funds_rejects_unknown_anchor() -> None:
    with pytest.raises(ValueError, match="unknown transaction_id"):
        queries.trace_funds(FakeGraph(), "missing-tx")


def test_inspect_by_transaction_includes_anchor_and_siblings() -> None:
    result = queries.inspect_counterparties(
        FakeGraph(), transaction_id="hi8-tx-00000001"
    )
    ids = [t["transaction_id"] for t in result["transactions"]]
    assert ids == ["hi8-tx-00000001", "hi8-tx-00000005"]
    assert result["transactions"][0]["source_account_key"] == "BANKA::00001001"
    assert result["query_duration_ms"] >= 0.0


def test_inspect_by_account_merges_both_legs() -> None:
    result = queries.inspect_counterparties(FakeGraph(), account_key="BANKA::00001001")
    by_id = {t["transaction_id"]: t for t in result["transactions"]}
    assert by_id["hi8-tx-00000003"]["involvement"] == "sent"
    assert by_id["hi8-tx-00000004"]["involvement"] == "received"
    assert "BANKA::00001001" in {a["account_key"] for a in result["accounts"]}


def test_detect_fan_out_groups_by_distinct_counterparty() -> None:
    fan = queries.detect_flow_patterns(
        FakeGraph(), account_key="BANKA::00001001", pattern="fan_out"
    )
    assert fan["pattern"] == "fan_out"
    assert fan["distinct_counterparties"] == 3
    by_key = {m["counterparty_key"]: m for m in fan["matches"]}
    assert set(by_key) == {"BANKZ::00009001", "BANKY::00008001"}
    assert by_key["BANKZ::00009001"]["transfer_count"] == 2
    assert [t["transaction_id"] for t in by_key["BANKZ::00009001"]["transfers"]] == [
        "hi8-tx-00000009", "hi8-tx-00000010",
    ]
    assert by_key["BANKY::00008001"]["transfer_count"] == 1
    assert fan["query_duration_ms"] >= 0.0


def test_detect_fan_gate_failure_returns_empty_matches_with_count() -> None:
    fan = queries.detect_flow_patterns(
        FakeGraph(), account_key="BANKA::00001001", pattern="fan_out", min_fanout=4
    )
    assert fan["distinct_counterparties"] == 3
    assert fan["matches"] == []


def test_detect_fan_in_groups_receiving_side() -> None:
    fan = queries.detect_flow_patterns(
        FakeGraph(), account_key="BANKA::00001001", pattern="fan_in"
    )
    assert fan["pattern"] == "fan_in"
    assert fan["distinct_counterparties"] == 3
    assert len(fan["matches"]) == 1
    assert fan["matches"][0]["counterparty_key"] == "BANKY::00008001"
    assert fan["matches"][0]["transfer_count"] == 1


def test_detect_pass_through_reports_no_distinct_count() -> None:
    chain = queries.detect_flow_patterns(
        FakeGraph(), account_key="BANKB::00002001", pattern="pass_through"
    )
    assert chain["distinct_counterparties"] is None
    assert chain["matches"][0]["transaction_ids"] == [
        "hi8-tx-00000001", "hi8-tx-00000002",
    ]
    assert chain["matches"][0]["account_keys"][1] == "BANKB::00002001"


def test_latest_epoch_covers_both_directions() -> None:
    fallback = queries.TRACE_ACCOUNT_LATEST_EPOCH
    # Regression: iteration 1 used UNION ... ORDER BY ... LIMIT 1, which
    # orders only the last branch. The fallback aggregates max() per leg.
    assert "UNION" not in fallback
    assert "-[:SENT]->" in fallback
    assert "<-[:RECEIVED]-" in fallback
    assert "max(s.timestamp_epoch)" in fallback
    assert "max(r.timestamp_epoch)" in fallback
    assert "a.last_epoch" in queries.TRACE_ACCOUNT_LAST_EPOCH
    # Receiver-only accounts resolve through the same lookup.
    result = queries.inspect_counterparties(
        FakeGraph(), account_key="BANKR::00009999"
    )
    assert result["anchor"] == {"account_key": "BANKR::00009999"}
    with pytest.raises(ValueError, match="unknown account_key"):
        queries.inspect_counterparties(FakeGraph(), account_key="MISSING::X")
    with pytest.raises(queries.AccountNotFound):
        queries.inspect_counterparties(FakeGraph(), account_key="MISSING::X")


def test_latest_epoch_falls_back_when_property_missing() -> None:
    class LegacyLoadedGraph(FakeGraph):
        def query(self, cypher, params=None, timeout=None):
            if "RETURN a.last_epoch" in cypher:
                self.calls.append((cypher, params, timeout))
                return FakeResult([[None]])
            if "max(s.timestamp_epoch)" in cypher:
                self.calls.append((cypher, params, timeout))
                return FakeResult([[1661990999]])
            return super().query(cypher, params=params, timeout=timeout)

    graph = LegacyLoadedGraph()
    assert queries._latest_account_epoch(graph, "BANKA::00001001", 1000) == 1661990999


def test_unknown_transaction_raises_typed_error() -> None:
    with pytest.raises(queries.TransactionNotFound, match="unknown transaction_id"):
        queries.trace_funds(FakeGraph(), "missing-tx")
    assert issubclass(queries.TransactionNotFound, ValueError)


def test_declarative_cypher_mirror_is_in_sync() -> None:
    from pathlib import Path

    mirror = (Path(__file__).resolve().parents[1] / "queries.cypher").read_text(encoding="utf-8")
    for statement in queries.ALL_QUERIES:
        assert statement.rstrip() in mirror
    assert queries._trace_branch("outgoing", 2) in mirror


def test_trace_duration_covers_anchor_lookup() -> None:
    class SlowAnchor(FakeGraph):
        def query(self, cypher, params=None, timeout=None):
            import time as _time

            if "MATCH (sender:Account)-[:SENT]->(anchor)" in cypher:
                _time.sleep(0.05)
            return super().query(cypher, params=params, timeout=timeout)

    result = queries.trace_funds(SlowAnchor(), "hi8-tx-00000001", max_hops=1)
    assert result["query_duration_ms"] >= 50.0
