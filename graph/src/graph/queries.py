"""Bounded, parameterized investigation queries for TraceAML.

Approved tools: ``trace_funds``, ``inspect_counterparties``, and
``detect_flow_patterns``. Every query is parameterized (no value
interpolation; the only formatted integers are the validated hop depths that
select among fixed query variants), row-bounded with ``LIMIT $result_limit``,
time-bounded with epoch filters, and executed with a client-side timeout
passed as ``timeout=`` milliseconds to ``graph.query`` (Context7-verified
falkordb-py signature ``graph.query(query, params, timeout=ms)``).

Bounds (shared with the ``POST /api/investigations`` contract):

- hop depth: 1-3
- row limit: 1-100
- time window: 1 second to 30 days (2_592_000 s)
- query timeout: default 5_000 ms, max 30_000 ms

Results carry source transaction IDs, timestamps, amounts paired with their
own currencies, account keys, ordered paths, and ``query_duration_ms``.
Queries reference only inference-safe properties; target labels, split
assignments, and typology metadata never appear.
"""

from __future__ import annotations

import time
from typing import Any, Literal

GRAPH_NAME = "traceaml"

MIN_HOPS = 1
MAX_HOPS = 3
MIN_LIMIT = 1
MAX_LIMIT = 100
MAX_TIME_WINDOW_SECONDS = 30 * 86_400
MIN_FANOUT = 2
MAX_FANOUT = 10
DEFAULT_TIMEOUT_MS = 5_000
MAX_TIMEOUT_MS = 30_000

DIRECTIONS: tuple[str, ...] = ("outgoing", "incoming", "both")
PATTERNS: tuple[str, ...] = ("fan_out", "fan_in", "pass_through")


class GraphEntityNotFound(ValueError):
    """An anchor entity does not exist in the graph (or has no activity).

    Subclasses ``ValueError`` so existing ``except ValueError`` callers keep
    working; the message text is unchanged from iteration 1.
    """


class TransactionNotFound(GraphEntityNotFound):
    """The anchor ``transaction_id`` is not in the graph."""


class AccountNotFound(GraphEntityNotFound):
    """The anchor ``account_key`` is not in the graph or has no activity."""


def validate_transaction_id(value: Any) -> str:
    """Return the transaction ID or raise for empty/non-string input."""
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError("transaction_id must be a non-empty string")
    return value


def validate_account_key(value: Any) -> str:
    """Return the account key or raise unless it looks like ``bank::account``."""
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError("account_key must be a non-empty string")
    if "::" not in value:
        raise ValueError("account_key must have the form {bank}::{account}")
    return value


def validate_hops(value: Any) -> int:
    """Return hop depth or raise unless it is within 1-3."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_hops must be an integer within 1-3")
    if not MIN_HOPS <= value <= MAX_HOPS:
        raise ValueError("max_hops must be an integer within 1-3")
    return value


def validate_limit(value: Any) -> int:
    """Return the row limit or raise unless it is within 1-100."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("result_limit must be an integer within 1-100")
    if not MIN_LIMIT <= value <= MAX_LIMIT:
        raise ValueError("result_limit must be an integer within 1-100")
    return value


def validate_time_window(value: Any) -> int:
    """Return the time window in seconds or raise unless within 1-2592000."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("time_window_seconds must be an integer within 1-2592000")
    if not 1 <= value <= MAX_TIME_WINDOW_SECONDS:
        raise ValueError("time_window_seconds must be an integer within 1-2592000")
    return value


def validate_timeout(value: Any) -> int:
    """Return the query timeout in ms or raise unless within 1-30000."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("timeout_ms must be an integer within 1-30000")
    if not 1 <= value <= MAX_TIMEOUT_MS:
        raise ValueError("timeout_ms must be an integer within 1-30000")
    return value


def validate_direction(value: Any) -> str:
    """Return the trace direction or raise for unknown values."""
    if value not in DIRECTIONS:
        raise ValueError(f"direction must be one of {list(DIRECTIONS)}")
    return value


def validate_pattern(value: Any) -> str:
    """Return the pattern name or raise for unknown values."""
    if value not in PATTERNS:
        raise ValueError(f"pattern must be one of {list(PATTERNS)}")
    return value


