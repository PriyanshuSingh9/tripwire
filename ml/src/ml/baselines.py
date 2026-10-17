"""First comparable transaction-level baselines for HI-Small v8.

Trains LogisticRegression and LightGBM on the identical feature set from
``features.parquet`` and the frozen train split. Mirrors Tracer's separation
into feature-building, baseline training, and machine-readable run artifacts,
without its account split, full-period features, or metric claims.

Protocol: thresholds are chosen on validation only (max-F1), LightGBM
early-stops on validation average precision, and the frozen test split is
evaluated once. Reported metrics: average precision, precision/recall/F1 at
the validation-selected threshold, and precision/recall at a predeclared
0.1% alert budget, plus prevalence and alert counts per split. With
``--graph-features`` an additional ``lgbm_graph`` run trains on the frozen
baseline features plus five past-only graph columns on identical splits;
without the flag the logreg/lgbm pair is produced exactly as before. No
dollar-weighted recall, latency, or cost claims are made here.

Data access uses columnar reads (DuckDB for checks, Polars/NumPy for model
matrices). The fit phase holds train + validation (validation is required
for early stopping and threshold selection); the frozen test split is
collected only after thresholds are locked. pandas is not required.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ml.common import sha256_of
from ml.features import CATEGORICAL_COLUMNS, FEATURE_COLUMNS

if TYPE_CHECKING:
    import numpy as np
    import polars as pl

FEATURES_DEFAULT = "artifacts/runs/hi-small-v8/baselines/features.parquet"
FEATURES_MANIFEST_DEFAULT = "artifacts/runs/hi-small-v8/baselines/features-manifest.json"
OUTPUT_DIR_DEFAULT = "artifacts/runs/hi-small-v8/baselines"
ALERT_BUDGET_DEFAULT = 0.001
LABEL_COLUMN = "target_is_laundering"

#: Graph feature columns produced by graph.feature_stream (int64 counts/flag,
#: keyed by transaction_id). Mirrors the producer schema; validated exactly
#: against the input file. No coordination beyond this contract is needed.
GRAPH_FEATURE_COLUMNS: tuple[str, ...] = (
    "gfeat_sender_out_24h",
    "gfeat_sender_in_24h",
    "gfeat_receiver_out_24h",
    "gfeat_receiver_in_24h",
    "gfeat_prior_reverse_flow",
)

NUMERIC_COLUMNS: tuple[str, ...] = (
    "log_amount_paid",
    "log_amount_received",
    "sender_prior_out",
    "sender_prior_in",
    "receiver_prior_out",
    "receiver_prior_in",
)

LOGREG_PARAMS: dict[str, object] = {"C": 1.0, "max_iter": 1000}
LGBM_PARAMS: dict[str, object] = {
    "n_estimators": 2000,
    "learning_rate": 0.05,
    "num_leaves": 127,
    "min_child_samples": 100,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "random_state": 42,
    "data_random_seed": 42,
    "bagging_seed": 42,
    "feature_fraction_seed": 42,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 4,
    "verbosity": -1,
}
EARLY_STOPPING_ROUNDS = 200
CATEGORICAL_INDICES: tuple[int, ...] = (6, 7, 8)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Train logreg and LightGBM baselines on frozen features."
    )
    parser.add_argument("--features", type=Path, default=Path(FEATURES_DEFAULT))
    parser.add_argument(
        "--features-manifest", type=Path, default=Path(FEATURES_MANIFEST_DEFAULT)
    )
    parser.add_argument("--output-dir", type=Path, default=Path(OUTPUT_DIR_DEFAULT))
    parser.add_argument("--alert-budget", type=float, default=ALERT_BUDGET_DEFAULT)
    parser.add_argument(
        "--graph-features",
        type=Path,
        default=None,
        help="Optional target-free graph feature Parquet keyed by transaction_id; "
        "adds a comparable lgbm_graph run. Omit for the frozen baseline pair.",
    )
    return parser


def average_precision(y_true: object, scores: object) -> float:
    """Thin wrapper keeping metric imports local to one place."""
    from sklearn.metrics import average_precision_score

    return float(average_precision_score(y_true, scores))


def select_threshold_max_f1(scores: np.ndarray, labels: np.ndarray) -> float:
    """Validation threshold rule: score cut with maximum F1 on given labels.

    Selects a variable-size alert set (every score at or above the cut).
    Shared by the training job and threshold-semantics tests.
    """
    import numpy as np
    from sklearn.metrics import precision_recall_curve

    precision, recall, thresholds = precision_recall_curve(labels, scores)
    f1 = 2 * precision * recall / np.clip(precision + recall, 1e-9, None)
    return float(thresholds[int(np.argmax(f1[:-1]))])


def select_top_k(scores: np.ndarray, k: int) -> np.ndarray:
    """Fixed-capacity rule: indices of the top-k scores in stable order.

    Always returns exactly k alerts regardless of score values, which is a
    distinct semantic from the variable-size threshold rule above.
    """
    import numpy as np

    order = np.argsort(-scores, kind="stable")
    return order[: max(k, 0)]


def package_versions(*names: str) -> dict[str, str]:
    """Return installed versions for the named distributions."""
    from importlib.metadata import version

    return {name: version(name) for name in names}


def validate_graph_features(path: Path) -> dict[str, object]:
    """Validate the graph feature file; fail closed. Returns file metadata.

    The schema must be exactly ``transaction_id`` plus the five ``gfeat_*``
    integer columns: any extra column (labels, splits, typology) or any
    missing/wrongly-typed column is rejected. Duplicate IDs, null values,
    and out-of-domain reverse-flow flags are rejected via out-of-core
    checks, so no full table is materialized in Python.
    """
    import pyarrow.parquet as pq

    if not path.is_file():
        raise FileNotFoundError(f"graph feature file not found: {path}")
    schema = pq.read_schema(path)
    names = schema.names
    expected = ["transaction_id", *GRAPH_FEATURE_COLUMNS]
    if set(names) != set(expected) or names[0] != "transaction_id":
        raise ValueError(f"graph feature schema mismatch: {names}")
    import pyarrow as pa

    for name in GRAPH_FEATURE_COLUMNS:
        if schema.field(name).type != pa.int64():
            raise ValueError(f"graph feature column not int64: {name}")

    import duckdb

    literal = "'" + str(path).replace("'", "''") + "'"
    con = duckdb.connect()
    try:
        total, distinct = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT transaction_id) FROM read_parquet({literal})"
        ).fetchone()
        if total != distinct:
            raise ValueError("duplicate transaction_id values in graph features")
        nulls = con.execute(
            "SELECT "
            + " + ".join(
                f"SUM(CASE WHEN {name} IS NULL THEN 1 ELSE 0 END)"
                for name in GRAPH_FEATURE_COLUMNS
            )
            + f" FROM read_parquet({literal})"
        ).fetchone()[0]
        if nulls:
            raise ValueError("null graph feature values present")
        flag_min, flag_max = con.execute(
            "SELECT MIN(gfeat_prior_reverse_flow), MAX(gfeat_prior_reverse_flow) "
            f"FROM read_parquet({literal})"
        ).fetchone()
        if flag_min not in (0, 1) or flag_max not in (0, 1):
            raise ValueError("gfeat_prior_reverse_flow outside {0, 1}")
    finally:
        con.close()

    from ml.common import sha256_of

    return {
        "path": str(path),
        "sha256": sha256_of(path),
        "rows": int(total),
        "columns": expected,
    }


def check_joined_height(joined_height: int, expected_height: int, split: str) -> None:
    """Raise unless a graph join preserved every feature row of a split."""
    if joined_height != expected_height:
        raise ValueError(
            f"graph features missing IDs for {split}: "
            f"{joined_height} joined vs {expected_height} expected"
        )


def join_graph_features(feature_lazy: object, graph_lazy: object) -> object:
    """Inner-join graph features by ID; duplicates fail the 1:1 validation."""
    try:
        return feature_lazy.join(
            graph_lazy, on="transaction_id", how="inner", validate="1:1"
        )
    except Exception as exc:
        raise ValueError(f"graph ID join failed: {type(exc).__name__}") from exc


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    import joblib
    import numpy as np
    import polars as pl
    from scipy.sparse import hstack
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    args = build_parser().parse_args(argv)
    if not 0.0 < args.alert_budget < 1.0:
        print(json.dumps({"error": "--alert-budget must be in (0, 1)"}))
        return 2
    for path in (args.features, args.features_manifest):
        if not path.is_file():
            print(json.dumps({"error": f"input not found: {path}"}))
            return 2

    features_manifest = json.loads(args.features_manifest.read_text(encoding="utf-8"))
    source_parquet = Path(features_manifest["source_parquet"])
    if not source_parquet.is_file():
        print(json.dumps({"error": f"label source not found: {source_parquet}"}))
        return 2
    if sha256_of(source_parquet) != features_manifest["prepared_parquet_sha256"]:
        print(json.dumps({"error": "prepared data changed after feature generation"}))
        return 1
    if features_manifest["feature_columns"] != list(FEATURE_COLUMNS):
        print(json.dumps({"error": "feature list drifted from ml.features"}))
        return 1
    if LABEL_COLUMN in FEATURE_COLUMNS:
        print(json.dumps({"error": "target column present in feature list"}))
        return 1

    graph_meta: dict[str, object] | None = None
    if args.graph_features is not None:
        try:
            graph_meta = validate_graph_features(args.graph_features)
        except FileNotFoundError as exc:
            print(json.dumps({"error": str(exc)}))
            return 2
        except ValueError as exc:
            print(json.dumps({"error": str(exc)}))
            return 1

    feature_lazy = pl.scan_parquet(
        args.features,
        extra_columns="ignore",
    ).select(["transaction_id", "split", *FEATURE_COLUMNS])
    label_lazy = pl.scan_parquet(source_parquet).select(
        ["transaction_id", "split", LABEL_COLUMN]
    )
    # Split and target come only from the offline feature table; the graph
    # file contributes structural columns joined by ID, nothing else.
    graph_lazy = (
        pl.scan_parquet(args.graph_features).select(
            ["transaction_id", *GRAPH_FEATURE_COLUMNS]
        )
        if graph_meta is not None
        else None
    )
    actual_counts = {
        row["split"]: row["len"]
        for row in feature_lazy.group_by("split").len().collect().to_dicts()
    }
    if actual_counts != features_manifest["split_counts"]:
        print(json.dumps({"error": f"split counts diverged: {actual_counts}"}))
        return 1

    def collect_labeled_split(split: str) -> pl.DataFrame:
        """Join offline labels (and optional graph features) by ID only."""
        joined = feature_lazy.filter(pl.col("split") == split).join(
            label_lazy.filter(pl.col("split") == split),
            on=["transaction_id", "split"],
            how="inner",
            validate="1:1",
        )
        if graph_lazy is not None:
            joined = join_graph_features(joined, graph_lazy)
        frame = joined.sort("transaction_id").collect(engine="streaming")
        if frame.height != actual_counts[split] or frame[LABEL_COLUMN].null_count():
            raise ValueError(f"feature/label rows do not reconcile for {split}")
        return frame

    # Fit phase holds train + validation only: validation drives early
    # stopping and threshold selection. The frozen test split is collected
    # afterwards, once thresholds are locked.
    fit_splits = ("train", "validation")
    fit_frames = {split: collect_labeled_split(split) for split in fit_splits}
    y_fit = {
        split: fit_frames[split][LABEL_COLUMN].to_numpy().astype(np.int64)
        for split in fit_splits
    }
    numeric_fit = {
        split: fit_frames[split].select(list(NUMERIC_COLUMNS)).to_numpy().astype(float)
        for split in fit_splits
    }

    # Integer-code categoricals with a dedicated unknown bucket, mapped from
    # train-observed values only so validation/test stay in the same space.
    mappings: dict[str, dict[str, int]] = {}
    for column in CATEGORICAL_COLUMNS:
        uniques = sorted(fit_frames["train"][column].unique().to_list())
        mappings[column] = {value: code for code, value in enumerate(uniques)}
    category_counts = {column: len(mapping) + 1 for column, mapping in mappings.items()}

    def encode_codes(frame: pl.DataFrame) -> np.ndarray:
        return np.column_stack(
            [
                np.array(
                    frame[column]
                    .replace_strict(mappings[column], default=len(mappings[column]))
                    .to_list(),
                    dtype=np.int64,
                )
                for column in CATEGORICAL_COLUMNS
            ]
        )

    codes_fit = {split: encode_codes(fit_frames[split]) for split in fit_splits}
    # Graph block appends after the categorical codes so indices [6,7,8]
    # keep pointing at the baseline categoricals in every dense layout.
    graph_fit = (
        {
            split: fit_frames[split]
            .select(list(GRAPH_FEATURE_COLUMNS))
            .to_numpy()
            .astype(float)
            for split in fit_splits
        }
        if graph_meta is not None
        else None
    )
    del fit_frames

    # Shared encoding: scaled numerics plus sparse one-hot categoricals.
    scaler = StandardScaler()
    scaled_fit = {
        split: (
            scaler.fit_transform(numeric_fit[split])
            if split == "train"
            else scaler.transform(numeric_fit[split])
        )
        for split in fit_splits
    }
    encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=True)
    encoder.fit(codes_fit["train"])
    sparse_fit = {
        split: hstack([scaled_fit[split], encoder.transform(codes_fit[split])]).tocsr()
        for split in fit_splits
    }
    del scaled_fit

    # Logistic regression baseline.
    logreg = LogisticRegression(**LOGREG_PARAMS)
    logreg.fit(sparse_fit["train"], y_fit["train"])
    logreg_scores_fit = {
        split: np.asarray(logreg.decision_function(sparse_fit[split]), dtype=float)
        for split in fit_splits
    }

    # LightGBM baseline on dense numerics plus integer-coded categoricals.
    # The graph arm appends its block after the codes, so positions [6,7,8]
    # keep pointing at the baseline categoricals in every dense layout.
    dense_fit = {
        split: np.hstack([numeric_fit[split], codes_fit[split].astype(float)])
        for split in fit_splits
    }
    dense_graph_fit: dict[str, np.ndarray] | None = None
    if graph_fit is not None:
        dense_graph_fit = {
            split: np.hstack([dense_fit[split], graph_fit[split]])
            for split in fit_splits
        }
    del numeric_fit, codes_fit, graph_fit

    def train_lgbm(
        X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray, y_val: np.ndarray
    ):  # -> lgb.LGBMClassifier
        import lightgbm as lgb

        model = lgb.LGBMClassifier(**LGBM_PARAMS)
        model.fit(
            X_train,
            y_train,
            eval_set=[(X_val, y_val)],
            eval_metric="average_precision",
            categorical_feature=list(CATEGORICAL_INDICES),
            callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
        )
        return model

    lgbm = train_lgbm(
        dense_fit["train"], y_fit["train"], dense_fit["validation"], y_fit["validation"]
    )
    lgbm_scores_fit = {
        split: np.asarray(lgbm.predict_proba(dense_fit[split])[:, 1], dtype=float)
        for split in fit_splits
    }
    lgbm_graph_scores_fit: dict[str, np.ndarray] | None = None
    lgbm_graph = None
    if dense_graph_fit is not None:
        lgbm_graph = train_lgbm(
            dense_graph_fit["train"],
            y_fit["train"],
            dense_graph_fit["validation"],
            y_fit["validation"],
        )
        lgbm_graph_scores_fit = {
            split: np.asarray(lgbm_graph.predict_proba(dense_graph_fit[split])[:, 1], dtype=float)
            for split in fit_splits
        }
    del dense_fit

    # Thresholds chosen on validation only (max-F1 per model).
    thresholds = {
        "logreg": select_threshold_max_f1(logreg_scores_fit["validation"], y_fit["validation"]),
        "lgbm": select_threshold_max_f1(lgbm_scores_fit["validation"], y_fit["validation"]),
    }
    if lgbm_graph_scores_fit is not None:
        thresholds["lgbm_graph"] = select_threshold_max_f1(
            lgbm_graph_scores_fit["validation"], y_fit["validation"]
        )

    scored_runs: list[tuple[str, dict[str, np.ndarray]]] = [
        ("logreg", logreg_scores_fit),
        ("lgbm", lgbm_scores_fit),
    ]
    if lgbm_graph_scores_fit is not None:
        scored_runs.append(("lgbm_graph", lgbm_graph_scores_fit))

    metrics: dict[str, dict[str, object]] = {}
    for name, scores_fit in scored_runs:
        threshold = thresholds[name]
        model_metrics: dict[str, object] = {
            "average_precision": {
                split: average_precision(y_fit[split], scores_fit[split])
                for split in fit_splits
            },
            "val_threshold_max_f1": threshold,
        }
        split_reports = {}
        for split in fit_splits:
            predicted = (scores_fit[split] >= threshold).astype(int)
            predicted_positives = int(predicted.sum())
            actual_positives = int(y_fit[split].sum())
            true_positives = int(((predicted == 1) & (y_fit[split] == 1)).sum())
            split_reports[split] = {
                "rows": len(y_fit[split]),
                "prevalence": actual_positives / len(y_fit[split]),
                "alerts": predicted_positives,
                "precision": true_positives / max(predicted_positives, 1),
                "recall": true_positives / max(actual_positives, 1),
            }
        model_metrics["splits"] = split_reports
        metrics[name] = model_metrics
    del sparse_fit, y_fit

    # Test phase: the frozen test split is loaded only now that thresholds
    # are locked. At 761k rows full-batch inference fits comfortably; batch
    # the scan below if a future test cohort outgrows memory.
    test_frame = collect_labeled_split("test")
    y_test = test_frame[LABEL_COLUMN].to_numpy().astype(np.int64)
    test_ids = test_frame["transaction_id"].to_list()
    numeric_test = test_frame.select(list(NUMERIC_COLUMNS)).to_numpy().astype(float)
    codes_test = encode_codes(test_frame)
    graph_test = (
        test_frame.select(list(GRAPH_FEATURE_COLUMNS)).to_numpy().astype(float)
        if graph_meta is not None
        else None
    )
    del test_frame
    sparse_test = hstack([scaler.transform(numeric_test), encoder.transform(codes_test)]).tocsr()
    dense_test = np.hstack([numeric_test, codes_test.astype(float)])
    test_scores = {
        "logreg": np.asarray(logreg.decision_function(sparse_test), dtype=float),
        "lgbm": np.asarray(lgbm.predict_proba(dense_test)[:, 1], dtype=float),
    }
    if graph_test is not None and lgbm_graph is not None:
        dense_graph_test = np.hstack([dense_test, graph_test])
        test_scores["lgbm_graph"] = np.asarray(
            lgbm_graph.predict_proba(dense_graph_test)[:, 1], dtype=float
        )
        del dense_graph_test
    del sparse_test, dense_test, numeric_test, codes_test, graph_test

    for name in [run_name for run_name, _ in scored_runs]:
        scores = test_scores[name]
        threshold = thresholds[name]
        metrics[name]["average_precision"]["test"] = average_precision(y_test, scores)
        test_predicted = (scores >= threshold).astype(int)
        tp = int(((test_predicted == 1) & (y_test == 1)).sum())
        fp = int(((test_predicted == 1) & (y_test == 0)).sum())
        fn = int(((test_predicted == 0) & (y_test == 1)).sum())
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        metrics[name]["test_at_val_threshold"] = {
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / max(precision + recall, 1e-9),
            "true_positives": tp,
            "false_positives": fp,
        }
        # Predeclared alert budget: top-budget fraction of test scores.
        top = select_top_k(scores, max(int(np.ceil(args.alert_budget * len(y_test))), 1))
        budget_k = len(top)
        budget_tp = int(y_test[top].sum())
        metrics[name]["test_at_alert_budget"] = {
            "budget": args.alert_budget,
            "alerts": budget_k,
            "true_positives": budget_tp,
            "precision": budget_tp / budget_k,
            "recall": budget_tp / max(int(y_test.sum()), 1),
        }
        metrics[name]["splits"]["test"] = {
            "rows": len(y_test),
            "prevalence": int(y_test.sum()) / len(y_test),
            "alerts": int(test_predicted.sum()),
            "precision": precision,
            "recall": recall,
        }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    models_dir = args.output_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    joblib.dump(
        {
            "scaler": scaler,
            "encoder": encoder,
            "mappings": mappings,
            "model": logreg,
            "params": LOGREG_PARAMS,
        },
        models_dir / "logreg.joblib",
    )
    lgbm.booster_.save_model(str(models_dir / "lgbm.txt"))
    if lgbm_graph is not None:
        lgbm_graph.booster_.save_model(str(models_dir / "lgbm_graph.txt"))

    import pyarrow as pa
    import pyarrow.parquet as pq

    score_columns: dict[str, list[object]] = {
        "transaction_id": test_ids,
        LABEL_COLUMN: [int(v) for v in y_test],
        "logreg_score": [float(v) for v in test_scores["logreg"]],
        "lgbm_score": [float(v) for v in test_scores["lgbm"]],
    }
    if "lgbm_graph" in test_scores:
        score_columns["lgbm_graph_score"] = [
            float(v) for v in test_scores["lgbm_graph"]
        ]
    scores_table = pa.table(score_columns)
    scores_path = args.output_dir / "test_scores.parquet"
    pq.write_table(scores_table, scores_path)

    config = {
        "created_utc": datetime.now(UTC).isoformat(),
        "tool": "ml.baselines",
        "features_manifest": str(args.features_manifest),
        "feature_columns": list(FEATURE_COLUMNS),
        "categorical_columns": list(CATEGORICAL_COLUMNS),
        "category_counts_including_unknown": category_counts,
        "categorical_feature_indices": list(CATEGORICAL_INDICES),
        "dense_layout": (
            "baseline numerics (6) + categorical codes (3) at indices [6,7,8]"
            + (
                f" + graph numerics ({len(GRAPH_FEATURE_COLUMNS)})"
                if graph_meta is not None
                else ""
            )
        ),
        "graph_feature_columns": list(GRAPH_FEATURE_COLUMNS),
        "graph_features": graph_meta,
        "label_column": LABEL_COLUMN,
        "source_sha256": features_manifest["source_sha256"],
        "prepared_parquet_sha256": features_manifest["prepared_parquet_sha256"],
        "split_manifest_sha256": features_manifest["split_manifest_sha256"],
        "features_manifest_sha256": sha256_of(args.features_manifest),
        "logreg_params": LOGREG_PARAMS,
        "lgbm_params": LGBM_PARAMS,
        "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
        "determinism": {
            "joined_splits_sorted_by": "transaction_id",
            "cpu_mode": "deterministic=True, force_row_wise=True",
            "note": "Fixed seeds and thread count; reruns on the same "
            "machine and locked dependencies must reproduce predictions exactly.",
        },
        "package_versions": package_versions(
            "lightgbm", "scikit-learn", "numpy", "polars", "pyarrow", "duckdb"
        ),
        "threshold_rule": "max-F1 on validation scores, applied unchanged to test",
        "alert_budget": args.alert_budget,
        "thresholds": thresholds,
        "deferred": ["dollar-weighted recall", "latency measurement", "cost claims"],
    }
    config_path = args.output_dir / "baseline_config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    metrics_path = args.output_dir / "baseline_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "config": str(config_path),
                "metrics": str(metrics_path),
                "models": [
                    str(models_dir / "logreg.joblib"),
                    str(models_dir / "lgbm.txt"),
                    *(
                        [str(models_dir / "lgbm_graph.txt")]
                        if lgbm_graph is not None
                        else []
                    ),
                ],
                "test_scores": str(scores_path),
                "thresholds": thresholds,
                "summary": {
                    name: {
                        "test_ap": model["average_precision"]["test"],
                        "test_at_val_threshold": model["test_at_val_threshold"],
                        "test_at_alert_budget": model["test_at_alert_budget"],
                    }
                    for name, model in metrics.items()
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
