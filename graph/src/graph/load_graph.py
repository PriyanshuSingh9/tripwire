"""Idempotent FalkorDB loader for normalized TraceAML transactions.

Reads the normalized Parquet table in streaming batches and materializes
first-class transfers::

    (:Account)-[:SENT]->(:Transaction)-[:RECEIVED]->(:Account)

Label isolation: the Parquet projection is an explicit allowlist
(:data:`LOAD_COLUMNS`). ``target_is_laundering``, ``split``, and pattern
metadata are never read, never bound to query parameters, and never written
to the graph. All stdout output is aggregate-only; no source row values are
printed.

Reproducible entry (from the repository root)::

    uv run --project graph traceaml-load-graph \\
        --parquet data/processed/hi-small-v8/transactions.parquet

Context7-verified client usage (falkordb-py):

- ``graph.query(query, params, timeout=ms)`` for parameterized batches with a
  per-batch execution timeout in milliseconds.
- ``graph.create_node_range_index(label, property)`` before
  ``graph.create_node_unique_constraint(label, property)``; indexes are
  created first, constraints second (supported order).
- ``UNWIND $rows`` batch binding is supported Cypher.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

GRAPH_NAME_DEFAULT = "traceaml"
HOST_DEFAULT = "localhost"
PORT_DEFAULT = 6379
BATCH_SIZE_DEFAULT = 5000
ACCOUNT_BATCH_SIZE = 20_000
TIMEOUT_MS_DEFAULT = 30_000
LIMIT_HELP = (
    "Load only the first N rows in file order as a representative prefix "
    "for memory/runtime probing. This is a smoke-test prefix, never an "
    "evaluation sample."
)

# Explicit allowlist projection. Every column not listed here -- in
# particular ``target_is_laundering``, ``split``, and anything derived from
# HI-Small_Patterns.txt -- is never read from Parquet, so it cannot leak
# into parameters, properties, or logs.
LOAD_COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "timestamp",
    "sender_bank",
    "sender_account",
    "receiver_bank",
    "receiver_account",
    "sender_account_key",
    "receiver_account_key",
    "amount_paid",
    "payment_currency",
    "amount_received",
    "receiving_currency",
    "payment_format",
    "source_row_number",
)

# Property allowlist mirrored into Transaction nodes. Must stay free of
# offline-only columns; see OFFLINE_ONLY_GUARD in tests.
# ``source_row_number`` is not stored: it is never returned as evidence and is
# recoverable from the row-derived ``transaction_id`` (``hi8-tx-{row:08d}``).
# ``sender_account_key``/``receiver_account_key`` are deliberately kept
# (denormalized): account-anchored queries read the counterparty key from the
# transaction instead of paying a second expansion per transfer.
TRANSACTION_PROPERTIES: tuple[str, ...] = (
    "transaction_id",
    "timestamp",
    "timestamp_epoch",
    "sender_account_key",
    "receiver_account_key",
    "amount_paid",
    "payment_currency",
    "amount_received",
    "receiving_currency",
    "payment_format",
)

# Property allowlist for Account nodes. ``last_epoch`` is the maximum
# sent-or-received ``timestamp_epoch`` over the loaded transactions (a
# derived, label-free activity bound used to anchor account windows).
ACCOUNT_PROPERTIES: tuple[str, ...] = ("account_key", "bank", "account_id", "last_epoch")

TIMESTAMP_DISPLAY_FORMAT = "%Y-%m-%d %H:%M"

LOAD_MODES: tuple[str, ...] = ("merge", "create")

# Accounts are de-duplicated per batch client-side and upserted once each.
# ``last_epoch`` only ever increases, so re-running the same rows is a no-op.
ACCOUNT_UPSERT_CYPHER = """UNWIND $accounts AS acc
MERGE (a:Account {account_key: acc.key})
ON CREATE SET a.bank = acc.bank, a.account_id = acc.account, a.last_epoch = acc.last_epoch
ON MATCH SET a.last_epoch = CASE
  WHEN a.last_epoch IS NULL OR acc.last_epoch > a.last_epoch THEN acc.last_epoch
  ELSE a.last_epoch END