def validate_fanout(value: Any) -> int:
    """Return the fan threshold or raise unless within 2-10."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("min_fanout must be an integer within 2-10")
    if not MIN_FANOUT <= value <= MAX_FANOUT:
        raise ValueError("min_fanout must be an integer within 2-10")
    return value


# ---------------------------------------------------------------------------
# Query builders (fixed variants selected by validated integers only)
# ---------------------------------------------------------------------------

_HOP_COLUMNS = """\
t{hop}.transaction_id AS transaction_id_{hop}, t{hop}.timestamp AS timestamp_{hop}, \
t{hop}.amount_paid AS amount_paid_{hop}, t{hop}.payment_currency AS payment_currency_{hop}, \
t{hop}.amount_received AS amount_received_{hop}, \
t{hop}.receiving_currency AS receiving_currency_{hop}, \
t{hop}.sender_account_key AS sender_account_key_{hop}, \
t{hop}.receiver_account_key AS receiver_account_key_{hop}"""

_NULL_HOP_COLUMNS = """\
NULL AS transaction_id_{hop}, NULL AS timestamp_{hop}, NULL AS amount_paid_{hop}, \
NULL AS payment_currency_{hop}, NULL AS amount_received_{hop}, \
NULL AS receiving_currency_{hop}, NULL AS sender_account_key_{hop}, \
NULL AS receiver_account_key_{hop}"""


def _trace_branch(direction: str, depth: int) -> str:
    """Build one fixed-depth branch for ``trace_funds`` (depth already 1-3)."""
    if direction == "outgoing":
        # (anchor)-[:RECEIVED]->(a0)-[:SENT]->(t1)-[:RECEIVED]->(a1) ...
        segments = "".join(
            f"-[:RECEIVED]->(a{i - 1})-[:SENT]->(t{i})"
            for i in range(1, depth + 1)
        )
        pattern = f"(anchor){segments}-[:RECEIVED]->(a{depth})"
    else:  # incoming
        # (anchor)<-[:SENT]-(a0)<-[:RECEIVED]-(t1)<-[:SENT]-(a1) ...
        segments = "".join(
            f"<-[:SENT]-(a{i - 1})<-[:RECEIVED]-(t{i})"
            for i in range(1, depth + 1)
        )
        pattern = f"(anchor){segments}<-[:SENT]-(a{depth})"
    epoch_checks = " AND ".join(
        f"t{i}.timestamp_epoch >= lo AND t{i}.timestamp_epoch <= hi"
        for i in range(1, depth + 1)
    )
    hop_columns = ",\n  ".join(
        [_HOP_COLUMNS.format(hop=i) for i in range(1, depth + 1)]
        + [_NULL_HOP_COLUMNS.format(hop=i) for i in range(depth + 1, MAX_HOPS + 1)]
    )
    key_columns = ", ".join(
        [f"a{i}.account_key AS account_key_{i}" for i in range(depth + 1)]
        + [f"NULL AS account_key_{i}" for i in range(depth + 1, MAX_HOPS + 1)]
    )
    # The anchor is bound first and the window is projected once, so the
    # planner expands from the indexed anchor instead of starting from a
    # Transaction timestamp range scan. The whole path stays in one MATCH
    # clause to keep Cypher relationship-uniqueness semantics unchanged.
    return f"""\
MATCH (anchor:Transaction {{transaction_id: $transaction_id}})
WITH anchor, anchor.timestamp_epoch - $time_window_seconds AS lo,
  anchor.timestamp_epoch + $time_window_seconds AS hi
MATCH {pattern}
WHERE {epoch_checks}
RETURN anchor.transaction_id AS anchor_id, {depth} AS depth,
  {hop_columns},
  {key_columns}
LIMIT $result_limit"""


def build_trace_query(direction: str, max_hops: int) -> str:
    """Return the UNION query covering depths 1..``max_hops``.

    Both arguments must already be validated; the integers only select among
    fixed MATCH variants and never carry query values.

    ``LIMIT $result_limit`` applies to every (direction, depth) branch. In
    Cypher a trailing ``LIMIT`` after ``UNION`` binds only to the last
    branch, so the iteration-1 query bounded only that branch and let the
    others run to completion; the per-branch limit keeps each branch lazy and
    bounded (at most ``result_limit`` paths per branch).
    """
    directions = (direction,) if direction in ("outgoing", "incoming") else ("outgoing", "incoming")
    branches = [
        _trace_branch(d, depth) for d in directions for depth in range(1, max_hops + 1)
    ]
    return "\nUNION\n".join(branches)


TRACE_COUNTERPARTIES_BY_TX = """\
MATCH (anchor:Transaction {transaction_id: $transaction_id})
MATCH (sender:Account)-[:SENT]->(anchor)-[:RECEIVED]->(receiver:Account)
RETURN anchor.transaction_id AS transaction_id, anchor.timestamp AS timestamp,
  sender.account_key AS sender_key, receiver.account_key AS receiver_key,
  anchor.amount_paid AS amount_paid, anchor.payment_currency AS payment_currency,
  anchor.amount_received AS amount_received,
  anchor.receiving_currency AS receiving_currency,
  anchor.payment_format AS payment_format
