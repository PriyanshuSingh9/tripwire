"""Vectorized offline point-in-time graph features for TraceAML.

Produces exactly the same five ``gfeat_*`` columns, with exactly the same
point-in-time semantics, as the FalkorDB streaming builder
(:mod:`graph.feature_stream`), but computes them in one columnar pass over
the Parquet projection instead of millions of graph round trips. The
FalkorDB builder stays available as an independent cross-check; parity is
asserted row for row in ``tests/test_feature_parity.py``.

Temporal protocol (identical to the streaming builder):

- Only ``transaction_id``, ``timestamp``, ``sender_account_key`` and
  ``receiver_account_key`` are selected; ``target_is_laundering``, ``split``
  and pattern metadata are never read.
- Every row belongs to the group epoch ``e`` = its timestamp truncated to the
  minute (naive timestamps interpreted as UTC). State visible to a row is
  every transfer in a strictly earlier minute; same-minute peers never see
  one another.
- A directed account pair ``(a, b)`` is *recent-eligible* for ``e`` with
  window ``W`` iff its latest transfer minute before ``e`` lies in
  ``[e - W, e)`` (inclusive lower bound, strict upper bound). This is the
  FalkorDB ``FLOW.last_epoch`` rule, because ``last_epoch`` there is exactly
  the latest strictly earlier transfer minute of the pair.
- ``prior_reverse_flow`` is 1 iff any transfer receiver -> sender occurred
  in a strictly earlier minute (all-time, not windowed).
- Self-transfers are ordinary pairs ``(a, a)``: they count ``a`` as its own
  out- and in-counterparty and can satisfy their own reverse flow.
- Duplicate source rows are featurized independently (one output row each)
  and collapse to one pair-minute event, as repeated ``MERGE`` does.

Algorithm (all NumPy, O(n log n)):

1. Deduplicate transfers to pair-minute events ``(a, b, t)``.
2. For consecutive events of a pair, event ``i`` is the latest-before-``e``
   event exactly when ``t_i < e <= next_i``, and it is in the window iff
   ``e <= t_i + W``. So event ``i`` contributes +1 to every query minute in
   ``(t_i, end_i]`` with ``end_i = min(next_i, t_i + W)``.
3. A distinct-counterparty count for ``(account, e)`` is therefore
   ``#{starts < e} - #{ends < e}`` over that account's events, answered with
   two ``searchsorted`` calls on account-major composite keys.
4. Reverse flow looks up the first transfer minute of pair ``(b, a)``.

Reproducible entry (from the repository root)::

    uv run --project graph traceaml-build-graph-features-batch \\
        --parquet data/processed/hi-small-v8/transactions.parquet \\
        --features-out data/processed/hi-small-v8/graph-features.parquet \\
        --manifest-out data/processed/hi-small-v8/graph-features-manifest.json
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from graph.feature_stream import (
    ELIGIBILITY_RULE,
    EXCLUDED_COLUMNS,
    FEATURE_COLUMNS,
    FEATURE_DEFINITIONS,
    FEATURE_NAMES,
    OUTPUT_SCHEMA_NAMES,
    WINDOW_SECONDS_DEFAULT,
    duckdb_connect,
    quote_literal,
    sha256_of,
)

if TYPE_CHECKING:
    import numpy as np
    import pyarrow as pa

SORT_MEMORY_DEFAULT = "4GB"
SPILL_PARENT_DEFAULT = "data/interim/traceaml-graph-features-batch"
MICROS_PER_SECOND = 1_000_000
INT64_SAFE = 1 << 62

SEMANTICS: dict[str, str] = {
    "group_epoch": "timestamp truncated to the minute; naive timestamps are UTC",
    "visibility": "a row sees only transfers in strictly earlier minutes; "
                  "same-minute peers are mutually invisible",
    "recent_counterparties": "distinct counterparties whose latest strictly earlier "
                             "transfer minute t satisfies group_epoch - window <= t",
    "prior_reverse_flow": "1 iff any receiver->sender transfer occurred in a strictly "
                          "earlier minute (all-time, not windowed)",
    "self_transfers": "a->a is an ordinary pair: a counts as its own out/in "
                      "counterparty and may satisfy its own reverse flow",
    "duplicates": "each source row is featurized; identical pair-minute transfers "
                  "collapse to one event",
    "limit": "smoke --limit keeps the first N rows by (timestamp, transaction_id) and "
             "drops the final (possibly partial) minute, as the streaming builder does",
    "parity": "row-for-row identical to graph.feature_stream (tests/test_feature_parity.py)",
}


@dataclass(frozen=True)
class BatchInput:
    """Projected, minute-ordered columns ready for vectorized computation."""

    transaction_id: pa.Array
    minute_epoch: np.ndarray
    sender: np.ndarray
    receiver: np.ndarray
    account_count: int


@dataclass(frozen=True)
class BatchFeatures:
    """The five feature arrays (int64), aligned with :class:`BatchInput` rows."""

    sender_out: np.ndarray
    sender_in: np.ndarray
    receiver_out: np.ndarray
    receiver_in: np.ndarray
    prior_reverse_flow: np.ndarray
    pair_minute_events: int


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the CLI parser and parse arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Build past-only graph features with a vectorized offline pass over "
            "the Parquet projection. Same columns and semantics as "
            "traceaml-build-graph-features (FalkorDB); never reads labels, "
            "splits, or typology metadata."
        ),
        epilog=(
            "Reproducible example (from the repository root):\n"
            "  uv run --project graph traceaml-build-graph-features-batch "
            "--parquet data/processed/hi-small-v8/transactions.parquet "
            "--features-out data/processed/hi-small-v8/graph-features.parquet "
            "--manifest-out data/processed/hi-small-v8/graph-features-manifest.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--features-out", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--window-seconds", type=int, default=WINDOW_SECONDS_DEFAULT)
    parser.add_argument("--limit", type=int, default=None,
                        help="Smoke mode: build over the first N chronological rows "
                             "(complete minutes only; plumbing check, not evaluation data).")
    parser.add_argument("--spill-dir", type=Path, default=None,
                        help="DuckDB spill directory for the sort "
                             "(default: ignored data/interim path).")
    parser.add_argument("--sort-memory", default=SORT_MEMORY_DEFAULT)
    args = parser.parse_args(argv)
    if args.window_seconds <= 0:
        parser.error("--window-seconds must be positive")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    return args


def read_sorted_projection(
    parquet_path: Path, *, limit: int | None, memory_limit: str, temp_dir: Path
) -> pa.Table:
    """Read exactly the four allowlisted columns ordered by (timestamp, id).

    The timestamp leaves DuckDB as integer epoch microseconds so no Python
    datetime objects are created; excluded columns are never selected.
    """
    temp_dir.mkdir(parents=True, exist_ok=True)
    handle = duckdb_connect(memory_limit)
    try:
        handle.execute(f"SET temp_directory={quote_literal(str(temp_dir))}")
        query = (
            "SELECT transaction_id, epoch_us(timestamp) AS epoch_us, "
            "sender_account_key, receiver_account_key "
            f"FROM read_parquet({quote_literal(str(parquet_path))}) "
            "ORDER BY timestamp, transaction_id"
            + (f" LIMIT {int(limit)}" if limit is not None else "")
        )
        result = handle.execute(query)
        fetch = getattr(result, "to_arrow_table", None) or result.fetch_arrow_table
        return fetch()
    finally:
        handle.close()


def minute_epochs_from_micros(micros: np.ndarray) -> np.ndarray:
    """Epoch-minute seconds matching ``feature_stream`` exactly.

    The streaming builder computes ``int(datetime.timestamp())`` (truncation
    toward zero) and then floors to the minute; both steps are mirrored.
    """
    import numpy as np

    seconds = np.where(
        micros >= 0, micros // MICROS_PER_SECOND, -((-micros) // MICROS_PER_SECOND)
    )
    return (seconds // 60) * 60


def prepare_input(table: pa.Table) -> BatchInput:
    """Validate the projection and encode account keys as dense integers."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc

    for name in table.column_names:
        if table.column(name).null_count:
            raise ValueError(f"null values in projected column: {name}")
    micros = table.column("epoch_us").to_numpy().astype(np.int64, copy=False)
    minute_epoch = minute_epochs_from_micros(micros)
    if minute_epoch.size and np.any(np.diff(minute_epoch) < 0):
        raise ValueError("sorted input violated minute monotonicity")

    senders = table.column("sender_account_key").cast(pa.string()).combine_chunks()
    receivers = table.column("receiver_account_key").cast(pa.string()).combine_chunks()
    encoded = pc.dictionary_encode(pa.concat_arrays([senders, receivers]))
    codes = encoded.indices.to_numpy(zero_copy_only=False).astype(np.int64)
    rows = table.num_rows
    transaction_id = table.column("transaction_id").cast(pa.string()).combine_chunks()
    return BatchInput(
        transaction_id=transaction_id,
        minute_epoch=minute_epoch,
        sender=codes[:rows],
        receiver=codes[rows:],
        account_count=len(encoded.dictionary),
    )