"""

_TRANSACTION_SET = """  t.timestamp = r.timestamp,
  t.timestamp_epoch = r.timestamp_epoch,
  t.sender_account_key = r.sender_key,
  t.receiver_account_key = r.receiver_key,
  t.amount_paid = r.amount_paid,
  t.payment_currency = r.payment_currency,
  t.amount_received = r.amount_received,
  t.receiving_currency = r.receiving_currency,
  t.payment_format = r.payment_format"""

# Fresh-load account phase: every distinct account is created exactly once
# with its final ``last_epoch`` (pre-aggregated client-side), so no MERGE
# or per-batch update is needed.
ACCOUNT_CREATE_CYPHER = """UNWIND $accounts AS acc
CREATE (:Account {account_key: acc.key, bank: acc.bank, account_id: acc.account,
  last_epoch: acc.last_epoch})
"""

# Idempotent path (default): safe to re-run on a populated graph.
TRANSACTION_MERGE_CYPHER = f"""UNWIND $rows AS r
MATCH (s:Account {{account_key: r.sender_key}})
MATCH (d:Account {{account_key: r.receiver_key}})
MERGE (t:Transaction {{transaction_id: r.transaction_id}})
ON CREATE SET
{_TRANSACTION_SET}
MERGE (s)-[:SENT]->(t)
MERGE (t)-[:RECEIVED]->(d)
"""

# Fresh-load path: the graph is verified empty first, so transactions and
# edges are created without per-row existence checks. The unique constraint
# on Transaction.transaction_id still rejects duplicate IDs.
TRANSACTION_CREATE_CYPHER = """UNWIND $rows AS r
MATCH (s:Account {account_key: r.sender_key})
MATCH (d:Account {account_key: r.receiver_key})
CREATE (s)-[:SENT]->(t:Transaction {
  transaction_id: r.transaction_id,
  timestamp: r.timestamp,
  timestamp_epoch: r.timestamp_epoch,
  sender_account_key: r.sender_key,
  receiver_account_key: r.receiver_key,
  amount_paid: r.amount_paid,
  payment_currency: r.payment_currency,
  amount_received: r.amount_received,
  receiving_currency: r.receiving_currency,
  payment_format: r.payment_format
})-[:RECEIVED]->(d)
"""

#: Self-contained idempotent statement over ``row_to_params`` rows (accounts
#: and transactions in one query). Kept for callers that load small graphs
#: directly, such as the API integration test; the CLI uses the faster
#: two-statement :func:`load_batch` path. Maintains ``Account.last_epoch``.
BATCH_CYPHER = f"""UNWIND $rows AS r
MERGE (s:Account {{account_key: r.sender_key}})
ON CREATE SET s.bank = r.sender_bank, s.account_id = r.sender_account,
  s.last_epoch = r.timestamp_epoch
ON MATCH SET s.last_epoch = CASE
  WHEN s.last_epoch IS NULL OR r.timestamp_epoch > s.last_epoch THEN r.timestamp_epoch
  ELSE s.last_epoch END
MERGE (d:Account {{account_key: r.receiver_key}})
ON CREATE SET d.bank = r.receiver_bank, d.account_id = r.receiver_account,
  d.last_epoch = r.timestamp_epoch
ON MATCH SET d.last_epoch = CASE
  WHEN d.last_epoch IS NULL OR r.timestamp_epoch > d.last_epoch THEN r.timestamp_epoch
  ELSE d.last_epoch END
