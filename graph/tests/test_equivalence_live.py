"""Live equivalence tests: optimized queries vs the frozen iteration-1 shapes.

Builds a small deterministic synthetic graph (no labels) in a throwaway
FalkorDB graph named ``agentgraph-equiv-*`` and compares every tool against
complete reference result sets computed with the legacy statements and an
unbounded row limit. Skipped when no FalkorDB server is reachable
(``TRACEAML_FALKORDB_HOST``/``TRACEAML_FALKORDB_PORT``, default
``localhost:6379``).

Comparison rule: iteration-1 queries order by timestamp only, so rows with
equal sort keys may legitimately be returned in any order and a ``LIMIT`` may
cut through a tie. The optimized result must therefore (a) contain only
records present in the complete reference with identical fields, (b) have the
same number of records as ``min(limit, reference size)``, and (c) have
exactly the reference's leading sort-key sequence. When the reference is
smaller than the limit this is plain set equality.
"""

from __future__ import annotations

import os
import random
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from graph import legacy_queries, load_graph, queries
from graph.synthetic_fixture import FixtureSpec, generate

UNBOUNDED = 1_000_000
LIMIT = 25  # small enough that hub anchors saturate it
WINDOW = 86_400


def _connect() -> Any:
    try:
        from falkordb import FalkorDB

        db = FalkorDB(
            host=os.environ.get("TRACEAML_FALKORDB_HOST", "localhost"),
            port=int(os.environ.get("TRACEAML_FALKORDB_PORT", "6379")),
            socket_connect_timeout=1,
        )
        db.connection.ping()
        return db
    except Exception:  # noqa: BLE001 - any failure means "no live server"
        return None


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Any:
    db = _connect()
    if db is None:
        pytest.skip("no FalkorDB server reachable")
    spec = FixtureSpec(
        transactions=4_000, accounts=500, days=2, active_days=2, banks=12, seed=11,
        planted_per_million=2_500,
    )
    parquet = Path(tmp_path_factory.mktemp("equiv")) / "transactions.parquet"
    generate(spec, parquet, chunk_rows=1_000)
    name = f"agentgraph-equiv-{uuid.uuid4().hex[:8]}"
    graph = db.select_graph(name)
    load_graph.ensure_schema(graph)
    accounts = load_graph.aggregate_accounts(parquet, limit=None, window=None)
    load_graph.create_accounts(graph, accounts, batch_size=500, timeout_ms=30_000)
    rows: list[dict[str, Any]] = []
    for batch in load_graph.iter_param_batches(parquet, batch_size=700, limit=None):
        load_graph.load_batch(graph, batch, mode="create", timeout_ms=30_000)
        rows.extend(batch)
    try:
        yield db, graph, rows
    finally:
        graph.delete()


def _q(graph: Any, cypher: str, params: dict[str, Any]) -> list[list[Any]]:
    return graph.query(cypher, params=params, timeout=30_000).result_set


def _true_latest(rows: list[dict[str, Any]]) -> dict[str, int]:
    latest: dict[str, int] = {}
    for row in rows:
        for key in (row["sender_key"], row["receiver_key"]):
            latest[key] = max(latest.get(key, 0), row["timestamp_epoch"])
    return latest