def drop_final_minute(data: BatchInput) -> tuple[BatchInput, int]:
    """Drop the final minute group (smoke mode); return the dropped row count."""
    import numpy as np

    if data.minute_epoch.size == 0:
        return data, 0
    keep = int(np.searchsorted(data.minute_epoch, data.minute_epoch[-1], side="left"))
    dropped = data.minute_epoch.size - keep
    return (
        BatchInput(
            transaction_id=data.transaction_id.slice(0, keep),
            minute_epoch=data.minute_epoch[:keep],
            sender=data.sender[:keep],
            receiver=data.receiver[:keep],
            account_count=data.account_count,
        ),
        dropped,
    )


def active_counts(
    start_keys: np.ndarray, end_keys: np.ndarray, *queries: np.ndarray
) -> list[np.ndarray]:
    """Per query key, ``#{start < q} - #{end < q}`` (active events of that account)."""
    import numpy as np

    starts = np.sort(start_keys)
    del start_keys
    ends = np.sort(end_keys)
    del end_keys
    return [
        np.searchsorted(starts, query, side="left") - np.searchsorted(ends, query, side="left")
        for query in queries
    ]


def compute_features(data: BatchInput, *, window_seconds: int) -> BatchFeatures:
    """Vectorized point-in-time features; see the module docstring."""
    import numpy as np

    rows = data.minute_epoch.size
    empty = np.zeros(rows, dtype=np.int64)
    if rows == 0:
        return BatchFeatures(empty, empty, empty, empty, empty, 0)
    accounts = max(data.account_count, 1)
    if accounts * accounts >= INT64_SAFE:
        raise ValueError("too many accounts for 64-bit pair keys")
    rel = data.minute_epoch - data.minute_epoch[0]
    span = int(rel[-1]) + int(window_seconds) + 1
    if accounts * span >= INT64_SAFE:
        raise ValueError("time span too large for 64-bit composite keys")

    # 1. Pair-minute events. Rows are minute-ordered, so a stable sort on the
    #    pair key yields (pair, minute) order.
    pair_key = data.sender * accounts + data.receiver
    order = np.argsort(pair_key, kind="stable")
    pair_sorted = pair_key[order]
    time_sorted = rel[order]
    del order
    is_new = np.empty(rows, dtype=bool)
    is_new[0] = True
    np.not_equal(pair_sorted[1:], pair_sorted[:-1], out=is_new[1:])
    first_of_pair = is_new.copy()
    is_new[1:] |= time_sorted[1:] != time_sorted[:-1]
    event_pair = pair_sorted[is_new]
    event_time = time_sorted[is_new]
    pair_first_key = pair_sorted[first_of_pair]
    pair_first_time = time_sorted[first_of_pair]
    del pair_sorted, time_sorted, is_new, first_of_pair

    # 2. Event i counts for query minutes e in (t_i, min(next_i, t_i + W)].
    events = event_pair.size
    end = event_time + int(window_seconds)
    same_pair_next = event_pair[1:] == event_pair[:-1]
    np.minimum(end[:-1], np.where(same_pair_next, event_time[1:], end[:-1]), out=end[:-1])
    del same_pair_next
    event_src = event_pair // accounts
    event_dst = event_pair - event_src * accounts
    del event_pair

    # 3. Account-major composite keys: other accounts' keys fall entirely
    #    below or above a query key, and every event has one start and one
    #    end key under the same account, so the lower-account offsets cancel.
    sender_query = data.sender * span + rel
    receiver_query = data.receiver * span + rel

    sender_out, receiver_out = active_counts(
        event_src * span + event_time, event_src * span + end, sender_query, receiver_query
    )
    sender_in, receiver_in = active_counts(
        event_dst * span + event_time, event_dst * span + end, sender_query, receiver_query
    )
    del sender_query, receiver_query, event_src, event_dst, end, event_time

    # 4. Reverse flow: first receiver->sender transfer strictly before e.
    reverse_key = data.receiver * accounts + data.sender
    position = np.searchsorted(pair_first_key, reverse_key, side="left")
    np.minimum(position, pair_first_key.size - 1, out=position)
    found = pair_first_key[position] == reverse_key
    reverse = (found & (pair_first_time[position] < rel)).astype(np.int64)

    return BatchFeatures(
        sender_out=sender_out.astype(np.int64, copy=False),
        sender_in=sender_in.astype(np.int64, copy=False),
        receiver_out=receiver_out.astype(np.int64, copy=False),
        receiver_in=receiver_in.astype(np.int64, copy=False),
        prior_reverse_flow=reverse,
        pair_minute_events=int(events),
    )