LIMIT 1
"""

# Account-anchored statements bind the indexed account first (``WITH``) and
# then expand its adjacency. Without the explicit binding the planner may
# start from the Transaction.timestamp_epoch range index and scan every
# transaction in the window (iteration-1 root cause). Counterparty keys come
# from the loader-maintained ``sender_account_key``/``receiver_account_key``
# Transaction properties, which equal the adjacent Account key by
# construction, so no second expansion per transfer is needed. Expanded
# nodes carry no label check: the loader is the only writer, so every SENT
# target and RECEIVED source is a Transaction and every other endpoint is an
# Account (on a high-degree account the label check cost several times the
# traversal itself).

TRACE_SIBLINGS_BY_TX = """\
MATCH (anchor:Transaction {transaction_id: $transaction_id})
MATCH (sender:Account)-[:SENT]->(anchor)
WITH anchor, sender, anchor.timestamp_epoch - $time_window_seconds AS lo,
  anchor.timestamp_epoch + $time_window_seconds AS hi
MATCH (sender)-[:SENT]->(t)
WHERE t.transaction_id <> anchor.transaction_id
  AND t.timestamp_epoch >= lo AND t.timestamp_epoch <= hi
RETURN t.transaction_id AS transaction_id, t.timestamp AS timestamp,
  sender.account_key AS sender_key, t.receiver_account_key AS receiver_key,
  t.amount_paid AS amount_paid, t.payment_currency AS payment_currency,
  t.amount_received AS amount_received, t.receiving_currency AS receiving_currency,
  t.payment_format AS payment_format
ORDER BY t.timestamp_epoch
LIMIT $result_limit
"""

#: O(1) lookup of the loader-maintained latest activity epoch.
TRACE_ACCOUNT_LAST_EPOCH = """\
MATCH (a:Account {account_key: $account_key})
RETURN a.last_epoch AS epoch
LIMIT 1
"""

#: Fallback for graphs loaded before ``last_epoch`` existed: the maximum
#: sent-or-received epoch, aggregated per direction from the bound account.
TRACE_ACCOUNT_LATEST_EPOCH = """\
MATCH (a:Account {account_key: $account_key})
OPTIONAL MATCH (a)-[:SENT]->(s:Transaction)
WITH a, max(s.timestamp_epoch) AS sent_max
OPTIONAL MATCH (a)<-[:RECEIVED]-(r:Transaction)
WITH sent_max, max(r.timestamp_epoch) AS received_max
WITH CASE WHEN received_max IS NULL OR sent_max >= received_max
  THEN sent_max ELSE received_max END AS epoch
WHERE epoch IS NOT NULL
RETURN epoch
LIMIT 1
"""

TRACE_COUNTERPARTIES_BY_ACCOUNT = """\
MATCH (a:Account {account_key: $account_key})
WITH a
MATCH (a)-[:SENT]->(t)
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
RETURN t.transaction_id AS transaction_id, t.timestamp AS timestamp,
  a.account_key AS sender_key, t.receiver_account_key AS receiver_key,
  t.amount_paid AS amount_paid, t.payment_currency AS payment_currency,
  t.amount_received AS amount_received, t.receiving_currency AS receiving_currency,
  t.payment_format AS payment_format, 'sent' AS involvement
ORDER BY t.timestamp_epoch DESC
LIMIT $result_limit
"""

TRACE_COUNTERPARTIES_BY_ACCOUNT_INCOMING = """\
MATCH (a:Account {account_key: $account_key})
WITH a
MATCH (a)<-[:RECEIVED]-(t)
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
RETURN t.transaction_id AS transaction_id, t.timestamp AS timestamp,
  t.sender_account_key AS sender_key, a.account_key AS receiver_key,
  t.amount_paid AS amount_paid, t.payment_currency AS payment_currency,
  t.amount_received AS amount_received, t.receiving_currency AS receiving_currency,
  t.payment_format AS payment_format, 'received' AS involvement
ORDER BY t.timestamp_epoch DESC
LIMIT $result_limit
"""

# Fan gate and detail run as one statement: a single expansion of the
# anchor's windowed transfers yields the distinct-counterparty count, the
# transfer count, and (only when the threshold is met) the earliest
# ``result_limit`` transfers in timestamp order.
_FAN_TEMPLATE = """\
MATCH (a:Account {{account_key: $account_key}})
WITH a
MATCH {expand}
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
WITH t ORDER BY t.timestamp_epoch
WITH count(DISTINCT t.{counterparty}) AS distinct_counterparties,
  count(t) AS transfers, collect(t) AS ordered
RETURN distinct_counterparties, transfers,
  CASE WHEN distinct_counterparties >= $min_fanout
    THEN [x IN ordered[0..$result_limit] | [x.{counterparty}, x.transaction_id,
      x.timestamp, x.amount_paid, x.payment_currency, x.amount_received,
      x.receiving_currency]]
    ELSE [] END AS details