MERGE (t:Transaction {{transaction_id: r.transaction_id}})
ON CREATE SET
{_TRANSACTION_SET}
MERGE (s)-[:SENT]->(t)
MERGE (t)-[:RECEIVED]->(d)
"""

COUNT_TRANSACTIONS_CYPHER = "MATCH (t:Transaction) RETURN count(t) AS n"
COUNT_SENT_CYPHER = "MATCH ()-[r:SENT]->() RETURN count(r) AS n"
COUNT_RECEIVED_CYPHER = "MATCH ()-[r:RECEIVED]->() RETURN count(r) AS n"
COUNT_ACCOUNTS_CYPHER = "MATCH (a:Account) RETURN count(a) AS n"


@dataclass
class LoaderStats:
    """Aggregate-only load counters for the final JSON summary."""

    rows_attempted: int = 0
    batches_executed: int = 0
    elapsed_seconds: float = 0.0
    peak_rss_mb: float = 0.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the CLI parser and parse arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Idempotently load normalized TraceAML transactions into FalkorDB "
            "as (:Account)-[:SENT]->(:Transaction)-[:RECEIVED]->(:Account). "
            "Never reads labels, splits, or typology metadata."
        ),
        epilog=(
            "Reproducible examples (from the repository root):\n"
            "  uv run --project graph traceaml-load-graph "
            "--parquet data/processed/hi-small-v8/transactions.parquet\n"
            "  uv run --project graph traceaml-load-graph "
            "--parquet data/processed/hi-small-v8/transactions.parquet --limit 1000\n"
            "  uv run --project graph traceaml-load-graph "
            "--parquet data/processed/hi-small-v8/transactions.parquet "
            "--limit 5000 --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--host", default=os.environ.get("TRACEAML_FALKORDB_HOST", HOST_DEFAULT))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("TRACEAML_FALKORDB_PORT", PORT_DEFAULT))
    )
    parser.add_argument(
        "--graph-name",
        default=os.environ.get("TRACEAML_GRAPH_NAME", GRAPH_NAME_DEFAULT),
    )
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT)
    parser.add_argument("--limit", type=int, default=None, help=LIMIT_HELP)
    parser.add_argument("--timeout-ms", type=int, default=TIMEOUT_MS_DEFAULT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stream, validate, and count Parquet batches without connecting to FalkorDB.",
    )
    parser.add_argument(
        "--mode",
        choices=LOAD_MODES,
        default="merge",
        help=(
            "'merge' (default) is idempotent and safe to re-run. 'create' is the "
            "fresh-load fast path: it refuses to run unless the graph has no "
            "Transaction nodes, then creates transactions and edges without "
            "existence checks."
        ),
    )
    parser.add_argument(
        "--window-around",
        default=None,
        metavar="TRANSACTION_ID",
        help=(
            "Load only rows whose timestamp is within --window-seconds (inclusive) "
            "of this transaction's timestamp. Reproduces the iteration-1 demo slice "
            "with hi8-tx-03863669 and 86400 s (998,021 rows)."
        ),
    )
    parser.add_argument("--window-seconds", type=int, default=86_400)
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Skip index/constraint creation (batches only).",
    )
    args = parser.parse_args(argv)
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.timeout_ms <= 0:
        parser.error("--timeout-ms must be positive")
    if args.window_seconds <= 0:
        parser.error("--window-seconds must be positive")
    return args


def validate_account_key(*, bank: Any, account: Any, key: Any, source_row_number: int) -> None:
    """Validate one ``{bank}::{account}`` composite key.

    Raises with the source row number and field name only; offending values
    are never included so logs stay aggregate-safe.
    """
    if not isinstance(bank, str) or bank == "":
        raise ValueError(f"empty bank component at source row {source_row_number}")
    if not isinstance(account, str) or account == "":
        raise ValueError(f"empty account component at source row {source_row_number}")
    if "::" in bank or "::" in account:
        raise ValueError(f"embedded separator in key component at source row {source_row_number}")
    if not isinstance(key, str) or key != f"{bank}::{account}":
        raise ValueError(f"account key mismatch at source row {source_row_number}")


def validate_transaction_id(value: Any, *, source_row_number: int) -> str:
    """Validate a row-derived transaction ID, returning it typed as str."""
    if not isinstance(value, str) or value == "":
        raise ValueError(f"empty transaction_id at source row {source_row_number}")
    return value


def row_to_params(record: dict[str, Any]) -> dict[str, Any]:
    """Convert one projected Parquet record into Cypher parameters.

    ``record`` holds only :data:`LOAD_COLUMNS` values. Amounts keep their own
    currencies; no conversion or cross-currency comparison happens here.
    Naive source timestamps are interpreted as UTC for ``timestamp_epoch``
    derivation, independent of the host local timezone.
    """
    source_row_number = int(record["source_row_number"])
    transaction_id = validate_transaction_id(record["transaction_id"], source_row_number=source_row_number)
    validate_account_key(
        bank=record["sender_bank"],
        account=record["sender_account"],
        key=record["sender_account_key"],
        source_row_number=source_row_number,
    )
    validate_account_key(
        bank=record["receiver_bank"],
        account=record["receiver_account"],
        key=record["receiver_account_key"],
        source_row_number=source_row_number,
    )
    timestamp = record["timestamp"]
    if isinstance(timestamp, datetime):
        # Source timestamps carry no zone; interpret them as UTC explicitly
        # so epoch derivation never depends on the host local timezone.
        moment = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
    else:  # pragma: no cover - defensive; Parquet schema types timestamp[us]
        raise TypeError(f"non-datetime timestamp at source row {source_row_number}")
    for field in ("amount_paid", "amount_received"):
        amount = record[field]
        if not isinstance(amount, (int, float)):
            raise TypeError(f"non-numeric {field} at source row {source_row_number}")
    for field in ("payment_currency", "receiving_currency", "payment_format"):
        if not isinstance(record[field], str) or record[field] == "":
            raise ValueError(f"empty {field} at source row {source_row_number}")
    return {
        "transaction_id": transaction_id,
        "timestamp": moment.strftime(TIMESTAMP_DISPLAY_FORMAT),
        "timestamp_epoch": int(moment.timestamp()),
        "sender_key": record["sender_account_key"],
        "receiver_key": record["receiver_account_key"],
        "sender_bank": record["sender_bank"],
        "sender_account": record["sender_account"],
        "receiver_bank": record["receiver_bank"],
        "receiver_account": record["receiver_account"],
        "amount_paid": float(record["amount_paid"]),
        "payment_currency": record["payment_currency"],
        "amount_received": float(record["amount_received"]),
        "receiving_currency": record["receiving_currency"],
        "payment_format": record["payment_format"],
        "source_row_number": source_row_number,
    }


def find_anchor_timestamp(parquet_path: Path, transaction_id: str) -> datetime:
    """Return the timestamp of one transaction (projection: ID and time only)."""
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    dataset = ds.dataset(parquet_path, format="parquet")
    table = dataset.to_table(
        columns=["transaction_id", "timestamp"],
        filter=pc.field("transaction_id") == transaction_id,
    )
    if table.num_rows != 1:
        raise ValueError(f"window anchor not found exactly once: {transaction_id}")
    value = table.column("timestamp")[0].as_py()
    if not isinstance(value, datetime):  # pragma: no cover - schema types timestamp[us]
        raise TypeError("window anchor timestamp is not a datetime")
    return value


def iter_param_batches(
    parquet_path: Path,
    *,
    batch_size: int,
    limit: int | None,
    window: tuple[datetime, datetime] | None = None,
) -> Any:
    """Yield validated ``UNWIND $rows`` parameter batches in file order.

    Streams via ``ParquetFile.iter_batches`` with the :data:`LOAD_COLUMNS`
    projection, so memory stays bounded by one batch. ``limit`` truncates to
    a file-order prefix. ``window`` keeps only rows whose timestamp lies in
    the inclusive ``[start, end]`` range (applied before ``limit``).
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    parquet_file = pq.ParquetFile(parquet_path)
    remaining = limit
    for record_batch in parquet_file.iter_batches(batch_size=batch_size, columns=list(LOAD_COLUMNS)):
        if window is not None:
            timestamps = record_batch.column(record_batch.schema.get_field_index("timestamp"))
            start = pa_scalar_like(window[0], timestamps.type)
            end = pa_scalar_like(window[1], timestamps.type)
            mask = pc.and_(pc.greater_equal(timestamps, start), pc.less_equal(timestamps, end))
            record_batch = record_batch.filter(mask)
        table = record_batch.to_pylist()
        if remaining is not None:
            table = table[:remaining]
            remaining -= len(table)
        params = [row_to_params(record) for record in table]
        if params:
            yield params
        if remaining is not None and remaining <= 0:
            break