def output_schema() -> pa.Schema:
    """Exactly the streaming builder's output schema."""
    import pyarrow as pa

    return pa.schema(
        [("transaction_id", pa.string())] + [(name, pa.int64()) for name in FEATURE_NAMES]
    )


def features_table(data: BatchInput, features: BatchFeatures) -> pa.Table:
    """Assemble the target-free output table in (timestamp, id) order."""
    import pyarrow as pa

    columns = [
        data.transaction_id,
        pa.array(features.sender_out, type=pa.int64()),
        pa.array(features.sender_in, type=pa.int64()),
        pa.array(features.receiver_out, type=pa.int64()),
        pa.array(features.receiver_in, type=pa.int64()),
        pa.array(features.prior_reverse_flow, type=pa.int64()),
    ]
    table = pa.Table.from_arrays(columns, schema=output_schema())
    assert tuple(table.column_names) == OUTPUT_SCHEMA_NAMES
    return table


def minute_stats(minute_epoch: np.ndarray) -> tuple[int, int]:
    """Return ``(distinct_minutes, max_minute_rows)``."""
    import numpy as np

    if minute_epoch.size == 0:
        return 0, 0
    _, counts = np.unique(minute_epoch, return_counts=True)
    return int(counts.size), int(counts.max())


def peak_rss_mb() -> float | None:
    """Process peak resident memory in MiB, or None when unavailable."""
    try:
        from graph.load_graph import peak_rss_mb as loader_peak_rss_mb

        return round(float(loader_peak_rss_mb()), 1)
    except Exception:  # noqa: BLE001 - metric is best-effort only
        return None


