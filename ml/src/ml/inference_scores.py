"""Publish a target-free score table for the held-out replay API."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from ml.common import sha256_of

SCORES_DEFAULT = "artifacts/runs/hi-small-v8/baselines/test_scores.parquet"
FEATURES_DEFAULT = "artifacts/runs/hi-small-v8/baselines/features.parquet"
OUTPUT_DEFAULT = "artifacts/runs/hi-small-v8/baselines/inference_scores.parquet"
MANIFEST_DEFAULT = "artifacts/runs/hi-small-v8/baselines/inference_scores-manifest.json"


def _sql_path(path: Path) -> str:
    """Return a safely quoted local path for a DuckDB read/write statement."""
    return str(path).replace("'", "''")


def build_parser() -> argparse.ArgumentParser:
    """Build the target-free score publication CLI."""
    parser = argparse.ArgumentParser(
        description=(
            "Publish only transaction ID, timestamp, and LightGBM risk score "
            "for the held-out replay API; never project evaluation labels."
        )
    )
    parser.add_argument("--scores", type=Path, default=Path(SCORES_DEFAULT))
    parser.add_argument("--features", type=Path, default=Path(FEATURES_DEFAULT))
    parser.add_argument("--output", type=Path, default=Path(OUTPUT_DEFAULT))
    parser.add_argument("--manifest", type=Path, default=Path(MANIFEST_DEFAULT))
    return parser


def publish_scores(
    scores: Path, features: Path, output: Path, *, score_column: str = "lgbm_score"
) -> int:
    """Write a label-free score artifact after ID reconciliation.

    ``score_column`` names the model score in ``scores``; only that column is
    projected (as ``score``), so evaluation labels are never copied.
    """
    if not score_column.replace("_", "").isalnum():
        raise ValueError(f"invalid score column name: {score_column}")
    import duckdb

    for path in (scores, features):
        if not path.is_file():
            raise FileNotFoundError(f"input not found: {path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    scores_sql = _sql_path(scores)
    features_sql = _sql_path(features)
    output_sql = _sql_path(output)
    con = duckdb.connect()
    try:
        con.execute(
            "CREATE TEMP VIEW api_scores AS "
            f"SELECT transaction_id, {score_column} AS score "
            f"FROM read_parquet('{scores_sql}')"
        )
        con.execute(
            "CREATE TEMP VIEW test_timestamps AS "
            "SELECT transaction_id, timestamp FROM read_parquet('"
            f"{features_sql}') WHERE split = 'test'"
        )
        counts = con.execute(
            "SELECT (SELECT COUNT(*) FROM api_scores), "
            "(SELECT COUNT(*) FROM test_timestamps), "
            "(SELECT COUNT(*) FROM api_scores s JOIN test_timestamps f USING (transaction_id))"
        ).fetchone()
        score_rows, feature_rows, joined_rows = map(int, counts)
        if score_rows != feature_rows or joined_rows != score_rows:
            raise ValueError(
                "score/test-feature rows do not reconcile "
                f"(scores={score_rows}, features={feature_rows}, joined={joined_rows})"
            )
        con.execute(
            "COPY (SELECT s.transaction_id, f.timestamp, s.score "
            "FROM api_scores s JOIN test_timestamps f USING (transaction_id)) "
            f"TO '{output_sql}' (FORMAT PARQUET)"
        )
        columns = {
            row[0]
            for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{output_sql}')"
            ).fetchall()
        }
        if columns != {"transaction_id", "timestamp", "score"}:
            raise ValueError(f"unexpected inference score schema: {sorted(columns)}")
    finally:
        con.close()
    return score_rows


def main(argv: list[str] | None = None) -> int:
    """Run the score publication job and print aggregate metadata only."""
    args = build_parser().parse_args(argv)
    try:
        rows = publish_scores(args.scores, args.features, args.output)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    manifest = {
        "created_utc": datetime.now(UTC).isoformat(),
        "tool": "ml.inference_scores",
        "rows": rows,
        "output": str(args.output),
        "scores_sha256": sha256_of(args.scores),
        "features_sha256": sha256_of(args.features),
        "output_sha256": sha256_of(args.output),
        "columns": ["transaction_id", "timestamp", "score"],
        "labels_read": False,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