"""

TRACE_FAN_OUT = _FAN_TEMPLATE.format(
    expand="(a)-[:SENT]->(t)", counterparty="receiver_account_key"
)
TRACE_FAN_IN = _FAN_TEMPLATE.format(
    expand="(a)<-[:RECEIVED]-(t)", counterparty="sender_account_key"
)

# Pass-through runs in three bounded statements plus client-side pairing, so
# a busy middle account never materializes the in x out cross product:
#
# 1. ``TRACE_PASS_THROUGH_OUT_HISTOGRAM``: per-epoch counts of the windowed
#    outgoing legs, encoded as one integer per epoch (small payload).
# 2. ``TRACE_PASS_THROUGH_FIRST_INCOMING``: the first ``result_limit``
#    incoming legs in (epoch, transaction ID) order that have at least one
#    outgoing leg within ``dwell_seconds`` (qualification is an interval test
#    derived from the histogram). Every qualifying incoming leg contributes at
#    least one pair, so no later leg can enter the first ``result_limit``
#    pairs.
# 3. ``TRACE_PASS_THROUGH_DETAIL``: the outgoing legs at only the epochs the
#    selected pairs need, joined to the selected incoming legs by ID.
HISTOGRAM_SHIFT = 4_294_967_296  # 2**32: (epoch - min_epoch) * shift + count

TRACE_PASS_THROUGH_OUT_HISTOGRAM = """\
MATCH (b:Account {account_key: $account_key})
WITH b
MATCH (b)-[:SENT]->(t)
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
WITH t.timestamp_epoch AS e, count(t) AS c
RETURN collect((e - $min_epoch) * 4294967296 + c) AS histogram
"""

TRACE_PASS_THROUGH_FIRST_INCOMING = """\
MATCH (b:Account {account_key: $account_key})
WITH b
MATCH (b)<-[:RECEIVED]-(t)
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
  AND any(iv IN $intervals WHERE t.timestamp_epoch >= iv[0] AND t.timestamp_epoch <= iv[1])
WITH t ORDER BY t.timestamp_epoch, t.transaction_id LIMIT $result_limit
RETURN collect([t.transaction_id, t.timestamp_epoch]) AS incoming
"""

TRACE_PASS_THROUGH_DETAIL = """\
MATCH (b:Account {account_key: $account_key})
WITH b
MATCH (b)-[:SENT]->(t2)
WHERE t2.timestamp_epoch IN $out_epochs
WITH t2 ORDER BY t2.transaction_id
WITH t2.timestamp_epoch AS e2, collect(t2)[0..$result_limit] AS legs
UNWIND $items AS item
WITH item, legs WHERE e2 = item[2]
UNWIND legs AS t2
MATCH (t1:Transaction {transaction_id: item[1]})
RETURN item[0] AS ordinal, t1.sender_account_key AS entry_key,
  $account_key AS middle_key, t2.receiver_account_key AS exit_key,
  t1.transaction_id AS transaction_id_1, t1.timestamp AS timestamp_1,
  t1.amount_paid AS amount_paid_1, t1.payment_currency AS payment_currency_1,
  t1.amount_received AS amount_received_1, t1.receiving_currency AS receiving_currency_1,
  t2.transaction_id AS transaction_id_2, t2.timestamp AS timestamp_2,
  t2.amount_paid AS amount_paid_2, t2.payment_currency AS payment_currency_2,
  t2.amount_received AS amount_received_2, t2.receiving_currency AS receiving_currency_2
