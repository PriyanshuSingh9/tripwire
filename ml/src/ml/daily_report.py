"""Daily prevalence report over the frozen test cohort.

Joins locked-threshold test scores to transaction timestamps and reports
per-day transaction counts, positives, and prevalence for the test split,
plus per-day average precision and threshold recall per model. Day-level
model metrics are computed only for days with at least
``MIN_DAY_POSITIVES`` positives; thinner days report counts with an explicit
reason instead of unstable metrics. All output is aggregate-only.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from ml.common import DATA_VERSION, sha256_of

SCORES_DEFAULT = "artifacts/runs/hi-small-v8/baselines/test_scores.parquet"
CONFIG_DEFAULT = "artifacts/runs/hi-small-v8/baselines/baseline_config.json"
TRANSACTIONS_DEFAULT = "data/processed/hi-small-v8/transactions.parquet"
OUTPUT_DEFAULT = "artifacts/runs/hi-small-v8/baselines/daily_report.json"

MIN_DAY_POSITIVES = 10
MODELS: tuple[str, ...] = ("logreg", "lgbm")


def sql_literal(path: Path) -> str:
    """Render a path as a single-quoted SQL string literal."""
    return "'" + str(path).replace("'", "''") + "'"


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Report daily test-cohort prevalence and day-level metrics."
    )
    parser.add_argument("--scores", type=Path, default=Path(SCORES_DEFAULT))
    parser.add_argument("--config", type=Path, default=Path(CONFIG_DEFAULT))
    parser.add_argument("--transactions", type=Path, default=Path(TRANSACTIONS_DEFAULT))
    parser.add_argument("--output", type=Path, default=Path(OUTPUT_DEFAULT))
    parser.add_argument("--data-version", type=int, default=DATA_VERSION)
    parser.add_argument("--min-day-positives", type=int, default=MIN_DAY_POSITIVES)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    import duckdb
    import numpy as np
    from sklearn.metrics import average_precision_score

    args = build_parser().parse_args(argv)
    if args.min_day_positives < 1:
        print(json.dumps({"error": "--min-day-positives must be positive"}))
        return 2
    for path in (args.scores, args.config, args.transactions):
        if not path.is_file():
            print(json.dumps({"error": f"input not found: {path}"}))
            return 2

    config = json.loads(args.config.read_text(encoding="utf-8"))
    thresholds = {name: float(config["thresholds"][name]) for name in MODELS}

    con = duckdb.connect()
    try:
        con.execute(
            "CREATE OR REPLACE TEMP VIEW dated AS "
            "SELECT CAST(t.timestamp AS DATE) AS day, s.target_is_laundering AS label, "
            "s.logreg_score, s.lgbm_score FROM "
            f"read_parquet({sql_literal(args.scores)}) s "
            "JOIN "
            f"read_parquet({sql_literal(args.transactions)}) t "
            "USING (transaction_id)"
        )
        day_rows = con.execute(
            "SELECT day, COUNT(*) AS transactions, SUM(label) AS positives "
            "FROM dated GROUP BY day ORDER BY day"
        ).fetchall()
        scored_days = con.execute(
            "SELECT CAST(day AS VARCHAR) AS day, label, logreg_score, lgbm_score "
            "FROM dated ORDER BY day"
        ).fetchall()
    finally:
        con.close()

    by_day: dict[str, dict[str, list[float]]] = {}
    for day, label, logreg_score, lgbm_score in scored_days:
        bucket = by_day.setdefault(
            day, {"labels": [], "logreg": [], "lgbm": []}
        )
        bucket["labels"].append(float(label))
        bucket["logreg"].append(float(logreg_score))
        bucket["lgbm"].append(float(lgbm_score))

    days = []
    for day, transactions, positives in day_rows:
        day_key = str(day)
        positives = int(positives)
        entry: dict[str, object] = {
            "date": day_key,
            "transactions": transactions,
            "positives": positives,
            "prevalence": positives / transactions,
            "models": {},
        }
        bucket = by_day[day_key]
        for name in MODELS:
            if positives < args.min_day_positives:
                entry["models"][name] = {
                    "reported": False,
                    "reason": (
                        f"only {positives} positives; "
                        f"minimum is {args.min_day_positives}"
                    ),
                }
                continue
            labels = np.array(bucket["labels"])
            scores = np.array(bucket[name])
            predicted = (scores >= thresholds[name]).astype(int)
            true_positives = int(((predicted == 1) & (labels == 1)).sum())
            entry["models"][name] = {
                "reported": True,
                "average_precision": float(average_precision_score(labels, scores)),
                "recall_at_locked_threshold": true_positives / positives,
                "alerts": int(predicted.sum()),
            }
        days.append(entry)

    report = {
        "data_version": args.data_version,
        "created_utc": datetime.now(UTC).isoformat(),
        "tool": "ml.daily_report",
        "inputs": {
            "scores": str(args.scores),
            "scores_sha256": sha256_of(args.scores),
            "config": str(args.config),
            "config_sha256": sha256_of(args.config),
        },
        "thresholds": thresholds,
        "threshold_origin": "validation-selected; test days never tune thresholds",
        "min_day_positives": args.min_day_positives,
        "days": days,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(args.output),
                "days": [
                    {
                        "date": day["date"],
                        "transactions": day["transactions"],
                        "positives": day["positives"],
                        "models_reported": [
                            name
                            for name, model in day["models"].items()
                            if model["reported"]
                        ],
                    }
                    for day in days
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
