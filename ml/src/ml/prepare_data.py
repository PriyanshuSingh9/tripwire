"""Reproducible HI-Small preparation command.

Streams the source transaction CSV twice with bounded memory (no full-frame
load): the first pass validates fields and accumulates whole-timestamp group
counts; the second pass writes normalized Parquet with frozen chronological
split labels. Output and logs contain aggregates only — never row values.

Label handling: the source ``Is Laundering`` value lands in exactly one
offline column (``target_is_laundering``), recorded as offline-only in the
manifest and excluded from every feature list. ``HI-Small_Patterns.txt`` is
never read here.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ml.common import (
    DATA_VERSION,
    TIMESTAMP_FORMAT,
    TIMESTAMP_OUTPUT_FORMAT,
    TIMESTAMP_RE,
    TRANS_COLUMNS,
    TRANS_FILENAME,
    TRANS_RAW_HEADER,
    read_header,
    require_header,
    sha256_of,
)

DEFAULT_INPUT_DIR = "data/raw/hi-small"
DEFAULT_OUTPUT_DIR = "data/processed/hi-small-v8"
PARQUET_FILENAME = "transactions.parquet"
MANIFEST_FILENAME = "split-manifest.json"

OFFLINE_ONLY_COLUMNS: tuple[str, ...] = ("target_is_laundering",)

PARQUET_SCHEMA_NAMES: tuple[str, ...] = (
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
    "target_is_laundering",
    "source_row_number",
    "split",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the CLI parser and parse arguments."""
    parser = argparse.ArgumentParser(
        description="Normalize HI-Small transactions and freeze a chronological split."
    )
    parser.add_argument("--input-dir", type=Path, default=Path(DEFAULT_INPUT_DIR))
    parser.add_argument("--output-dir", type=Path, default=Path(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--data-version", type=int, default=DATA_VERSION)
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--val-fraction", type=float, default=0.85)
    parser.add_argument("--batch-rows", type=int, default=100_000)
    args = parser.parse_args(argv)
    if not 0.0 < args.train_fraction < args.val_fraction < 1.0:
        parser.error("require 0 < --train-fraction < --val-fraction < 1")
    if args.batch_rows <= 0:
        parser.error("--batch-rows must be positive")
    return args


@contextmanager
def stream_records(path: Path) -> Iterator[Iterator[dict[str, str]]]:
    """Yield normalized record dicts in file order after header validation."""
    handle = path.open("r", encoding="utf-8-sig", newline="")
    try:
        reader = csv.reader(handle)
        require_header(next(reader), TRANS_RAW_HEADER, filename=path.name)
        yield (dict(zip(TRANS_COLUMNS, record)) for record in reader)
    finally:
        handle.close()


def validated_row(row: dict[str, str], *, source_row_number: int) -> dict[str, object]:
    """Validate one raw record and return typed values (raises on violation)."""
    timestamp = row["timestamp"]
    if TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise ValueError(f"malformed timestamp at source row {source_row_number}")
    label = row["is_laundering"]
    if label not in ("0", "1"):
        raise ValueError(f"unexpected label at source row {source_row_number}: {label!r}")
    try:
        amount_paid = float(row["amount_paid"])
        amount_received = float(row["amount_received"])
    except ValueError:
        raise ValueError(f"non-numeric amount at source row {source_row_number}") from None
    if not math.isfinite(amount_paid) or not math.isfinite(amount_received):
        raise ValueError(f"non-finite amount at source row {source_row_number}")
    for column in TRANS_COLUMNS:
        if row[column] == "":
            raise ValueError(
                f"empty {column} at source row {source_row_number} "
                "(all source fields are required in v1)"
            )
    return {
        "timestamp": timestamp,
        "sender_bank": row["sender_bank"],
        "sender_account": row["sender_account"],
        "receiver_bank": row["receiver_bank"],
        "receiver_account": row["receiver_account"],
        "amount_paid": amount_paid,
        "payment_currency": row["payment_currency"],
        "amount_received": amount_received,
        "receiving_currency": row["receiving_currency"],
        "payment_format": row["payment_format"],
        "label": int(label),
    }


def choose_cutoffs(
    ordered: list[str],
    timestamp_counts: Counter[str],
    total: int,
    train_target: float,
    val_target: float,
) -> tuple[str, str]:
    """Pick whole-timestamp cutoffs nearest to the cumulative targets.

    ``ordered`` must be chronologically ascending. Cutoffs are inclusive
    upper bounds: train holds timestamps <= cut1, validation holds
    cut1 < timestamps <= cut2, test holds the rest. ``min`` returns the
    first minimum in chronological iteration order, so ties break toward
    the earlier timestamp deterministically.
    """
    cumulative = 0
    fractions: dict[str, float] = {}
    for timestamp in ordered:
        cumulative += timestamp_counts[timestamp]
        fractions[timestamp] = cumulative / total
    cut1 = min(ordered, key=lambda ts: abs(fractions[ts] - train_target))
    later = ordered[ordered.index(cut1) + 1 :]
    if not later:
        raise ValueError("no timestamp group after the train cutoff")
    cut2 = min(later, key=lambda ts: abs(fractions[ts] - val_target))
    return cut1, cut2


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    args = parse_args(argv)
    source = args.input_dir / TRANS_FILENAME
    if not source.is_file():
        print(json.dumps({"error": f"source file not found: {source}"}))
        return 2

    actual_header = read_header(source)
    try:
        require_header(actual_header, TRANS_RAW_HEADER, filename=source.name)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2

    source_bytes = source.stat().st_size
    source_sha256 = sha256_of(source)

    # Pass 1: validate fields and accumulate whole-timestamp group counts.
    timestamp_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    total_rows = 0
    try:
        with stream_records(source) as records:
            for row in records:
                total_rows += 1
                typed = validated_row(row, source_row_number=total_rows)
                timestamp_counts[typed["timestamp"]] += 1
                label_counts[str(typed["label"])] += 1
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    if total_rows == 0:
        print(json.dumps({"error": "no data rows in source file"}))
        return 1

    # Canonical parsed form proves every group timestamp is a real date and
    # defines chronological order (raw MM/DD/YYYY strings do not sort
    # chronologically across month/year boundaries).
    parsed = {ts: datetime.strptime(ts, TIMESTAMP_FORMAT) for ts in timestamp_counts}
    ordered = sorted(timestamp_counts, key=lambda ts: parsed[ts])
    cut1, cut2 = choose_cutoffs(
        ordered, timestamp_counts, total_rows, args.train_fraction, args.val_fraction
    )
    cut1_dt = parsed[cut1]
    cut2_dt = parsed[cut2]
    split_of = {
        ts: ("train" if parsed[ts] <= cut1_dt else "validation" if parsed[ts] <= cut2_dt else "test")
        for ts in ordered
    }

    # Pass 2: write normalized Parquet in bounded batches with split labels.
    schema = pa.schema(
        [
            ("transaction_id", pa.string()),
            ("timestamp", pa.timestamp("us")),
            ("sender_bank", pa.string()),
            ("sender_account", pa.string()),
            ("receiver_bank", pa.string()),
            ("receiver_account", pa.string()),
            ("sender_account_key", pa.string()),
            ("receiver_account_key", pa.string()),
            ("amount_paid", pa.float64()),
            ("payment_currency", pa.string()),
            ("amount_received", pa.float64()),
            ("receiving_currency", pa.string()),
            ("payment_format", pa.string()),
            ("target_is_laundering", pa.int8()),
            ("source_row_number", pa.int64()),
            ("split", pa.string()),
        ]
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = args.output_dir / PARQUET_FILENAME
    manifest_path = args.output_dir / MANIFEST_FILENAME

    split_counts: Counter[str] = Counter()
    split_label_counts: dict[str, Counter[str]] = {
        "train": Counter(),
        "validation": Counter(),
        "test": Counter(),
    }
    written = 0

    def new_batch() -> dict[str, list[object]]:
        return {name: [] for name in PARQUET_SCHEMA_NAMES}

    try:
        with pq.ParquetWriter(parquet_path, schema) as writer:
            batch = new_batch()
            row_number = 0
            with stream_records(source) as records:
                for row in records:
                    row_number += 1
                    typed = validated_row(row, source_row_number=row_number)
                    timestamp = typed["timestamp"]
                    split = split_of[timestamp]
                    batch["transaction_id"].append(f"hi8-tx-{row_number:08d}")
                    batch["timestamp"].append(parsed[timestamp])
                    batch["sender_bank"].append(typed["sender_bank"])
                    batch["sender_account"].append(typed["sender_account"])
                    batch["receiver_bank"].append(typed["receiver_bank"])
                    batch["receiver_account"].append(typed["receiver_account"])
                    batch["sender_account_key"].append(
                        f"{typed['sender_bank']}::{typed['sender_account']}"
                    )
                    batch["receiver_account_key"].append(
                        f"{typed['receiver_bank']}::{typed['receiver_account']}"
                    )
                    batch["amount_paid"].append(typed["amount_paid"])
                    batch["payment_currency"].append(typed["payment_currency"])
                    batch["amount_received"].append(typed["amount_received"])
                    batch["receiving_currency"].append(typed["receiving_currency"])
                    batch["payment_format"].append(typed["payment_format"])
                    batch["target_is_laundering"].append(typed["label"])
                    batch["source_row_number"].append(row_number)
                    batch["split"].append(split)
                    split_counts[split] += 1
                    split_label_counts[split][str(typed["label"])] += 1
                    if len(batch["transaction_id"]) >= args.batch_rows:
                        writer.write_table(pa.Table.from_pydict(batch, schema=schema))
                        written += len(batch["transaction_id"])
                        batch = new_batch()
            if batch["transaction_id"]:
                writer.write_table(pa.Table.from_pydict(batch, schema=schema))
                written += len(batch["transaction_id"])
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1

    if written != total_rows:
        print(json.dumps({"error": f"row mismatch: pass1={total_rows} written={written}"}))
        return 1

    splits: dict[str, dict[str, object]] = {}
    for split in ("train", "validation", "test"):
        rows = split_counts[split]
        splits[split] = {
            "rows": rows,
            "fraction": rows / total_rows,
            "label_counts": dict(sorted(split_label_counts[split].items())),
        }

    manifest = {
        "data_version": args.data_version,
        "created_utc": datetime.now(UTC).isoformat(),
        "tool": "ml.prepare_data",
        "source_files": [
            {
                "file": TRANS_FILENAME,
                "bytes": source_bytes,
                "sha256": source_sha256,
                "rows": total_rows,
            }
        ],
        "input_header": list(actual_header),
        "normalized_columns": list(PARQUET_SCHEMA_NAMES),
        "schema": {field.name: str(field.type) for field in schema},
        "id_scheme": "hi8-tx-{source_row_number:08d}; stable under the recorded source sha256",
        "key_format": "{bank}::{account} with bank and account preserved as strings",
        "timestamp_input_format": TIMESTAMP_FORMAT,
        "timestamp_resolution": "minute; split groups are whole parsed timestamps",
        "split_algorithm": (
            "cumulative transaction counts over whole-timestamp groups in ascending "
            "order; cutoffs are inclusive upper bounds nearest to the cumulative "
            "targets with ties broken toward the smaller timestamp; validation "
            "cutoff is chosen strictly after the train cutoff"
        ),
        "split_targets": {"train": args.train_fraction, "validation": args.val_fraction},
        "cutoffs": {
            "train_end_inclusive": cut1_dt.strftime(TIMESTAMP_OUTPUT_FORMAT),
            "validation_end_inclusive": cut2_dt.strftime(TIMESTAMP_OUTPUT_FORMAT),
        },
        "splits": splits,
        "totals": {
            "rows": total_rows,
            "label_counts": dict(sorted(label_counts.items())),
        },
        "offline_only_columns": list(OFFLINE_ONLY_COLUMNS),
        "inference_notes": [
            (
                "target_is_laundering is offline-only; it must never appear in "
                "graph properties, inference queries, or feature inputs."
            ),
            "HI-Small_Patterns.txt was never read by this command.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "parquet": str(parquet_path),
                "manifest": str(manifest_path),
                "rows": total_rows,
                "cutoffs": manifest["cutoffs"],
                "splits": splits,
                "label_counts": dict(sorted(label_counts.items())),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
