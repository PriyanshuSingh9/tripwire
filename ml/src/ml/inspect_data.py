"""Reproducible HI-Small dataset inspection command.

Streams the three user-supplied source files with bounded memory and writes an
aggregate-only profile to ``artifacts/runs/hi-small-v8/profile.json``. Output
and logs contain counts, hashes, vocabularies, and ranges only — never row
values. ``HI-Small_Patterns.txt`` is checksummed and counted, never parsed
into features.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ml.common import (
    ACCOUNTS_COLUMNS,
    ACCOUNTS_FILENAME,
    ACCOUNTS_RAW_HEADER,
    DATA_VERSION,
    PATTERNS_FILENAME,
    TIMESTAMP_FORMAT,
    TIMESTAMP_OUTPUT_FORMAT,
    TIMESTAMP_RE,
    TRANS_COLUMNS,
    TRANS_FILENAME,
    TRANS_RAW_HEADER,
    require_header,
    sha256_of,
)

DEFAULT_INPUT_DIR = "data/raw/hi-small"
DEFAULT_OUTPUT = "artifacts/runs/hi-small-v8/profile.json"


def duplicate_and_range_stats(path: Path) -> dict[str, object]:
    """Exact duplicate count and per-currency amount ranges via DuckDB.

    Reads the CSV out-of-core with positional columns (sidestepping the
    duplicate ``Account`` header names) and aggregates without retaining rows
    in Python memory. A duplicate is a row identical in all 11 raw fields;
    the count is the number of excess rows beyond the first per group.
    Amount Paid is ranged per Payment Currency and Amount Received per
    Receiving Currency, keeping the units separate; rows with an empty
    currency are excluded from the ranges (they are already counted in
    ``empty_counts``).
    """
    import duckdb

    escaped = str(path).replace("'", "''")
    columns_sql = ", ".join(f"'c{i}': 'VARCHAR'" for i in range(len(TRANS_RAW_HEADER)))
    con = duckdb.connect()
    try:
        con.execute(
            "CREATE OR REPLACE TEMP VIEW src AS SELECT * FROM read_csv("
            f"'{escaped}', header=false, skip=1, columns={{{columns_sql}}}, "
            "delim=',', quote='\"', escape='\"', nullstr='')"
        )
        group_keys = ", ".join(f"c{i}" for i in range(len(TRANS_RAW_HEADER)))
        duplicates = con.execute(
            "SELECT COALESCE(SUM(c - 1), 0) FROM "
            f"(SELECT COUNT(*) AS c FROM src GROUP BY {group_keys} "
            "HAVING COUNT(*) > 1) t"
        ).fetchone()[0]
        paid_ranges = con.execute(
            "SELECT c8 AS ccy, COUNT(*) AS n, "
            "MIN(TRY_CAST(c7 AS DOUBLE)) AS min_amount, "
            "MAX(TRY_CAST(c7 AS DOUBLE)) AS max_amount "
            "FROM src WHERE c8 IS NOT NULL GROUP BY c8 ORDER BY ccy"
        ).fetchall()
        received_ranges = con.execute(
            "SELECT c6 AS ccy, COUNT(*) AS n, "
            "MIN(TRY_CAST(c5 AS DOUBLE)) AS min_amount, "
            "MAX(TRY_CAST(c5 AS DOUBLE)) AS max_amount "
            "FROM src WHERE c6 IS NOT NULL GROUP BY c6 ORDER BY ccy"
        ).fetchall()
        bad_paid = con.execute(
            "SELECT COUNT(*) FROM src "
            "WHERE c7 IS NOT NULL AND TRY_CAST(c7 AS DOUBLE) IS NULL"
        ).fetchone()[0]
        bad_received = con.execute(
            "SELECT COUNT(*) FROM src "
            "WHERE c5 IS NOT NULL AND TRY_CAST(c5 AS DOUBLE) IS NULL"
        ).fetchone()[0]
    finally:
        con.close()

    def range_map(
        rows: list[tuple[object, object, object, object]],
    ) -> dict[str, dict[str, object]]:
        return {
            str(ccy): {"rows": int(n), "min_amount": min_amount, "max_amount": max_amount}
            for ccy, n, min_amount, max_amount in rows
        }

    return {
        "duplicate_method": (
            "DuckDB out-of-core GROUP BY over all 11 raw source fields; "
            "duplicates = sum(group_size - 1) over groups larger than 1. "
            "Python holds only counters and small vocabularies, never rows."
        ),
        "duplicate_full_rows": int(duplicates),
        "amount_paid_ranges_by_payment_currency": range_map(paid_ranges),
        "amount_received_ranges_by_receiving_currency": range_map(received_ranges),
        "unparseable_amount_paid_rows": int(bad_paid),
        "unparseable_amount_received_rows": int(bad_received),
    }


def inspect_transactions(path: Path) -> dict[str, object]:
    """Stream the transaction CSV and return aggregate metadata only."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        require_header(next(reader), TRANS_RAW_HEADER, filename=path.name)

        rows = 0
        malformed_timestamps = 0
        empty_counts = Counter()
        label_counts: Counter[str] = Counter()
        receiving_currency: Counter[str] = Counter()
        payment_currency: Counter[str] = Counter()
        payment_format: Counter[str] = Counter()
        seen_timestamps: set[str] = set()

        for record in reader:
            rows += 1
            row = dict(zip(TRANS_COLUMNS, record))
            for column in TRANS_COLUMNS:
                if row[column] == "":
                    empty_counts[column] += 1
            timestamp = row["timestamp"]
            if TIMESTAMP_RE.fullmatch(timestamp) is None:
                malformed_timestamps += 1
            else:
                seen_timestamps.add(timestamp)
            label_counts[row["is_laundering"]] += 1
            if row["receiving_currency"]:
                receiving_currency[row["receiving_currency"]] += 1
            if row["payment_currency"]:
                payment_currency[row["payment_currency"]] += 1
            if row["payment_format"]:
                payment_format[row["payment_format"]] += 1

    # Order by calendar components of the YYYY/MM/DD HH:MM shape, never by
    # assumptions about separators.
    def timestamp_key(value: str) -> tuple[str, str, str, str, str]:
        return (value[0:4], value[5:7], value[8:10], value[11:13], value[14:16])

    ts_min = min(seen_timestamps, key=timestamp_key, default=None)
    ts_max = max(seen_timestamps, key=timestamp_key, default=None)

    def iso_format(value: str | None) -> str | None:
        if value is None:
            return None
        return datetime.strptime(value, TIMESTAMP_FORMAT).strftime(TIMESTAMP_OUTPUT_FORMAT)

    return {
        "rows": rows,
        "malformed_timestamp_rows": malformed_timestamps,
        "timestamp_min": iso_format(ts_min),
        "timestamp_max": iso_format(ts_max),
        **duplicate_and_range_stats(path),
        "label_counts": dict(sorted(label_counts.items())),
        "empty_counts": {column: empty_counts.get(column, 0) for column in TRANS_COLUMNS},
        "receiving_currency_counts": dict(sorted(receiving_currency.items())),
        "payment_currency_counts": dict(sorted(payment_currency.items())),
        "payment_format_counts": dict(sorted(payment_format.items())),
    }