def pa_scalar_like(value: datetime, arrow_type: Any) -> Any:
    """Build an Arrow scalar of ``arrow_type`` for timestamp comparisons."""
    import pyarrow as pa

    return pa.scalar(value, type=arrow_type)


def ensure_schema(graph: Any) -> dict[str, str]:
    """Create range indexes before unique constraints (supported order).

    Uses the client constraint API; plain Neo4j-only ``CONSTRAINT`` Cypher is
    never issued. Returns a status map for the summary. Re-runs are safe:
    already-existing indexes/constraints are reported, not fatal.
    """
    status: dict[str, str] = {}
    # Only the two lookup keys are indexed. Transaction.timestamp and
    # Transaction.timestamp_epoch range indexes are intentionally absent:
    # no approved query anchors on time, and their presence let the planner
    # choose a window-wide timestamp scan over the indexed account anchor.
    steps = [
        ("index:Account(account_key)", graph.create_node_range_index, ("Account", "account_key")),
        (
            "index:Transaction(transaction_id)",
            graph.create_node_range_index,
            ("Transaction", "transaction_id"),
        ),
        (
            "unique:Account(account_key)",
            graph.create_node_unique_constraint,
            ("Account", "account_key"),
        ),
        (
            "unique:Transaction(transaction_id)",
            graph.create_node_unique_constraint,
            ("Transaction", "transaction_id"),
        ),
    ]
    for name, func, args in steps:
        try:
            func(*args)
            status[name] = "created"
        except Exception as exc:  # noqa: BLE001 - idempotent re-runs must not fail
            message = str(exc).lower()
            if (
                "already exists" in message
                or "already indexed" in message
                or "exists" in message
            ):
                status[name] = "exists"
            else:
                status[name] = f"warning: {type(exc).__name__}"
    return status