def _anchors(rows: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    rng = random.Random(5)
    degree: dict[str, int] = defaultdict(int)
    for row in rows:
        degree[row["sender_key"]] += 1
        degree[row["receiver_key"]] += 1
    hubs = sorted(degree, key=lambda k: (-degree[k], k))[:6]
    sample = rng.sample(sorted(degree), 30)
    accounts = hubs + [k for k in sample if k not in hubs]
    tx = [row["transaction_id"] for row in rng.sample(rows, 30)]
    hub_set = set(hubs)
    tx += [row["transaction_id"] for row in rows if row["receiver_key"] in hub_set][-5:]
    tx += [row["transaction_id"] for row in rows if row["sender_key"] in hub_set][-5:]
    return accounts, tx


def _assert_prefix_equivalent(
    got: list[dict[str, Any]],
    reference: list[dict[str, Any]],
    *,
    identity: tuple[str, ...],
    sort_key: tuple[str, ...],
    limit: int,
) -> None:
    by_identity = {tuple(r[f] for f in identity): r for r in reference}
    assert len(by_identity) == len(reference), "reference identities must be unique"
    for record in got:
        ident = tuple(record[f] for f in identity)
        assert ident in by_identity, ident
        assert record == by_identity[ident]
    assert len(got) == min(limit, len(reference))
    expected_keys = sorted(tuple(r[f] for f in sort_key) for r in reference)[: len(got)]
    assert sorted(tuple(r[f] for f in sort_key) for r in got) == expected_keys
    if len(reference) <= limit:
        assert {tuple(r[f] for f in identity) for r in got} == set(by_identity)


def test_latest_epoch_is_true_maximum(live: Any) -> None:
    _, graph, rows = live
    truth = _true_latest(rows)
    legacy_wrong = 0
    for key, expected in sorted(truth.items()):
        assert queries._latest_account_epoch(graph, key, 30_000) == expected
        fallback = _q(graph, queries.TRACE_ACCOUNT_LATEST_EPOCH, {"account_key": key})
        assert fallback[0][0] == expected
        legacy = legacy_queries._latest_account_epoch(graph, key, 30_000)
        legacy_wrong += legacy != expected
    # Documents the iteration-1 UNION ordering bug on this fixture.
    print(f"legacy latest-epoch wrong for {legacy_wrong} of {len(truth)} accounts")


def test_trace_funds_matches_legacy_paths(live: Any) -> None:
    _, graph, rows = live
    _, transactions = _anchors(rows)
    for tid in transactions:
        for hops in (1, 2, 3):
            got = queries.trace_funds(
                graph, tid, direction="both", max_hops=hops, time_window_seconds=WINDOW,
                result_limit=LIMIT,
            )
            per_branch: dict[tuple[str, int], set[tuple[Any, ...]]] = defaultdict(set)
            for direction in ("outgoing", "incoming"):
                for depth in range(1, hops + 1):
                    branch = legacy_queries._trace_branch(direction, depth)
                    full = _q(graph, branch, {"transaction_id": tid,
                                              "time_window_seconds": WINDOW})
                    for row in full:
                        hop_ids = tuple(row[2 + 8 * h] for h in range(depth))
                        keys = tuple(row[26 + i] for i in range(depth + 1))
                        per_branch[(direction, depth)].add((hop_ids, keys))
            got_branches: dict[tuple[str, int], list[tuple[Any, ...]]] = defaultdict(list)
            # Paths do not carry their direction in the row; reconstruct by
            # membership (outgoing and incoming paths never share tx IDs here
            # unless cyclic, in which case either branch accepts them).
            for path in got["paths"]:
                ident = (tuple(path["transaction_ids"]), tuple(path["account_keys"]))
                candidates = [b for b in per_branch if b[1] == path["depth"]
                              and ident in per_branch[b]]
                assert candidates, (tid, hops, ident)
                got_branches[candidates[0]].append(ident)
            for branch, idents in got_branches.items():
                assert len(idents) <= LIMIT
            total_expected = sum(min(LIMIT, len(v)) for v in per_branch.values())
            overlapping = any(
                per_branch[a] & per_branch[b]
                for a in per_branch for b in per_branch if a < b
            )
            if overlapping:
                # UNION removes rows that two branches both produce.
                assert len(got["paths"]) <= total_expected
            else:
                assert len(got["paths"]) == total_expected
            assert {t["transaction_id"] for t in got["transactions"]} == {
                i for p in got["paths"] for i in p["transaction_ids"]
            }


def _evidence_rows(result: list[list[Any]], columns: list[str]) -> list[dict[str, Any]]:
    return [dict(zip(columns, row)) for row in result]


def test_inspect_by_transaction_matches_legacy(live: Any) -> None:
    _, graph, rows = live
    _, transactions = _anchors(rows)
    cols = ["transaction_id", "timestamp", "sender_key", "receiver_key", "amount_paid",
            "payment_currency", "amount_received", "receiving_currency", "payment_format"]
    for tid in transactions:
        got = queries.inspect_counterparties(
            graph, transaction_id=tid, time_window_seconds=WINDOW, result_limit=LIMIT
        )
        legacy = legacy_queries.inspect_counterparties(
            graph, transaction_id=tid, time_window_seconds=WINDOW, result_limit=LIMIT
        )
        assert got["transactions"][0] == legacy["transactions"][0]
        reference = [
            queries._evidence_from_counterparty_row(r)
            for r in _evidence_rows(
                _q(graph, legacy_queries.TRACE_SIBLINGS_BY_TX,
                   {"transaction_id": tid, "time_window_seconds": WINDOW,
                    "result_limit": UNBOUNDED}),
                cols,
            )
        ]
        _assert_prefix_equivalent(
            got["transactions"][1:], reference, identity=("transaction_id",),
            sort_key=("timestamp",), limit=LIMIT,
        )


def test_inspect_by_account_matches_legacy(live: Any) -> None:
    _, graph, rows = live
    accounts, _ = _anchors(rows)
    truth = _true_latest(rows)
    cols = ["transaction_id", "timestamp", "sender_key", "receiver_key", "amount_paid",
            "payment_currency", "amount_received", "receiving_currency", "payment_format",
            "involvement"]
    for key in accounts:
        got = queries.inspect_counterparties(
            graph, account_key=key, time_window_seconds=WINDOW, result_limit=LIMIT
        )
        params = {"account_key": key, "min_epoch": truth[key] - WINDOW,
                  "max_epoch": truth[key], "result_limit": UNBOUNDED}
        for involvement, statement in (
            ("sent", legacy_queries.TRACE_COUNTERPARTIES_BY_ACCOUNT),
            ("received", legacy_queries.TRACE_COUNTERPARTIES_BY_ACCOUNT_INCOMING),
        ):
            reference = []
            for r in _evidence_rows(_q(graph, statement, params), cols):
                record = queries._evidence_from_counterparty_row(r)
                record["involvement"] = r["involvement"]
                reference.append(record)
            mine = [t for t in got["transactions"] if t["involvement"] == involvement]
            # DESC order: compare with negated-free keys by reversing the sort.
            _assert_prefix_equivalent(
                [dict(t, _k=_neg(t["timestamp"])) for t in mine],
                [dict(t, _k=_neg(t["timestamp"])) for t in reference],
                identity=("transaction_id",), sort_key=("_k",), limit=LIMIT,
            )


def _neg(timestamp: str) -> str:
    """Map a minute timestamp to a key that sorts in reverse order."""
    return "".join(chr(0x7F - ord(c)) for c in timestamp)


def test_fan_patterns_match_legacy(live: Any) -> None:
    _, graph, rows = live
    accounts, _ = _anchors(rows)
    truth = _true_latest(rows)
    detail_cols = ["counterparty_key", "transaction_id", "timestamp", "amount_paid",
                   "payment_currency", "amount_received", "receiving_currency"]
    for key in accounts:
        window = {"account_key": key, "min_epoch": truth[key] - WINDOW,
                  "max_epoch": truth[key], "result_limit": UNBOUNDED}
        for pattern, gate, detail in (
            ("fan_out", legacy_queries.TRACE_FAN_GATE_OUT, legacy_queries.TRACE_FAN_DETAIL_OUT),
            ("fan_in", legacy_queries.TRACE_FAN_GATE_IN, legacy_queries.TRACE_FAN_DETAIL_IN),
        ):
            for min_fanout in (2, 3, 10):
                got = queries.detect_flow_patterns(
                    graph, account_key=key, pattern=pattern, time_window_seconds=WINDOW,
                    min_fanout=min_fanout, result_limit=LIMIT,
                )
                distinct = _q(graph, gate, window)[0][0]
                assert got["distinct_counterparties"] == distinct
                if distinct < min_fanout:
                    assert got["matches"] == []
                    continue
                reference = _evidence_rows(_q(graph, detail, window), detail_cols)
                flat = [
                    dict(transfer, counterparty_key=match["counterparty_key"])
                    for match in got["matches"] for transfer in match["transfers"]
                ]
                for match in got["matches"]:
                    assert match["transfer_count"] == len(match["transfers"])
                _assert_prefix_equivalent(
                    flat, reference, identity=("transaction_id",), sort_key=("timestamp",),
                    limit=LIMIT,
                )


def test_pass_through_matches_legacy(live: Any) -> None:
    _, graph, rows = live
    accounts, _ = _anchors(rows)
    truth = _true_latest(rows)
    cols = ["entry_key", "middle_key", "exit_key",
            "transaction_id_1", "timestamp_1", "amount_paid_1", "payment_currency_1",
            "amount_received_1", "receiving_currency_1",
            "transaction_id_2", "timestamp_2", "amount_paid_2", "payment_currency_2",
            "amount_received_2", "receiving_currency_2"]
    nonempty = 0
    for key in accounts:
        for dwell in (WINDOW, 3_600, 300):
            got = queries.detect_flow_patterns(
                graph, account_key=key, pattern="pass_through", time_window_seconds=WINDOW,
                dwell_seconds=dwell, result_limit=LIMIT,
            )
            params = {"account_key": key, "min_epoch": truth[key] - WINDOW,
                      "max_epoch": truth[key], "result_limit": UNBOUNDED,
                      "dwell_seconds": dwell}
            reference = _evidence_rows(_q(graph, legacy_queries.TRACE_PASS_THROUGH, params), cols)
            flat = []
            for match in got["matches"]:
                row = {"entry_key": match["account_keys"][0],
                       "middle_key": match["account_keys"][1],
                       "exit_key": match["account_keys"][2]}
                for hop, suffix in (("first_hop", "1"), ("second_hop", "2")):
                    for field, value in match[hop].items():
                        row[f"{field}_{suffix}"] = value
                flat.append(row)
            nonempty += bool(flat)
            _assert_prefix_equivalent(
                flat, reference, identity=("transaction_id_1", "transaction_id_2"),
                sort_key=("timestamp_1", "timestamp_2"), limit=LIMIT,
            )
            # Pairs come back in (t1, t2) time order.
            keys = [(r["timestamp_1"], r["timestamp_2"]) for r in flat]
            assert keys == sorted(keys)
    assert nonempty > 0


def test_merge_mode_builds_same_graph_as_create_mode(live: Any, tmp_path: Path) -> None:
    db, graph, rows = live
    name = f"agentgraph-equiv-merge-{uuid.uuid4().hex[:8]}"
    other = db.select_graph(name)
    try:
        load_graph.ensure_schema(other)
        for start in range(0, len(rows), 900):
            load_graph.load_batch(other, rows[start:start + 900], mode="merge", timeout_ms=30_000)
        # Idempotent re-run.
        load_graph.load_batch(other, rows[:900], mode="merge", timeout_ms=30_000)
        statement = "MATCH (a:Account) RETURN a.account_key, a.last_epoch ORDER BY a.account_key"
        assert _q(other, statement, {}) == _q(graph, statement, {})
        tx = ("MATCH (s:Account)-[:SENT]->(t:Transaction)-[:RECEIVED]->(d:Account) "
              "RETURN t.transaction_id, s.account_key, d.account_key, t.timestamp, "
              "t.amount_paid ORDER BY t.transaction_id")
        assert _q(other, tx, {}) == _q(graph, tx, {})
        assert load_graph.reconcile_counts(other, rows_attempted=len(rows), timeout_ms=30_000)
    finally:
        other.delete()
    # The self-contained BATCH_CYPHER (used by small direct loaders such as
    # the API integration test) must build the same graph.
    single = db.select_graph(f"agentgraph-equiv-batch-{uuid.uuid4().hex[:8]}")
    try:
        load_graph.ensure_schema(single)
        for start in range(0, len(rows), 900):
            single.query(load_graph.BATCH_CYPHER, params={"rows": rows[start:start + 900]},
                         timeout=30_000)
        assert _q(single, statement, {}) == _q(graph, statement, {})
        assert _q(single, tx, {}) == _q(graph, tx, {})
    finally:
        single.delete()