def inspect_accounts(path: Path) -> dict[str, object]:
    """Stream the accounts CSV and return aggregate metadata only."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        require_header(next(reader), ACCOUNTS_RAW_HEADER, filename=path.name)

        rows = 0
        empty_counts = Counter()
        bank_ids: set[str] = set()
        account_numbers: set[str] = set()
        bank_account_pairs: set[tuple[str, str]] = set()

        for record in reader:
            rows += 1
            row = dict(zip(ACCOUNTS_COLUMNS, record))
            for column in ACCOUNTS_COLUMNS:
                if row[column] == "":
                    empty_counts[column] += 1
            bank_ids.add(row["bank_id"])
            account_numbers.add(row["account_number"])
            bank_account_pairs.add((row["bank_id"], row["account_number"]))

    return {
        "rows": rows,
        "empty_counts": {column: empty_counts.get(column, 0) for column in ACCOUNTS_COLUMNS},
        "distinct_bank_ids": len(bank_ids),
        "distinct_account_numbers": len(account_numbers),
        "distinct_bank_account_pairs": len(bank_account_pairs),
    }


def inspect_patterns(path: Path) -> dict[str, object]:
    """Checksum, count, and strictly parse the pattern sidecar.

    Accepts only the documented grammar: ``BEGIN LAUNDERING ATTEMPT - <TYP>``
    headers, data rows with at least nine comma-separated fields, matching
    ``END LAUNDERING ATTEMPT`` footers, and blank lines. Any other line
    shape yields ``status: unsupported`` with a line number instead of a
    partial parse. Reported typologies are category labels with attempt and
    row counts only — never transaction records. Pattern rows are not linked
    to transaction IDs here, so coverage of labeled positives is not
    established and missing entries are not negatives.
    """
    import re

    lines = 0
    begin_re = re.compile(r"^BEGIN LAUNDERING ATTEMPT - (.+?)\s*(?::|$)")
    attempts = 0
    attempts_by_typology: Counter[str] = Counter()
    rows_by_typology: Counter[str] = Counter()
    current: str | None = None
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            lines += 1
            stripped = line.strip()
            if not stripped:
                continue
            begin = begin_re.match(stripped)
            if begin:
                if current is not None:
                    return _patterns_unsupported(
                        lines, f"nested BEGIN at line {line_number}"
                    )
                current = begin.group(1).strip()
                attempts += 1
                attempts_by_typology[current] += 1
                continue
            if stripped.startswith("END LAUNDERING ATTEMPT"):
                if current is None:
                    return _patterns_unsupported(
                        lines, f"END without BEGIN at line {line_number}"
                    )
                current = None
                continue
            if current is None:
                return _patterns_unsupported(
                    lines, f"data row outside a block at line {line_number}"
                )
            if len(stripped.split(",")) < 9:
                return _patterns_unsupported(
                    lines, f"short data row at line {line_number}"
                )
            rows_by_typology[current] += 1
    if current is not None:
        return _patterns_unsupported(lines, "file ends inside an open block")
    return {
        "lines": lines,
        "typology_status": "parsed",
        "attempts": attempts,
        "attempts_by_typology": dict(sorted(attempts_by_typology.items())),
        "pattern_rows": sum(rows_by_typology.values()),
        "rows_by_typology": dict(sorted(rows_by_typology.items())),
        "coverage_limitation": (
            "Pattern rows are not linked to transaction IDs; coverage of "
            "labeled positives is not established. Missing entries are not "
            "negative labels."
        ),
    }


def _patterns_unsupported(lines: int, reason: str) -> dict[str, object]:
    """Return the coverage-limitation record when the grammar does not hold."""
    return {
        "lines": lines,
        "typology_status": "unsupported",
        "reason": reason,
        "coverage_limitation": (
            "Typology distribution is not reported because the sidecar does "
            "not follow the expected block grammar. Missing entries are not "
            "negative labels."
        ),
    }


def build_profile(input_dir: Path, data_version: int) -> dict[str, object]:
    """Build the aggregate profile for all three source files."""
    trans_path = input_dir / TRANS_FILENAME
    accounts_path = input_dir / ACCOUNTS_FILENAME
    patterns_path = input_dir / PATTERNS_FILENAME
    for path in (trans_path, accounts_path, patterns_path):
        if not path.is_file():
            raise FileNotFoundError(f"source file not found: {path}")

    trans = inspect_transactions(trans_path)
    trans["file"] = TRANS_FILENAME
    trans["bytes"] = trans_path.stat().st_size
    trans["sha256"] = sha256_of(trans_path)

    accounts = inspect_accounts(accounts_path)
    accounts["file"] = ACCOUNTS_FILENAME
    accounts["bytes"] = accounts_path.stat().st_size
    accounts["sha256"] = sha256_of(accounts_path)

    patterns = inspect_patterns(patterns_path)
    patterns["file"] = PATTERNS_FILENAME
    patterns["bytes"] = patterns_path.stat().st_size
    patterns["sha256"] = sha256_of(patterns_path)

    return {
        "data_version": data_version,
        "created_utc": datetime.now(UTC).isoformat(),
        "tool": "ml.inspect_data",
        "sources": {
            "transactions": trans,
            "accounts": accounts,
            "patterns": patterns,
        },
        "notes": [
            (
                "HI-Small_Patterns.txt is checksummed and counted only; "
                "it is never joined into features."
            ),
            "Labels appear here as aggregate counts only.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Inspect HI-Small sources into an aggregate-only profile."
    )
    parser.add_argument("--input-dir", type=Path, default=Path(DEFAULT_INPUT_DIR))
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument("--data-version", type=int, default=DATA_VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    try:
        profile = build_profile(args.input_dir, args.data_version)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    trans = profile["sources"]["transactions"]
    print(
        json.dumps(
            {
                "profile": str(args.output),
                "transaction_rows": trans["rows"],
                "timestamp_range": [trans["timestamp_min"], trans["timestamp_max"]],
                "malformed_timestamp_rows": trans["malformed_timestamp_rows"],
                "label_counts": trans["label_counts"],
                "account_rows": profile["sources"]["accounts"]["rows"],
                "pattern_lines": profile["sources"]["patterns"]["lines"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