def build(
    parquet_path: Path,
    *,
    window_seconds: int,
    limit: int | None,
    sort_memory: str,
    spill_dir: Path,
) -> tuple[pa.Table, dict[str, Any]]:
    """Run the full batch build and return the output table plus statistics."""
    import shutil

    import pyarrow.parquet as pq

    total_rows = pq.read_metadata(parquet_path).num_rows
    full_build = limit is None or limit >= total_rows
    timings: dict[str, float] = {}
    started = time.perf_counter()
    phase = started
    try:
        table = read_sorted_projection(
            parquet_path, limit=limit, memory_limit=sort_memory, temp_dir=spill_dir
        )
    finally:
        shutil.rmtree(spill_dir, ignore_errors=True)
    timings["read_sort_seconds"] = time.perf_counter() - phase

    phase = time.perf_counter()
    data = prepare_input(table)
    del table
    rows_seen = data.minute_epoch.size
    dropped = 0
    if not full_build:
        data, dropped = drop_final_minute(data)
    timings["encode_seconds"] = time.perf_counter() - phase

    phase = time.perf_counter()
    features = compute_features(data, window_seconds=window_seconds)
    timings["compute_seconds"] = time.perf_counter() - phase

    output = features_table(data, features)
    distinct_minutes, max_minute_rows = minute_stats(data.minute_epoch)
    if output.num_rows + dropped != rows_seen:
        raise ValueError(
            f"row reconciliation failed (seen={rows_seen} written={output.num_rows} "
            f"dropped={dropped})"
        )
    stats = {
        "source_rows": total_rows,
        "rows_seen": rows_seen,
        "rows_written": output.num_rows,
        "dropped_trailing_partial": dropped,
        "distinct_minutes": distinct_minutes,
        "max_minute_rows": max_minute_rows,
        "distinct_accounts": data.account_count,
        "pair_minute_events": features.pair_minute_events,
        "timings": timings,
        "started": started,
    }
    return output, stats


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    import os

    import pyarrow.parquet as pq

    args = parse_args(argv)
    if not args.parquet.is_file():
        print(json.dumps({"error": f"parquet file not found: {args.parquet}"}))
        return 2
    if set(FEATURE_COLUMNS) & set(EXCLUDED_COLUMNS):
        print(json.dumps({"error": "allowlist overlaps excluded columns"}))
        return 1

    spill_parent = args.spill_dir or Path(SPILL_PARENT_DEFAULT)
    spill_dir = spill_parent / f"run-{int(time.time())}-{os.getpid()}"
    try:
        output, stats = build(
            args.parquet,
            window_seconds=args.window_seconds,
            limit=args.limit,
            sort_memory=args.sort_memory,
            spill_dir=spill_dir,
        )
        timings: dict[str, float] = stats["timings"]
        phase = time.perf_counter()
        args.features_out.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(output, args.features_out)
        timings["write_seconds"] = time.perf_counter() - phase
        timings["total_seconds"] = time.perf_counter() - stats["started"]
        phase = time.perf_counter()
        source_sha256 = sha256_of(args.parquet)
        timings["source_hash_seconds"] = time.perf_counter() - phase
    except Exception as exc:  # noqa: BLE001 - report build failures as JSON
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1

    rounded = {key: round(value, 3) for key, value in timings.items()}
    peak = peak_rss_mb()
    manifest = {
        "tool": "graph.feature_batch",
        "created_utc": datetime.now(UTC).isoformat(),
        "source": {
            "parquet": str(args.parquet),
            "sha256": source_sha256,
            "rows": stats["source_rows"],
        },
        "columns_read": list(FEATURE_COLUMNS),
        "columns_excluded_proof": {
            "never_selected": list(EXCLUDED_COLUMNS),
            "note": "the DuckDB SELECT projects exactly columns_read; "
                    "target_is_laundering and split never enter the sort, "
                    "features, or logs",
        },
        "window_seconds": args.window_seconds,
        "eligibility_rule": ELIGIBILITY_RULE,
        "ordering": "timestamp ascending, transaction_id ascending; complete same-minute groups",
        "utc_assumption": "naive source timestamps interpreted as UTC for epoch derivation",
        "semantics": dict(SEMANTICS),
        "features": [dict(item) for item in FEATURE_DEFINITIONS],
        "output": {
            "features_parquet": str(args.features_out),
            "rows_written": stats["rows_written"],
            "rows_seen": stats["rows_seen"],
            "dropped_trailing_partial": stats["dropped_trailing_partial"],
            "distinct_minutes": stats["distinct_minutes"],
            "max_minute_rows": stats["max_minute_rows"],
            "distinct_accounts": stats["distinct_accounts"],
            "pair_minute_events": stats["pair_minute_events"],
        },
        "timings": rounded,
        "peak_rss_mb": peak,
    }
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "rows_written": stats["rows_written"],
            "rows_seen": stats["rows_seen"],
            "distinct_minutes": stats["distinct_minutes"],
            "max_minute_rows": stats["max_minute_rows"],
            "dropped_trailing_partial": stats["dropped_trailing_partial"],
            "timings": rounded,
            "peak_rss_mb": peak,
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
