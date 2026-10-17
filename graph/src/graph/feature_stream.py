"""Point-in-time FalkorDB graph feature extraction for TraceAML.

Builds past-only structural features for every source transaction without
changing the main transaction-evidence graph: all intermediate state lives
in a separate aggregate feature graph (default ``traceaml-features``) holding
only ``FAccount`` nodes and one directed ``FLOW`` edge per distinct ordered
account pair with a ``last_epoch`` property.

Temporal protocol (strict past-only, minute resolution):

- Input rows are sorted out-of-core by ``(timestamp, transaction_id)`` with
  DuckDB over exactly the four allowlisted columns; ``target_is_laundering``,
  ``split``, and pattern metadata are never selected.
- Rows stream in timestamp order as complete same-minute groups. For each
  group, features are queried from prior graph state first; graph updates
  for the group apply only after every row in that minute has its features,
  so same-minute peers never observe one another.
- A ``FLOW`` edge is *recent-eligible* for group epoch ``e`` with window
  ``W`` iff ``e - W <= last_epoch < e``. The strict ``< e`` bound holds by
  construction (only strictly earlier minutes are applied) and is also
  enforced explicitly in every feature query.

Candidate features (prefix ``gfeat_``):

- ``sender_out_24h`` / ``sender_in_24h``: distinct recent-eligible
  out/in counterparties of the sender.
- ``receiver_out_24h`` / ``receiver_in_24h``: same for the receiver.
- ``prior_reverse_flow``: 1 when a ``FLOW`` edge from receiver back to
  sender exists with ``last_epoch`` strictly before the group epoch
  (all-time prior reciprocity), else 0.

Output: one target-free Parquet row per source transaction keyed by stable
``transaction_id``, plus a manifest recording the source hash, row counts,
window, feature definitions, UTC assumption, and proof that target/split
columns were never read.

Reproducible entries (from the repository root)::

    uv run --project graph traceaml-build-graph-features \\
        --parquet data/processed/hi-small-v8/transactions.parquet \\
        --features-out data/processed/hi-small-v8/graph-features.parquet \\
        --manifest-out data/processed/hi-small-v8/graph-features-manifest.json

Smoke (plumbing only; prefix-relative features, never evaluation data)::

    uv run --project graph traceaml-build-graph-features --parquet ... \\
        --features-out /tmp/gfeat.parquet --manifest-out /tmp/gfeat.json \\
        --limit 2000

Context7-verified client usage (falkordb-py): ``graph.query(query, params,
timeout=ms)`` for parameterized batches; ``graph.delete()`` to reset the
feature graph before a build so reruns reproduce identical output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FEATURE_GRAPH_DEFAULT = "traceaml-features"
HOST_DEFAULT = "localhost"
PORT_DEFAULT = 6379
WINDOW_SECONDS_DEFAULT = 24 * 3_600
TIMEOUT_MS_DEFAULT = 30_000
READ_BATCH_SIZE = 5_000
WRITE_FLUSH_ROWS = 200_000
SORT_MEMORY_LIMIT = "1GB"
SPILL_PARENT_DEFAULT = "data/interim/traceaml-graph-features"

# The only columns ever selected from the prepared Parquet. Everything else
# in that table -- in particular ``target_is_laundering`` and ``split`` --
# is never read and therefore cannot enter features, logs, or the manifest.
FEATURE_COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "timestamp",
    "sender_account_key",
    "receiver_account_key",
)

# Columns whose absence from the read path is asserted and recorded.
EXCLUDED_COLUMNS: tuple[str, ...] = ("target_is_laundering", "split")

FEATURE_NAMES: tuple[str, ...] = (
    "gfeat_sender_out_24h",
    "gfeat_sender_in_24h",
    "gfeat_receiver_out_24h",
    "gfeat_receiver_in_24h",
    "gfeat_prior_reverse_flow",
)

FEATURE_DEFINITIONS: tuple[dict[str, str], ...] = (
    {"name": "gfeat_sender_out_24h", "definition": "distinct recent-eligible out-counterparties of the sender"},
    {"name": "gfeat_sender_in_24h", "definition": "distinct recent-eligible in-counterparties of the sender"},
    {"name": "gfeat_receiver_out_24h", "definition": "distinct recent-eligible out-counterparties of the receiver"},
    {"name": "gfeat_receiver_in_24h", "definition": "distinct recent-eligible in-counterparties of the receiver"},
    {"name": "gfeat_prior_reverse_flow", "definition": "1 when a FLOW edge receiver->sender exists with last_epoch strictly before the group epoch, else 0"},
)

ELIGIBILITY_RULE = "recent-eligible iff group_epoch - window_seconds <= last_epoch < group_epoch"

OUT_NEIGHBORHOOD_Q = """\
UNWIND $keys AS k
MATCH (a:FAccount {account_key: k})
OPTIONAL MATCH (a)-[f1:FLOW]->(m:FAccount)
WHERE f1.last_epoch >= $min_epoch AND f1.last_epoch < $epoch
RETURN k AS account_key, count(DISTINCT m.account_key) AS count
"""

IN_NEIGHBORHOOD_Q = """\
UNWIND $keys AS k
MATCH (a:FAccount {account_key: k})
OPTIONAL MATCH (a)<-[f0:FLOW]-(i:FAccount)
WHERE f0.last_epoch >= $min_epoch AND f0.last_epoch < $epoch
RETURN k AS account_key, count(DISTINCT i.account_key) AS count
"""

REVERSE_Q = """\
UNWIND $pairs AS p
OPTIONAL MATCH (r:FAccount {account_key: p.receiver_key})-[f:FLOW]->(s:FAccount {account_key: p.sender_key})
WHERE f.last_epoch < $epoch
RETURN p.sender_key AS sender_key, p.receiver_key AS receiver_key, (f IS NOT NULL) AS reverse
"""

APPLY_Q = """\
UNWIND $pairs AS p
MERGE (a:FAccount {account_key: p.sender_key})
MERGE (b:FAccount {account_key: p.receiver_key})
MERGE (a)-[f:FLOW]->(b)
ON CREATE SET f.first_epoch = p.epoch
SET f.last_epoch = p.epoch
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the CLI parser and parse arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Build past-only FalkorDB graph features into a target-free Parquet "
            "table. Reads only transaction IDs, timestamps, and account keys; "
            "never reads labels, splits, or typology metadata."
        ),
        epilog=(
            "Reproducible examples (from the repository root):\n"
            "  uv run --project graph traceaml-build-graph-features "
            "--parquet data/processed/hi-small-v8/transactions.parquet "
            "--features-out data/processed/hi-small-v8/graph-features.parquet "
            "--manifest-out data/processed/hi-small-v8/graph-features-manifest.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--features-out", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--host", default=os.environ.get("TRACEAML_FALKORDB_HOST", HOST_DEFAULT))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("TRACEAML_FALKORDB_PORT", PORT_DEFAULT))
    )
    parser.add_argument("--feature-graph", default=FEATURE_GRAPH_DEFAULT)
    parser.add_argument("--window-seconds", type=int, default=WINDOW_SECONDS_DEFAULT)
    parser.add_argument("--timeout-ms", type=int, default=TIMEOUT_MS_DEFAULT)
    parser.add_argument("--limit", type=int, default=None,
                        help="Smoke mode: build over the first N chronological rows "
                             "(complete minutes only; plumbing check, not evaluation data).")
    parser.add_argument("--spill-dir", type=Path, default=None,
                        help="Parent for the temporary sorted Parquet (default: ignored data/interim path).")
    parser.add_argument("--sort-memory", default=SORT_MEMORY_LIMIT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report projection, row counts, and minute-group stats without sorting, DB access, or output.",
    )
    args = parser.parse_args(argv)
    if args.window_seconds <= 0:
        parser.error("--window-seconds must be positive")
    if args.timeout_ms <= 0:
        parser.error("--timeout-ms must be positive")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    return args


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    """Stream a file and return its hex sha256 without loading it fully."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def duckdb_connect(memory_limit: str):
    """Open an in-process DuckDB handle (import deferred for testability)."""
    import duckdb

    handle = duckdb.connect()
    handle.execute(f"SET memory_limit='{memory_limit}'")
    return handle


def quote_literal(value: str) -> str:
    """Quote a path for embedding in DuckDB SQL."""
    return "'" + value.replace("'", "''") + "'"


def write_sorted_parquet(
    parquet_path: Path,
    sorted_path: Path,
    *,
    limit: int | None,
    memory_limit: str,
    temp_dir: Path,
) -> None:
    """Out-of-core sort of the four allowlisted columns by (timestamp, id).

    The ``SELECT`` projects exactly :data:`FEATURE_COLUMNS`, so excluded
    columns never enter the sort, the spill files, or the sorted output.
    """
    temp_dir.mkdir(parents=True, exist_ok=True)
    handle = duckdb_connect(memory_limit)
    try:
        handle.execute(f"SET temp_directory={quote_literal(str(temp_dir))}")
        columns = ", ".join(FEATURE_COLUMNS)
        query = (
            f"COPY (SELECT {columns} FROM read_parquet({quote_literal(str(parquet_path))}) "
            f"ORDER BY timestamp, transaction_id"
            + (f" LIMIT {int(limit)}" if limit is not None else "")
            + f") TO {quote_literal(str(sorted_path))} (FORMAT PARQUET)"
        )
        handle.execute(query)
    finally:
        handle.close()


def epoch_of(value: Any) -> int:
    """Convert a naive Parquet timestamp to epoch seconds (UTC explicit)."""
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return int(moment.timestamp())


def minute_of_epoch(epoch: int) -> int:
    """Truncate epoch seconds to the minute."""
    return (epoch // 60) * 60


def iter_sorted_rows(sorted_path: Path):
    """Yield normalized row dicts from the DuckDB-sorted Parquet in file order."""
    import pyarrow.parquet as pq

    reader = pq.ParquetFile(sorted_path)
    for batch in reader.iter_batches(batch_size=50_000, columns=list(FEATURE_COLUMNS)):
        for record in batch.to_pylist():
            yield {
                "transaction_id": str(record["transaction_id"]),
                "epoch": epoch_of(record["timestamp"]),
                "sender_key": str(record["sender_account_key"]),
                "receiver_key": str(record["receiver_account_key"]),
            }


class MinuteGrouper:
    """Batch timestamp-ordered rows into complete same-minute groups.

    Single-group lookahead: each completed group is yielded immediately when
    the next minute starts, so memory stays bounded by the largest minute
    (measured max source minute: 11,193 rows) rather than the file. Groups
    are validated monotonic; any decrease aborts, proving the sort held
    before any feature query ran.

    In smoke mode (``drop_trailing_partial``) the final group -- possibly
    cut short by the SQL row limit -- is dropped instead of yielded, so
    every processed minute is complete. Only the final group is ever
    dropped.
    """

    def __init__(self, *, drop_trailing_partial: bool = False) -> None:
        self.drop_trailing_partial = drop_trailing_partial
        self.groups_yielded = 0
        self.rows_seen = 0
        self.dropped = 0

    def group(self, records):
        """Yield ``(minute_epoch, rows)`` from ordered row dicts with stats."""
        current_minute: int | None = None
        current_rows: list[dict[str, Any]] = []
        for row in records:
            minute = minute_of_epoch(row["epoch"])
            if current_minute is not None and minute < current_minute:
                raise ValueError("sorted input violated minute monotonicity")
            if current_minute is None:
                current_minute = minute
            if minute != current_minute:
                yield current_minute, current_rows
                self.groups_yielded += 1
                current_minute = minute
                current_rows = []
            current_rows.append(row)
            self.rows_seen += 1
        if current_rows:
            if self.drop_trailing_partial:
                self.dropped = len(current_rows)
            else:
                yield current_minute, current_rows
                self.groups_yielded += 1


def chunked(items: list[Any], size: int):
    """Yield successive slices of at most ``size`` items."""
    for start in range(0, len(items), size):
        yield items[start:start + size]


def reset_feature_graph(graph: Any) -> None:
    """Delete the feature graph so a build always starts from empty state."""
    client = getattr(graph, "client", None)
    name = getattr(graph, "name", None)
    if client is None or name is None:
        # Keep lightweight fake graphs usable in unit tests.
        graph.delete()
        return
    if name in client.list_graphs():
        graph.delete()


def ensure_feature_schema(graph: Any) -> str:
    """Index feature-account keys so batched lookups avoid label scans."""
    create_index = getattr(graph, "create_node_range_index", None)
    if create_index is None:
        return "test-double"
    try:
        create_index("FAccount", "account_key")
        return "created"
    except Exception as exc:
        message = str(exc).lower()
        if "already exists" in message or "already indexed" in message or "exists" in message:
            return "exists"
        raise


def query_neighborhood(
    graph: Any, keys: list[str], *, min_epoch: int, epoch: int, timeout_ms: int
) -> dict[str, dict[str, int]]:
    """Return per-key recent-eligible outgoing and incoming counterparties."""
    counts = {key: {"out": 0, "in": 0} for key in keys}
    queries = (
        (OUT_NEIGHBORHOOD_Q, "out"),
        (IN_NEIGHBORHOOD_Q, "in"),
    )
    for batch in chunked(keys, READ_BATCH_SIZE):
        params = {"keys": batch, "min_epoch": min_epoch, "epoch": epoch}
        for query, feature_name in queries:
            result = graph.query(query, params=params, timeout=timeout_ms)
            for key, count in result.result_set:
                counts[str(key)][feature_name] = int(count)
    return counts


def query_reverse_flow(
    graph: Any, pairs: list[dict[str, str]], *, epoch: int, timeout_ms: int
) -> dict[tuple[str, str], int]:
    """Return ``(sender, receiver) -> 0/1`` prior reverse-flow flags."""
    flags: dict[tuple[str, str], int] = {}
    unique_pairs = sorted(
        {(pair["sender_key"], pair["receiver_key"]) for pair in pairs}
    )
    for batch in chunked(
        [{"sender_key": sender, "receiver_key": receiver} for sender, receiver in unique_pairs],
        READ_BATCH_SIZE,
    ):
        result = graph.query(
            REVERSE_Q, params={"pairs": batch, "epoch": epoch}, timeout=timeout_ms
        )
        for sender_key, receiver_key, reverse in result.result_set:
            flags[(str(sender_key), str(receiver_key))] = 1 if reverse else 0
    return flags


def apply_minute(graph: Any, rows: list[dict[str, Any]], *, epoch: int, timeout_ms: int) -> None:
    """Merge one complete minute into the feature graph (post-query only)."""
    unique_pairs = sorted(
        {(row["sender_key"], row["receiver_key"]) for row in rows}
    )
    pairs = [
        {"sender_key": sender, "receiver_key": receiver, "epoch": epoch}
        for sender, receiver in unique_pairs
    ]
    for batch in chunked(pairs, READ_BATCH_SIZE):
        graph.query(APPLY_Q, params={"pairs": batch}, timeout=timeout_ms)


def featurize_minute(
    graph: Any, rows: list[dict[str, Any]], *, epoch: int, window_seconds: int, timeout_ms: int
) -> list[dict[str, Any]]:
    """Query prior-state features for every row, then apply the minute."""
    min_epoch = epoch - window_seconds
    keys = sorted({row["sender_key"] for row in rows} | {row["receiver_key"] for row in rows})
    pairs = [{"sender_key": row["sender_key"], "receiver_key": row["receiver_key"]} for row in rows]
    neighborhood = query_neighborhood(
        graph, keys, min_epoch=min_epoch, epoch=epoch, timeout_ms=timeout_ms
    )
    reverse = query_reverse_flow(graph, pairs, epoch=epoch, timeout_ms=timeout_ms)
    default = {"out": 0, "in": 0}
    features = []
    for row in rows:
        sender = neighborhood.get(row["sender_key"], default)
        receiver = neighborhood.get(row["receiver_key"], default)
        features.append(
            {
                "transaction_id": row["transaction_id"],
                "gfeat_sender_out_24h": sender["out"],
                "gfeat_sender_in_24h": sender["in"],
                "gfeat_receiver_out_24h": receiver["out"],
                "gfeat_receiver_in_24h": receiver["in"],
                "gfeat_prior_reverse_flow": reverse.get(
                    (row["sender_key"], row["receiver_key"]), 0
                ),
            }
        )
    apply_minute(graph, rows, epoch=epoch, timeout_ms=timeout_ms)
    return features


OUTPUT_SCHEMA_NAMES: tuple[str, ...] = ("transaction_id", *FEATURE_NAMES)


def open_writer(features_path: Path):
    """Open a streaming Parquet writer for target-free feature rows."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    features_path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([("transaction_id", pa.string())] + [(name, pa.int64()) for name in FEATURE_NAMES])
    return pq.ParquetWriter(features_path, schema)