ORDER BY ordinal, transaction_id_2
LIMIT $result_limit
"""

#: Every executable statement in this module, for allowlist/bound assertions.
ALL_QUERIES: tuple[str, ...] = (
    TRACE_COUNTERPARTIES_BY_TX,
    TRACE_SIBLINGS_BY_TX,
    TRACE_ACCOUNT_LAST_EPOCH,
    TRACE_ACCOUNT_LATEST_EPOCH,
    TRACE_COUNTERPARTIES_BY_ACCOUNT,
    TRACE_COUNTERPARTIES_BY_ACCOUNT_INCOMING,
    TRACE_FAN_OUT,
    TRACE_FAN_IN,
    TRACE_PASS_THROUGH_OUT_HISTOGRAM,
    TRACE_PASS_THROUGH_FIRST_INCOMING,
    TRACE_PASS_THROUGH_DETAIL,
)


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------

def _timed_query(graph: Any, cypher: str, params: dict[str, Any], timeout_ms: int) -> Any:
    """Execute one parameterized query with the client-side timeout."""
    return graph.query(cypher, params=params, timeout=timeout_ms)


def _row_dicts(result: Any, columns: list[str]) -> list[dict[str, Any]]:
    """Map FalkorDB ``result_set`` rows (lists) onto named columns."""
    return [dict(zip(columns, row)) for row in result.result_set]


def _evidence_from_counterparty_row(row: dict[str, Any]) -> dict[str, Any]:
    """Shape one counterparty row into TransactionEvidence fields."""
    return {
        "transaction_id": row["transaction_id"],
        "timestamp": row["timestamp"],
        "source_account_key": row["sender_key"],
        "destination_account_key": row["receiver_key"],
        "amount_paid": row["amount_paid"],
        "payment_currency": row["payment_currency"],
        "amount_received": row["amount_received"],
        "receiving_currency": row["receiving_currency"],
        "payment_format": row.get("payment_format"),
    }


def _require_anchor_tx(graph: Any, transaction_id: str, timeout_ms: int) -> dict[str, Any]:
    """Fetch the anchor transaction or raise when it is missing."""
    result = _timed_query(graph, TRACE_COUNTERPARTIES_BY_TX, {"transaction_id": transaction_id}, timeout_ms)
    rows = _row_dicts(
        result,
        [
            "transaction_id", "timestamp", "sender_key", "receiver_key",
            "amount_paid", "payment_currency", "amount_received",
            "receiving_currency", "payment_format",
        ],
    )
    if not rows:
        raise TransactionNotFound(f"unknown transaction_id: {transaction_id}")
    return rows[0]


def _latest_account_epoch(graph: Any, account_key: str, timeout_ms: int) -> int:
    """Return the latest sent-or-received epoch for an account or raise.

    Covers both directions so receiver-only accounts remain investigable.
    Reads the loader-maintained ``Account.last_epoch`` (one index lookup);
    graphs loaded before that property existed fall back to aggregating the
    maximum epoch over both legs. (The iteration-1 ``UNION ... ORDER BY ...
    LIMIT 1`` statement ordered only its last branch, so it could return an
    arbitrary sent epoch instead of the latest one.)
    """
    params = {"account_key": account_key}
    result = _timed_query(graph, TRACE_ACCOUNT_LAST_EPOCH, params, timeout_ms)
    if not result.result_set:
        raise AccountNotFound(f"unknown account_key or no account activity: {account_key}")
    if result.result_set[0][0] is not None:
        return int(result.result_set[0][0])
    result = _timed_query(graph, TRACE_ACCOUNT_LATEST_EPOCH, params, timeout_ms)
    if not result.result_set or result.result_set[0][0] is None:
        raise AccountNotFound(f"unknown account_key or no account activity: {account_key}")
    return int(result.result_set[0][0])


def decode_histogram(encoded: list[Any], min_epoch: int) -> list[tuple[int, int]]:
    """Decode ``(epoch - min_epoch) * 2**32 + count`` integers, sorted by epoch."""
    pairs = [
        (min_epoch + int(v) // HISTOGRAM_SHIFT, int(v) % HISTOGRAM_SHIFT) for v in encoded
    ]
    return sorted(pairs)


def qualifying_intervals(
    out_epochs: list[int], *, dwell_seconds: int, min_epoch: int, max_epoch: int
) -> list[list[int]]:
    """Epoch intervals of incoming legs with an outgoing leg within the dwell.

    An incoming epoch ``e1`` qualifies iff some outgoing epoch ``e2`` has
    ``e1 <= e2 <= e1 + dwell_seconds``. Outgoing epochs whose gaps are at
    most ``dwell_seconds`` form clusters ``[s, f]``; each cluster admits
    exactly ``[s - dwell_seconds, f]``. ``out_epochs`` must be sorted.
    """
    clusters: list[list[int]] = []
    for epoch in out_epochs:
        if clusters and epoch - clusters[-1][1] <= dwell_seconds:
            clusters[-1][1] = epoch
        else:
            clusters.append([epoch, epoch])
    return [
        [max(start - dwell_seconds, min_epoch), min(end, max_epoch)]
        for start, end in clusters
    ]


def plan_pass_through_items(
    incoming: list[list[Any]],
    out_histogram: list[tuple[int, int]],
    *,
    max_epoch: int,
    dwell_seconds: int,
    result_limit: int,
) -> list[list[Any]]:
    """Return ``[ordinal, t1_id, e2]`` items covering the first pairs.

    Pairs are ordered by ``(t1.epoch, t2.epoch)`` as in iteration 1; ties
    (left unspecified by iteration 1) are broken by incoming then outgoing
    transaction ID. Each item expands to every outgoing leg at epoch ``e2``
    (capped at ``result_limit``); items stop once they cover
    ``result_limit`` pairs.
    """
    import bisect

    out_epochs = [epoch for epoch, _ in out_histogram]
    ordered = sorted((int(e), str(t)) for t, e in incoming)
    items: list[list[Any]] = []
    covered = 0
    i = 0
    while i < len(ordered) and covered < result_limit:
        epoch = ordered[i][0]
        group_end = i
        while group_end < len(ordered) and ordered[group_end][0] == epoch:
            group_end += 1
        lo = bisect.bisect_left(out_epochs, epoch)
        hi = bisect.bisect_right(out_epochs, min(epoch + dwell_seconds, max_epoch))
        for j in range(lo, hi):
            for k in range(i, group_end):
                items.append([len(items), ordered[k][1], out_epochs[j]])
                covered += min(out_histogram[j][1], result_limit)
                if covered >= result_limit:
                    break
            if covered >= result_limit:
                break
        i = group_end
    return items


# ---------------------------------------------------------------------------
# Approved tools
# ---------------------------------------------------------------------------

Direction = Literal["outgoing", "incoming", "both"]


def trace_funds(
    graph: Any,
    transaction_id: str,
    direction: Direction = "outgoing",
    max_hops: int = 2,
    time_window_seconds: int = 86_400,
    result_limit: int = 50,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Trace fund flows out of, into, or around an anchor transaction.

    Returns ordered hop records with transaction IDs, timestamps,
    amounts/currencies, traversed account keys, assembled paths, and the
    measured ``query_duration_ms``. The duration covers every database call
    in this tool (anchor lookup plus traversal) so latencies are comparable
    across tools. An empty path list is not evidence that
    no activity exists outside the bounded scope.
    """
    transaction_id = validate_transaction_id(transaction_id)
    direction = validate_direction(direction)
    max_hops = validate_hops(max_hops)
    time_window_seconds = validate_time_window(time_window_seconds)
    result_limit = validate_limit(result_limit)
    timeout_ms = validate_timeout(timeout_ms)

    started = time.perf_counter()
    anchor = _require_anchor_tx(graph, transaction_id, timeout_ms)
    cypher = build_trace_query(direction, max_hops)
    result = _timed_query(
        graph,
        cypher,
        {
            "transaction_id": transaction_id,
            "time_window_seconds": time_window_seconds,
            "result_limit": result_limit,
        },
        timeout_ms,
    )
    duration_ms = (time.perf_counter() - started) * 1000.0
    columns = (
        ["anchor_id", "depth"]
        + [f"{name}_{hop}" for hop in (1, 2, 3) for name in (
            "transaction_id", "timestamp", "amount_paid", "payment_currency",
            "amount_received", "receiving_currency", "sender_account_key",
            "receiver_account_key",
        )]
        + ["account_key_0", "account_key_1", "account_key_2", "account_key_3"]
    )
    rows = _row_dicts(result, columns)

    transactions: dict[str, dict[str, Any]] = {}
    accounts: dict[str, dict[str, str]] = {}
    paths: list[dict[str, Any]] = []
    for row in rows:
        depth = int(row["depth"])
        hop_ids: list[str] = []
        hop_records: list[dict[str, Any]] = []
        for hop in range(1, depth + 1):
            tid = row[f"transaction_id_{hop}"]
            hop_ids.append(tid)
            record = {
                "transaction_id": tid,
                "timestamp": row[f"timestamp_{hop}"],
                "amount_paid": row[f"amount_paid_{hop}"],
                "payment_currency": row[f"payment_currency_{hop}"],
                "amount_received": row[f"amount_received_{hop}"],
                "receiving_currency": row[f"receiving_currency_{hop}"],
                "source_account_key": row[f"sender_account_key_{hop}"],
                "destination_account_key": row[f"receiver_account_key_{hop}"],
            }
            transactions.setdefault(tid, record)
            hop_records.append(record)
        path_keys = [row[f"account_key_{i}"] for i in range(depth + 1)]
        for key in path_keys:
            accounts.setdefault(key, {"account_key": key})
        paths.append(
            {
                "anchor_transaction_id": row["anchor_id"],
                "direction": direction,
                "depth": depth,
                "account_keys": path_keys,
                "transaction_ids": hop_ids,
                "hops": hop_records,
            }
        )
    return {
        "anchor_transaction_id": transaction_id,
        "anchor": _evidence_from_counterparty_row(anchor),
        "direction": direction,
        "max_hops": max_hops,
        "transactions": list(transactions.values()),
        "accounts": list(accounts.values()),
        "paths": paths,
        "query_duration_ms": duration_ms,
    }