def split_batch(params: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split validated row params into de-duplicated accounts and transaction rows.

    Each account appears once per batch with the maximum epoch it takes part
    in (as sender or receiver). Transaction rows keep only the stored
    allowlisted fields plus the two account keys used for matching.
    """
    accounts: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for row in params:
        epoch = row["timestamp_epoch"]
        for side in ("sender", "receiver"):
            key = row[f"{side}_key"]
            entry = accounts.get(key)
            if entry is None:
                accounts[key] = {
                    "key": key,
                    "bank": row[f"{side}_bank"],
                    "account": row[f"{side}_account"],
                    "last_epoch": epoch,
                }
            elif epoch > entry["last_epoch"]:
                entry["last_epoch"] = epoch
        rows.append(
            {
                "transaction_id": row["transaction_id"],
                "timestamp": row["timestamp"],
                "timestamp_epoch": epoch,
                "sender_key": row["sender_key"],
                "receiver_key": row["receiver_key"],
                "amount_paid": row["amount_paid"],
                "payment_currency": row["payment_currency"],
                "amount_received": row["amount_received"],
                "receiving_currency": row["receiving_currency"],
                "payment_format": row["payment_format"],
            }
        )
    return list(accounts.values()), rows


def aggregate_accounts(
    parquet_path: Path,
    *,
    limit: int | None,
    window: tuple[datetime, datetime] | None,
) -> list[dict[str, Any]]:
    """Return every distinct account in scope with its maximum epoch.

    Vectorized (Arrow group-by) over only the account and timestamp columns,
    with the same window/limit scoping as :func:`iter_param_batches`. Naive
    timestamps are interpreted as UTC, matching :func:`row_to_params`.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    columns = [
        "timestamp", "sender_bank", "sender_account", "sender_account_key",
        "receiver_bank", "receiver_account", "receiver_account_key",
    ]
    table = pq.read_table(parquet_path, columns=columns)
    if window is not None:
        ts = table.column("timestamp")
        mask = pc.and_(
            pc.greater_equal(ts, pa_scalar_like(window[0], ts.type)),
            pc.less_equal(ts, pa_scalar_like(window[1], ts.type)),
        )
        table = table.filter(mask)
    if limit is not None:
        table = table.slice(0, limit)
    epoch = pc.divide(
        pc.cast(pc.cast(table.column("timestamp"), pa.timestamp("us")), pa.int64()), 1_000_000
    )
    parts = []
    for side in ("sender", "receiver"):
        parts.append(
            pa.table(
                {
                    "key": table.column(f"{side}_account_key"),
                    "bank": table.column(f"{side}_bank"),
                    "account": table.column(f"{side}_account"),
                    "epoch": epoch,
                }
            )
        )
    combined = pa.concat_tables(parts)
    grouped = combined.group_by("key").aggregate(
        [("bank", "min"), ("account", "min"), ("epoch", "max")]
    )
    return [
        {"key": key, "bank": bank, "account": account, "last_epoch": int(last)}
        for key, bank, account, last in zip(
            grouped.column("key").to_pylist(),
            grouped.column("bank_min").to_pylist(),
            grouped.column("account_min").to_pylist(),
            grouped.column("epoch_max").to_pylist(),
        )
    ]


def create_accounts(
    graph: Any, accounts: list[dict[str, Any]], *, batch_size: int, timeout_ms: int
) -> int:
    """Create pre-aggregated accounts in batches; return the batch count."""
    batches = 0
    for start in range(0, len(accounts), batch_size):
        graph.query(
            ACCOUNT_CREATE_CYPHER,
            params={"accounts": accounts[start:start + batch_size]},
            timeout=timeout_ms,
        )
        batches += 1
    return batches


def load_batch(graph: Any, params: list[dict[str, Any]], *, mode: str, timeout_ms: int) -> None:
    """Load one batch.

    ``merge``: upsert the batch's de-duplicated accounts, then MERGE its
    transactions. ``create``: accounts already exist (created up front by
    :func:`create_accounts`), so only CREATE the transactions and edges.
    """
    accounts, rows = split_batch(params)
    if mode == "create":
        graph.query(TRANSACTION_CREATE_CYPHER, params={"rows": rows}, timeout=timeout_ms)
        return
    graph.query(ACCOUNT_UPSERT_CYPHER, params={"accounts": accounts}, timeout=timeout_ms)
    graph.query(TRANSACTION_MERGE_CYPHER, params={"rows": rows}, timeout=timeout_ms)


def scalar_count(graph: Any, cypher: str, *, timeout_ms: int) -> int:
    """Run a single-row ``count`` query and return the integer result."""
    result = graph.query(cypher, timeout=timeout_ms)
    return int(result.result_set[0][0])


def reconcile_counts(
    graph: Any, *, rows_attempted: int, timeout_ms: int
) -> dict[str, int]:
    """Compare ingested rows against graph-side entity counts.

    Every source row maps to exactly one ``Transaction`` node, one ``SENT``
    edge, and one ``RECEIVED`` edge, so all three counts must equal
    ``rows_attempted`` on a fresh graph. Account counts are reported without
    an equality assertion (accounts are shared across rows).
    """
    counts = {
        "transactions": scalar_count(graph, COUNT_TRANSACTIONS_CYPHER, timeout_ms=timeout_ms),
        "sent_edges": scalar_count(graph, COUNT_SENT_CYPHER, timeout_ms=timeout_ms),
        "received_edges": scalar_count(graph, COUNT_RECEIVED_CYPHER, timeout_ms=timeout_ms),
        "accounts": scalar_count(graph, COUNT_ACCOUNTS_CYPHER, timeout_ms=timeout_ms),
    }
    mismatches = {
        key: counts[key] for key in ("transactions", "sent_edges", "received_edges")
        if counts[key] != rows_attempted
    }
    if mismatches:
        raise ValueError(
            "count reconciliation failed "
            f"(rows_attempted={rows_attempted} counts={counts})"
        )
    return counts


def peak_rss_mb() -> float:
    """Return current peak resident set size in MiB (aggregate-only metric)."""
    if sys.platform == "win32":
        return _windows_peak_working_set_bytes() / (1024.0 * 1024.0)

    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _windows_peak_working_set_bytes() -> int:
    """Return the peak working set of this process on Windows, in bytes."""
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(ProcessMemoryCounters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.PeakWorkingSetSize)


def connect(host: str, port: int, graph_name: str) -> Any:
    """Open the FalkorDB graph handle (import deferred for testability)."""
    from falkordb import FalkorDB

    return FalkorDB(host=host, port=port).select_graph(graph_name)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = parse_args(argv)
    if not args.parquet.is_file():
        print(json.dumps({"error": f"parquet file not found: {args.parquet}"}))
        return 2

    started = time.perf_counter()
    stats = LoaderStats()
    schema_status: dict[str, str] = {"mode": "dry-run"} if args.dry_run else {}

    window_info: dict[str, Any] | None = None
    account_phase: dict[str, Any] | None = None
    try:
        window: tuple[datetime, datetime] | None = None
        if args.window_around is not None:
            center = find_anchor_timestamp(args.parquet, args.window_around)
            delta = timedelta(seconds=args.window_seconds)
            window = (center - delta, center + delta)
            window_info = {
                "anchor_transaction_id": args.window_around,
                "window_seconds": args.window_seconds,
                "rule": "anchor timestamp +/- window_seconds, inclusive",
            }
        graph = None
        if not args.dry_run:
            graph = connect(args.host, args.port, args.graph_name)
            if args.mode == "create":
                existing = scalar_count(graph, COUNT_TRANSACTIONS_CYPHER, timeout_ms=args.timeout_ms)
                if existing:
                    raise ValueError(
                        f"--mode create requires an empty graph ({existing} transactions present); "
                        "use --mode merge for idempotent re-runs"
                    )
            if not args.skip_schema:
                schema_status = ensure_schema(graph)
            else:
                schema_status = {"mode": "schema-skipped"}
            if args.mode == "create":
                phase_started = time.perf_counter()
                accounts = aggregate_accounts(args.parquet, limit=args.limit, window=window)
                account_batches = create_accounts(
                    graph, accounts, batch_size=ACCOUNT_BATCH_SIZE, timeout_ms=args.timeout_ms
                )
                account_phase = {
                    "accounts": len(accounts),
                    "batches": account_batches,
                    "seconds": round(time.perf_counter() - phase_started, 3),
                }

        for params in iter_param_batches(
            args.parquet, batch_size=args.batch_size, limit=args.limit, window=window
        ):
            if graph is not None:
                load_batch(graph, params, mode=args.mode, timeout_ms=args.timeout_ms)
            stats.rows_attempted += len(params)
            stats.batches_executed += 1

        reconciliation: dict[str, int] = {}
        if graph is not None:
            reconciliation = reconcile_counts(
                graph, rows_attempted=stats.rows_attempted, timeout_ms=args.timeout_ms
            )
    except (ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc), "rows_attempted": stats.rows_attempted}))
        return 1

    stats.elapsed_seconds = time.perf_counter() - started
    stats.peak_rss_mb = peak_rss_mb()
    print(
        json.dumps(
            {
                "parquet": str(args.parquet),
                "graph": args.graph_name,
                "rows": stats.rows_attempted,
                "batches": stats.batches_executed,
                "elapsed_seconds": round(stats.elapsed_seconds, 3),
                "peak_rss_mb": round(stats.peak_rss_mb, 1),
                "limit": args.limit,
                "mode": args.mode,
                "window": window_info,
                "account_phase": account_phase,
                "dry_run": args.dry_run,
                "schema": schema_status,
                "reconciliation": reconciliation,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
