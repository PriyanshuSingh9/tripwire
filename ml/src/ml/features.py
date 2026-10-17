"""Transaction-level feature builder for the HI-Small v8 benchmark.

Produces one feature row per source transaction in
``artifacts/runs/hi-small-v8/baselines/features.parquet``. Split assignments and
transaction IDs are read from the prepared Parquet and never recomputed.

Features (``FEATURE_COLUMNS`` exactly):
- ``log_amount_paid``, ``log_amount_received`` (log1p of source amounts)
- ``payment_currency``, ``receiving_currency``, ``payment_format``
- ``sender_prior_out``, ``sender_prior_in``, ``receiver_prior_out``,
  ``receiver_prior_in``: strict past-only behavioral counts.

Past-only rule: raw transfers become per-account event rows grouped by
(account key, timestamp) first, so one event row exists per
account/timestamp. A cumulative window ending at 1 PRECEDING then excludes
the whole current-timestamp group, meaning same-minute peers never inform
each other. All aggregation runs out-of-core in DuckDB; Python holds no
transaction rows.

Excluded by construction: target labels, pattern data, and any amount sums
across currencies. This stage projects only feature inputs and split/ID
metadata from the prepared table; the target column is not read or copied.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ml.common import DATA_VERSION, sha256_of

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

SOURCE_PARQUET_DEFAULT = "data/processed/hi-small-v8/transactions.parquet"
SPLIT_MANIFEST_DEFAULT = "data/processed/hi-small-v8/split-manifest.json"
PROFILE_DEFAULT = "artifacts/runs/hi-small-v8/profile.json"
OUTPUT_DIR_DEFAULT = "artifacts/runs/hi-small-v8/baselines"
FEATURES_FILENAME = "features.parquet"
FEATURES_MANIFEST_FILENAME = "features-manifest.json"

FEATURE_COLUMNS: tuple[str, ...] = (
    "log_amount_paid",
    "log_amount_received",
    "payment_currency",
    "receiving_currency",
    "payment_format",
    "sender_prior_out",
    "sender_prior_in",
    "receiver_prior_out",
    "receiver_prior_in",
)

CATEGORICAL_COLUMNS: tuple[str, ...] = (
    "payment_currency",
    "receiving_currency",
    "payment_format",
)

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Build past-only transaction features for the frozen split."
    )
    parser.add_argument("--source-parquet", type=Path, default=Path(SOURCE_PARQUET_DEFAULT))
    parser.add_argument("--split-manifest", type=Path, default=Path(SPLIT_MANIFEST_DEFAULT))
    parser.add_argument("--profile", type=Path, default=Path(PROFILE_DEFAULT))
    parser.add_argument("--output-dir", type=Path, default=Path(OUTPUT_DIR_DEFAULT))
    parser.add_argument("--data-version", type=int, default=DATA_VERSION)
    return parser


def create_prior_views(con: DuckDBPyConnection, tx_view: str) -> None:
    """Build account_events/account_prior views over a tx-like view.

    Shared by the feature job and leakage tests. ``tx_view`` must expose
    ``sender_account_key``, ``receiver_account_key``, and ``timestamp``.
    Events are grouped by (account key, timestamp) first, so one event row
    exists per account/timestamp; the cumulative window ending at 1 PRECEDING
    then excludes the whole current-timestamp group from each account's
    priors, meaning same-minute peers never inform each other while strictly
    earlier (and only earlier) activity is counted.
    """
    con.execute(
        "CREATE OR REPLACE TEMP VIEW account_events AS "
        "SELECT account_key, ts, SUM(out_n) AS out_n, SUM(in_n) AS in_n "
        "FROM ("
        f"SELECT sender_account_key AS account_key, timestamp AS ts, "
        f"1 AS out_n, 0 AS in_n FROM {tx_view} "
        "UNION ALL "
        f"SELECT receiver_account_key AS account_key, timestamp AS ts, "
        f"0 AS out_n, 1 AS in_n FROM {tx_view}"
        ") GROUP BY account_key, ts"
    )
    con.execute(
        "CREATE OR REPLACE TEMP VIEW account_prior AS "
        "SELECT account_key, ts, "
        "COALESCE(SUM(out_n) OVER w, 0)::BIGINT AS prior_out, "
        "COALESCE(SUM(in_n) OVER w, 0)::BIGINT AS prior_in "
        "FROM account_events "
        "WINDOW w AS (PARTITION BY account_key ORDER BY ts "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    import duckdb

    args = build_parser().parse_args(argv)
    for path in (args.source_parquet, args.split_manifest, args.profile):
        if not path.is_file():
            print(json.dumps({"error": f"input not found: {path}"}))
            return 2

    manifest = json.loads(args.split_manifest.read_text(encoding="utf-8"))
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    expected_splits = {name: info["rows"] for name, info in manifest["splits"].items()}
    manifest_source_sha = manifest["source_files"][0]["sha256"]
    if manifest_source_sha != profile["sources"]["transactions"]["sha256"]:
        print(json.dumps({"error": "split manifest and profile disagree on source sha256"}))
        return 1
    con = duckdb.connect()
    try:
        escaped = str(args.source_parquet).replace("'", "''")
        con.execute(
            "CREATE OR REPLACE TEMP VIEW tx AS "
            "SELECT transaction_id, timestamp, sender_account_key, "
            "receiver_account_key, amount_paid, payment_currency, "
            "amount_received, receiving_currency, payment_format, split "
            f"FROM read_parquet('{escaped}')"
        )
        create_prior_views(con, "tx")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        features_path = args.output_dir / FEATURES_FILENAME
        escaped_out = str(features_path).replace("'", "''")
        con.execute(
            "COPY (SELECT "
            "t.transaction_id, t.split, t.timestamp, "
            "LN(1 + t.amount_paid) AS log_amount_paid, "
            "LN(1 + t.amount_received) AS log_amount_received, "
            "t.payment_currency, t.receiving_currency, t.payment_format, "
            "sp.prior_out AS sender_prior_out, sp.prior_in AS sender_prior_in, "
            "rp.prior_out AS receiver_prior_out, rp.prior_in AS receiver_prior_in "
            "FROM tx t "
            "JOIN account_prior sp ON sp.account_key = t.sender_account_key "
            "AND sp.ts = t.timestamp "
            "JOIN account_prior rp ON rp.account_key = t.receiver_account_key "
            "AND rp.ts = t.timestamp"
            f") TO '{escaped_out}' (FORMAT PARQUET)"
        )

        output_columns = {
            row[0]
            for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{escaped_out}')"
            ).fetchall()
        }
        if "target_is_laundering" in output_columns:
            print(json.dumps({"error": "target column leaked into features"}))
            return 1

        # Reconciliation assertions against the frozen manifest.
        counts = {
            row[0]: row[1]
            for row in con.execute(
                "SELECT split, COUNT(*) FROM read_parquet("
                f"'{escaped_out}') GROUP BY split"
            ).fetchall()
        }
        if counts != expected_splits:
            print(json.dumps({"error": f"split counts diverged: {counts}"}))
            return 1
        mismatches = con.execute(
            "SELECT COUNT(*) FROM ("
            f"SELECT transaction_id, split FROM read_parquet('{escaped}') "
            "EXCEPT "
            f"SELECT transaction_id, split FROM read_parquet('{escaped_out}')"
            ") t"
        ).fetchone()[0]
        if mismatches != 0:
            print(json.dumps({"error": f"transaction_id/split mismatch rows: {mismatches}"}))
            return 1
        null_features = con.execute(
            "SELECT COUNT(*) FROM read_parquet("
            f"'{escaped_out}') WHERE "
            + " OR ".join(f"{column} IS NULL" for column in FEATURE_COLUMNS)
        ).fetchone()[0]
        if null_features != 0:
            print(json.dumps({"error": f"null feature values: {null_features}"}))
            return 1
    finally:
        con.close()

    features_manifest = {
        "data_version": args.data_version,
        "created_utc": datetime.now(UTC).isoformat(),
        "tool": "ml.features",
        "source_parquet": str(args.source_parquet),
        "source_sha256": manifest_source_sha,
        "prepared_parquet_sha256": sha256_of(args.source_parquet),
        "split_manifest_sha256": sha256_of(args.split_manifest),
        "profile_sha256": sha256_of(args.profile),
        "feature_columns": list(FEATURE_COLUMNS),
        "categorical_columns": list(CATEGORICAL_COLUMNS),
        "split_counts": counts,
        "method": (
            "Per-account events grouped by (account key, timestamp) first, "
            "then a cumulative window ending at 1 PRECEDING, so the whole "
            "current-timestamp group (including same-minute peers) is excluded "
            "from each transaction's counts. This stage reads no target column; "
            "split labels and transaction IDs are copied from the prepared table."
        ),
        "excluded": [
            "transaction/account IDs and keys",
            "target labels",
            "pattern-file data",
            "amount sums across currencies",
        ],
    }
    manifest_path = args.output_dir / FEATURES_MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(features_manifest, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "features": str(features_path),
                "manifest": str(manifest_path),
                "split_counts": counts,
                "feature_columns": list(FEATURE_COLUMNS),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