def inspect_counterparties(
    graph: Any,
    *,
    account_key: str | None = None,
    transaction_id: str | None = None,
    time_window_seconds: int = 86_400,
    result_limit: int = 50,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict[str, Any]:
    """List counterparties and repeated transaction records for one anchor.

    Exactly one of ``account_key`` or ``transaction_id`` is required. The
    transaction-anchored path centers the time window on the anchor's own
    timestamp; the account-anchored path bounds the window to
    ``time_window_seconds`` before the account's latest sent transaction.
    """
    time_window_seconds = validate_time_window(time_window_seconds)
    result_limit = validate_limit(result_limit)
    timeout_ms = validate_timeout(timeout_ms)
    if (account_key is None) == (transaction_id is None):
        raise ValueError("provide exactly one of account_key or transaction_id")

    started = time.perf_counter()
    transactions: list[dict[str, Any]] = []
    account_keys: dict[str, dict[str, str]] = {}
    anchor: dict[str, Any]
    if transaction_id is not None:
        transaction_id = validate_transaction_id(transaction_id)
        anchor = _require_anchor_tx(graph, transaction_id, timeout_ms)
        anchor_record = _evidence_from_counterparty_row(anchor)
        transactions.append(anchor_record)
        siblings = _timed_query(
            graph,
            TRACE_SIBLINGS_BY_TX,
            {
                "transaction_id": transaction_id,
                "time_window_seconds": time_window_seconds,
                "result_limit": result_limit,
            },
            timeout_ms,
        )
        for row in _row_dicts(
            siblings,
            [
                "transaction_id", "timestamp", "sender_key", "receiver_key",
                "amount_paid", "payment_currency", "amount_received",
                "receiving_currency", "payment_format",
            ],
        ):
            transactions.append(_evidence_from_counterparty_row(row))
        anchor = {"transaction_id": transaction_id}
    else:
        assert account_key is not None
        account_key = validate_account_key(account_key)
        latest = _latest_account_epoch(graph, account_key, timeout_ms)
        params = {
            "account_key": account_key,
            "min_epoch": latest - time_window_seconds,
            "max_epoch": latest,
            "result_limit": result_limit,
        }
        sent = _timed_query(graph, TRACE_COUNTERPARTIES_BY_ACCOUNT, params, timeout_ms)
        received = _timed_query(
            graph, TRACE_COUNTERPARTIES_BY_ACCOUNT_INCOMING, params, timeout_ms
        )
        columns = [
            "transaction_id", "timestamp", "sender_key", "receiver_key",
            "amount_paid", "payment_currency", "amount_received",
            "receiving_currency", "payment_format", "involvement",
        ]
        for row in _row_dicts(sent, columns) + _row_dicts(received, columns):
            record = _evidence_from_counterparty_row(row)
            record["involvement"] = row["involvement"]
            transactions.append(record)
        anchor = {"account_key": account_key}
    for record in transactions:
        account_keys.setdefault(
            record["source_account_key"], {"account_key": record["source_account_key"]}
        )
        account_keys.setdefault(
            record["destination_account_key"],
            {"account_key": record["destination_account_key"]},
        )
    return {
        "anchor": anchor,
        "transactions": transactions,
        "accounts": list(account_keys.values()),
        "query_duration_ms": (time.perf_counter() - started) * 1000.0,
    }


def detect_flow_patterns(
    graph: Any,
    *,
    account_key: str,
    pattern: str = "fan_out",
    time_window_seconds: int = 86_400,
    min_fanout: int = 3,
    dwell_seconds: int = 86_400,
    result_limit: int = 50,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Match one bounded structural pattern anchored at an account.

    ``fan_out``/``fan_in`` group the anchor's windowed transfers by distinct
    counterparty account and require at least ``min_fanout`` (2-10) distinct
    counterparties; matches are grouped per counterparty with supporting
    transfer records. One statement counts distinct counterparties and, only
    when the threshold is met, returns the earliest ``result_limit``
    transfers, so an unmet threshold returns empty matches (with the
    observed count). ``pass_through`` finds two-transaction chains through
    the anchor account where the second hop lands within ``dwell_seconds``
    after the first; candidate legs are fetched once and paired client-side
    by a sorted merge, then at most ``result_limit`` pairs are fetched by
    transaction ID. Matches carry supporting transaction IDs with timestamps
    and amounts/currencies.
    """
    account_key = validate_account_key(account_key)
    pattern = validate_pattern(pattern)
    time_window_seconds = validate_time_window(time_window_seconds)
    dwell_seconds = validate_time_window(dwell_seconds)
    result_limit = validate_limit(result_limit)
    timeout_ms = validate_timeout(timeout_ms)

    started = time.perf_counter()
    latest = _latest_account_epoch(graph, account_key, timeout_ms)
    window = {
        "account_key": account_key,
        "min_epoch": latest - time_window_seconds,
        "max_epoch": latest,
        "result_limit": result_limit,
    }
    matches: list[dict[str, Any]] = []
    distinct_counterparties: int | None = None
    if pattern in ("fan_out", "fan_in"):
        min_fanout = validate_fanout(min_fanout)
        fan_cypher = TRACE_FAN_OUT if pattern == "fan_out" else TRACE_FAN_IN
        fan = _timed_query(graph, fan_cypher, {**window, "min_fanout": min_fanout}, timeout_ms)
        distinct_counterparties = int(fan.result_set[0][0]) if fan.result_set else 0
        details = fan.result_set[0][2] if fan.result_set else []
        if distinct_counterparties >= min_fanout:
            grouped: dict[str, dict[str, Any]] = {}
            for row in (
                dict(zip(
                    [
                        "counterparty_key", "transaction_id", "timestamp",
                        "amount_paid", "payment_currency", "amount_received",
                        "receiving_currency",
                    ],
                    detail,
                ))
                for detail in details
            ):
                group = grouped.setdefault(
                    row["counterparty_key"],
                    {"counterparty_key": row["counterparty_key"], "transfers": []},
                )
                transfers = group["transfers"]
                assert isinstance(transfers, list)
                transfers.append(
                    {
                        "transaction_id": row["transaction_id"],
                        "timestamp": row["timestamp"],
                        "amount_paid": row["amount_paid"],
                        "payment_currency": row["payment_currency"],
                        "amount_received": row["amount_received"],
                        "receiving_currency": row["receiving_currency"],
                    }
                )
            for group in grouped.values():
                transfers = group["transfers"]
                assert isinstance(transfers, list)
                group["transfer_count"] = len(transfers)
                matches.append(group)
    else:
        histogram_result = _timed_query(graph, TRACE_PASS_THROUGH_OUT_HISTOGRAM, window, timeout_ms)
        encoded = histogram_result.result_set[0][0] if histogram_result.result_set else []
        out_histogram = decode_histogram(encoded or [], window["min_epoch"])
        rows: list[list[Any]] = []
        if out_histogram:
            intervals = qualifying_intervals(
                [epoch for epoch, _ in out_histogram],
                dwell_seconds=dwell_seconds,
                min_epoch=window["min_epoch"],
                max_epoch=latest,
            )
            first = _timed_query(
                graph, TRACE_PASS_THROUGH_FIRST_INCOMING, {**window, "intervals": intervals},
                timeout_ms,
            )
            incoming = first.result_set[0][0] if first.result_set else []
            items = plan_pass_through_items(
                incoming or [],
                out_histogram,
                max_epoch=latest,
                dwell_seconds=dwell_seconds,
                result_limit=result_limit,
            )
            if items:
                result = _timed_query(
                    graph,
                    TRACE_PASS_THROUGH_DETAIL,
                    {
                        "account_key": account_key,
                        "out_epochs": sorted({item[2] for item in items}),
                        "items": items,
                        "result_limit": result_limit,
                    },
                    timeout_ms,
                )
                # Drop the leading ordinal column; rows arrive in pair order.
                rows = [list(row[1:]) for row in result.result_set]
        columns = [
            "entry_key", "middle_key", "exit_key",
            "transaction_id_1", "timestamp_1",
            "amount_paid_1", "payment_currency_1",
            "amount_received_1", "receiving_currency_1",
            "transaction_id_2", "timestamp_2",
            "amount_paid_2", "payment_currency_2",
            "amount_received_2", "receiving_currency_2",
        ]
        for row in (dict(zip(columns, values)) for values in rows):
            matches.append(
                {
                    "account_keys": [row["entry_key"], row["middle_key"], row["exit_key"]],
                    "transaction_ids": [row["transaction_id_1"], row["transaction_id_2"]],
                    "first_hop": {
                        "transaction_id": row["transaction_id_1"],
                        "timestamp": row["timestamp_1"],
                        "amount_paid": row["amount_paid_1"],
                        "payment_currency": row["payment_currency_1"],
                        "amount_received": row["amount_received_1"],
                        "receiving_currency": row["receiving_currency_1"],
                    },
                    "second_hop": {
                        "transaction_id": row["transaction_id_2"],
                        "timestamp": row["timestamp_2"],
                        "amount_paid": row["amount_paid_2"],
                        "payment_currency": row["payment_currency_2"],
                        "amount_received": row["amount_received_2"],
                        "receiving_currency": row["receiving_currency_2"],
                    },
                }
            )
    return {
        "anchor": {"account_key": account_key},
        "pattern": pattern,
        "distinct_counterparties": distinct_counterparties,
        "matches": matches,
        "query_duration_ms": (time.perf_counter() - started) * 1000.0,
    }