def connect(host: str, port: int, graph_name: str) -> Any:
    """Open the FalkorDB feature-graph handle (import deferred)."""
    from falkordb import FalkorDB

    return FalkorDB(host=host, port=port).select_graph(graph_name)


def dry_run_report(parquet_path: Path) -> dict[str, Any]:
    """Aggregate-only projection and group stats (no sort, DB, or output)."""
    handle = duckdb_connect(SORT_MEMORY_LIMIT)
    try:
        described = handle.execute(
            f"DESCRIBE SELECT {', '.join(FEATURE_COLUMNS)} "
            f"FROM read_parquet({quote_literal(str(parquet_path))})"
        ).fetchall()
        columns = [row[0] for row in described]
        if tuple(columns) != FEATURE_COLUMNS:
            raise ValueError(f"unexpected projected schema: {columns}")
        stats = handle.execute(
            "SELECT SUM(cnt) AS n, COUNT(*) AS minutes, MAX(cnt) AS max_group "
            "FROM (SELECT date_trunc('minute', timestamp) AS minute, COUNT(*) AS cnt "
            f"FROM read_parquet({quote_literal(str(parquet_path))}) GROUP BY minute) t"
        ).fetchone()
        if stats is None:
            raise ValueError("empty input table")
        return {"columns": columns, "rows": int(stats[0]), "distinct_minutes": int(stats[1]),
                "max_minute_rows": int(stats[2])}
    finally:
        handle.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = parse_args(argv)
    if not args.parquet.is_file():
        print(json.dumps({"error": f"parquet file not found: {args.parquet}"}))
        return 2
    if set(FEATURE_COLUMNS) & set(EXCLUDED_COLUMNS):
        print(json.dumps({"error": "allowlist overlaps excluded columns"}))
        return 1

    if args.dry_run:
        try:
            report = dry_run_report(args.parquet)
        except Exception as exc:  # noqa: BLE001 - report driver/SQL failures as JSON
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
            return 1
        report.update({"dry_run": True, "excluded_never_selected": list(EXCLUDED_COLUMNS)})
        print(json.dumps(report, indent=2))
        return 0

    import pyarrow.parquet as pq

    total_rows = pq.read_metadata(args.parquet).num_rows
    full_build = args.limit is None or args.limit >= total_rows
    spill_parent = args.spill_dir or Path(SPILL_PARENT_DEFAULT)
    run_dir = spill_parent / f"run-{int(time.time())}-{os.getpid()}"
    sorted_path = run_dir / "sorted.parquet"
    started = time.perf_counter()
    timings: dict[str, float] = {}
    try:
        phase = time.perf_counter()
        write_sorted_parquet(
            args.parquet, sorted_path, limit=args.limit,
            memory_limit=args.sort_memory, temp_dir=run_dir,
        )
        timings["sort_seconds"] = time.perf_counter() - phase

        graph = connect(args.host, args.port, args.feature_graph)
        reset_feature_graph(graph)
        feature_index_status = ensure_feature_schema(graph)

        phase = time.perf_counter()
        writer = open_writer(args.features_out)
        buffer: list[dict[str, Any]] = []
        rows_written = 0
        rows_used = 0
        max_group = 0
        grouper = MinuteGrouper(drop_trailing_partial=not full_build)
        try:
            for minute, rows in grouper.group(iter_sorted_rows(sorted_path)):
                max_group = max(max_group, len(rows))
                buffer.extend(
                    featurize_minute(
                        graph, rows, epoch=minute,
                        window_seconds=args.window_seconds, timeout_ms=args.timeout_ms,
                    )
                )
                rows_used += len(rows)
                if len(buffer) >= WRITE_FLUSH_ROWS:
                    writer.write_table(table_of(buffer))
                    rows_written += len(buffer)
                    buffer = []
            if buffer:
                writer.write_table(table_of(buffer))
                rows_written += len(buffer)
        finally:
            writer.close()
        timings["query_write_seconds"] = time.perf_counter() - phase

        if rows_written != rows_used or rows_used + grouper.dropped != grouper.rows_seen:
            raise ValueError(
                "row reconciliation failed "
                f"(seen={grouper.rows_seen} used={rows_used} "
                f"written={rows_written} dropped={grouper.dropped})"
            )
    except Exception as exc:  # noqa: BLE001 - report build failures as JSON
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    timings["total_seconds"] = time.perf_counter() - started

    manifest = {
        "tool": "graph.feature_stream",
        "created_utc": datetime.now(UTC).isoformat(),
        "source": {
            "parquet": str(args.parquet),
            "sha256": sha256_of(args.parquet),
            "rows": total_rows,
        },
        "columns_read": list(FEATURE_COLUMNS),
        "columns_excluded_proof": {
            "never_selected": list(EXCLUDED_COLUMNS),
            "note": "the DuckDB sort SELECT projects exactly columns_read; "
                    "target_is_laundering and split never enter sort, spill, "
                    "query parameters, features, or logs",
        },
        "feature_graph": args.feature_graph,
        "feature_account_index": feature_index_status,
        "window_seconds": args.window_seconds,
        "eligibility_rule": ELIGIBILITY_RULE,
        "ordering": "timestamp ascending, transaction_id ascending; complete same-minute groups",
        "utc_assumption": "naive source timestamps interpreted as UTC for epoch derivation",
        "features": [dict(item) for item in FEATURE_DEFINITIONS],
        "output": {
            "features_parquet": str(args.features_out),
            "rows_written": rows_written,
            "rows_seen": grouper.rows_seen,
            "dropped_trailing_partial": grouper.dropped,
            "distinct_minutes": grouper.groups_yielded,
            "max_minute_rows": max_group,
        },
        "timings": {key: round(value, 3) for key, value in timings.items()},
    }
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "rows_written": rows_written,
            "rows_seen": grouper.rows_seen,
            "distinct_minutes": grouper.groups_yielded,
            "max_minute_rows": max_group,
            "dropped_trailing_partial": grouper.dropped,
            "feature_graph": args.feature_graph,
            "timings": manifest["timings"],
        },
        indent=2,
    ))
    return 0


def table_of(buffer: list[dict[str, Any]]):
    """Build an Arrow table from buffered feature rows (stable column order)."""
    import pyarrow as pa

    return pa.Table.from_pylist(buffer, schema=pa.schema(
        [("transaction_id", pa.string())] + [(name, pa.int64()) for name in FEATURE_NAMES]
    ))


if __name__ == "__main__":
    raise SystemExit(main())
