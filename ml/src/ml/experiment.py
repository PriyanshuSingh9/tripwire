"""Iteration 2 detection experiment runner (``traceaml-experiment``).

Subcommands:

- ``features``: build every point-in-time feature family from
  :mod:`ml.feature_families` over a target-free projection of the prepared
  table and write ``features.parquet`` plus a manifest with family columns,
  build timings, serving-state notes, and input hashes.
- ``validate``: train LightGBM configurations on train, early-stop and score
  on validation, and append one record per configuration to
  ``validation_runs.jsonl``. Never reads test labels.
- ``test``: train one pre-declared candidate exactly as in ``validate``, lock
  its validation max-F1 threshold, then evaluate the frozen test cohort once
  and append the result to ``test_ledger.jsonl``. The candidate must appear in
  the declared candidate manifest with a matching config hash, and the runner
  fails closed rather than overwrite an existing test artifact or append a
  second ledger row for the same candidate and experiment id.
- ``publish``: write target-free inference scores (transaction_id,
  timestamp, score) for a tested candidate plus a manifest.

Protocol: features use only strictly earlier rows; feature selection,
hyperparameters, and thresholds are chosen on train + validation only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ml.common import DATA_VERSION, sha256_of
from ml.feature_families import (
    ACCOUNTS_VIEW,
    FAMILIES,
    TX_INPUT_COLUMNS,
    build_families,
    categorical_columns,
    feature_columns,
)

if TYPE_CHECKING:
    import numpy as np

LABEL_COLUMN = "target_is_laundering"
ALL_FAMILIES: tuple[str, ...] = tuple(FAMILIES)
ALERT_BUDGET = 0.001
#: Test bulk window: the low-prevalence Sept 9-10 days (calendar dates).
BULK_DAYS: tuple[str, ...] = ("2022-09-09", "2022-09-10")
FEATURES_FILENAME = "features.parquet"
FEATURES_MANIFEST_FILENAME = "features-manifest.json"
VALIDATION_LEDGER = "validation_runs.jsonl"
TEST_LEDGER = "test_ledger.jsonl"
#: Committed manifest of pre-declared test candidates (name plus config hash).
DEFAULT_CANDIDATES_MANIFEST = "experiments/iteration2_candidates.json"


def sql_literal(path: Path) -> str:
    """Render a path as a single-quoted SQL string literal."""
    return "'" + str(path).replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------


def run_features(args: argparse.Namespace) -> int:
    """Build all requested families and write the feature table + manifest."""
    import duckdb

    for path in (args.source_parquet, args.split_manifest):
        if not path.is_file():
            print(json.dumps({"error": f"input not found: {path}"}))
            return 2
    families = list(args.families or ALL_FAMILIES)
    if any(FAMILIES[name].needs_accounts for name in families if name in FAMILIES) and (
        args.accounts_csv is None or not args.accounts_csv.is_file()
    ):
        print(json.dumps({"error": "kyc family requires --accounts-csv"}))
        return 2
    manifest = json.loads(args.split_manifest.read_text(encoding="utf-8"))
    expected_splits = {name: info["rows"] for name, info in manifest["splits"].items()}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = args.output_dir / "duckdb-tmp"
    con = duckdb.connect()
    timings: dict[str, float] = {}
    total_start = time.perf_counter()
    try:
        con.execute(f"SET memory_limit='{args.memory_limit}'")
        con.execute(f"SET threads={int(args.threads)}")
        con.execute(f"SET temp_directory={sql_literal(temp_dir)}")
        con.execute("SET preserve_insertion_order=false")
        source = sql_literal(args.source_parquet)
        # Target-free projection: the builder only ever sees these columns.
        con.execute(
            "CREATE OR REPLACE TEMP TABLE tx_input AS SELECT "
            + ", ".join(TX_INPUT_COLUMNS)
            + f" FROM read_parquet({source})"
        )
        if args.accounts_csv is not None and args.accounts_csv.is_file():
            con.execute(
                f"CREATE OR REPLACE TEMP VIEW {ACCOUNTS_VIEW} AS SELECT "
                '"Bank ID" AS bank_id, "Account Number" AS account_number, '
                '"Entity ID" AS entity_id, '
                "split_part(\"Entity Name\", ' #', 1) AS entity_type "
                f"FROM read_csv({sql_literal(args.accounts_csv)}, all_varchar=true, header=true)"
            )
        joined = build_families(con, "tx_input", families, timings=timings)
        features_path = args.output_dir / FEATURES_FILENAME
        start = time.perf_counter()
        con.execute(
            "COPY (SELECT f.*, s.split, s.timestamp FROM "
            f"{joined} f JOIN (SELECT transaction_id, split, timestamp FROM "
            f"read_parquet({source})) s USING (transaction_id) ORDER BY transaction_id) "
            f"TO {sql_literal(features_path)} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        timings["write"] = time.perf_counter() - start
        out = sql_literal(features_path)
        columns = [
            row[0] for row in con.execute(f"DESCRIBE SELECT * FROM read_parquet({out})").fetchall()
        ]
        if LABEL_COLUMN in columns:
            print(json.dumps({"error": "target column leaked into features"}))
            return 1
        counts = {
            row[0]: row[1]
            for row in con.execute(
                f"SELECT split, COUNT(*) FROM read_parquet({out}) GROUP BY split"
            ).fetchall()
        }
        if counts != expected_splits:
            print(json.dumps({"error": f"split counts diverged: {counts}"}))
            return 1
        null_counts = {
            column: int(
                con.execute(
                    f"SELECT COUNT(*) - COUNT({column}) FROM read_parquet({out})"
                ).fetchone()[0]
            )
            for column in feature_columns(families)
        }
    finally:
        con.close()
    total_seconds = time.perf_counter() - total_start

    features_manifest = {
        "data_version": args.data_version,
        "created_utc": datetime.now(UTC).isoformat(),
        "tool": "ml.experiment features",
        "source_parquet": str(args.source_parquet),
        "prepared_parquet_sha256": sha256_of(args.source_parquet),
        "split_manifest_sha256": sha256_of(args.split_manifest),
        "accounts_csv": str(args.accounts_csv) if args.accounts_csv else None,
        "accounts_csv_sha256": (
            sha256_of(args.accounts_csv)
            if args.accounts_csv is not None and args.accounts_csv.is_file()
            else None
        ),
        "families": {
            name: {
                "columns": list(FAMILIES[name].columns),
                "categorical": list(FAMILIES[name].categorical),
                "serving_state": FAMILIES[name].serving_state,
                "notes": list(FAMILIES[name].notes),
                "build_seconds": round(timings.get(name, 0.0), 2),
            }
            for name in families
        },
        "feature_columns": feature_columns(families),
        "categorical_columns": categorical_columns(families),
        "null_counts": null_counts,
        "split_counts": counts,
        "timings_seconds": {key: round(value, 2) for key, value in timings.items()},
        "total_build_seconds": round(total_seconds, 2),
        "engine": {
            "duckdb_threads": args.threads,
            "duckdb_memory_limit": args.memory_limit,
        },
        "point_in_time_rule": (
            "features for a transaction at minute t use only rows with timestamp < t; "
            "same-minute rows are simultaneous and invisible; windows are [t - w, t)"
        ),
        "currency_rule": (
            "log_received_over_paid is computed only when payment_currency equals "
            "receiving_currency and both amounts are strictly positive; it is null "
            "otherwise, so amounts from different currencies are never compared. "
            "currency_mismatch is the separate mismatched-currency indicator."
        ),
        "excluded": [
            "target labels",
            "split assignments (copied after feature computation, not a feature)",
            "pattern-file data",
            "transaction/account/entity identifiers",
            "amount sums or comparisons across currencies",
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
                "n_features": len(features_manifest["feature_columns"]),
                "timings_seconds": features_manifest["timings_seconds"],
                "total_build_seconds": features_manifest["total_build_seconds"],
            },
            indent=2,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# training and evaluation
# ---------------------------------------------------------------------------

#: LightGBM defaults for Iteration 2 runs; a config's ``params`` override these.
BASE_LGBM_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "metric": "average_precision",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 10.0,
    "max_bin": 255,
    "seed": 42,
    "bagging_seed": 42,
    "feature_fraction_seed": 42,
    "data_random_seed": 42,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 6,
    "verbosity": -1,
}
DEFAULT_MAX_ROUNDS = 3000
DEFAULT_EARLY_STOPPING = 200
#: External sidecar family (Iteration 1 graph contract, vectorized builder).
GFEAT_FAMILY = "gfeat"


def config_hash(config: dict[str, Any]) -> str:
    """Stable short hash of a run configuration."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load_candidates_manifest(path: Path) -> dict[str, dict[str, Any]]:
    """Load the committed candidate manifest keyed by candidate name.

    Every entry must pin ``name`` and a declared ``config_hash``. The test
    evaluator accepts only candidates present here whose grid configuration
    still hashes to that value; anything else fails closed.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    by_name: dict[str, dict[str, Any]] = {}
    for entry in data.get("candidates", []):
        name = entry.get("name")
        declared = entry.get("config_hash")
        if not name or not declared:
            raise ValueError("candidate manifest entries require name and config_hash")
        by_name[name] = entry
    return by_name


def _test_ledger_rows(output_dir: Path) -> list[dict[str, Any]]:
    """Existing test-ledger rows, or an empty list when none are recorded."""
    path = output_dir / TEST_LEDGER
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def git_commit() -> str | None:
    """Current git HEAD of the working tree, if available."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def config_columns(config: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Resolve a config's families/drops into an ordered feature column list."""
    columns: list[str] = []
    for family in config["families"]:
        if family == GFEAT_FAMILY:
            from ml.baselines import GRAPH_FEATURE_COLUMNS

            columns += list(GRAPH_FEATURE_COLUMNS)
        else:
            columns += manifest["families"][family]["columns"]
    drop = set(config.get("drop_columns", []))
    unknown = drop - set(columns)
    if unknown:
        raise ValueError(f"drop_columns not in selected families: {sorted(unknown)}")
    return [column for column in columns if column not in drop]


class SplitData:
    """Feature matrix, labels, and evaluation-only metadata for one split."""

    def __init__(
        self,
        X: np.ndarray,
        y: np.ndarray,
        ids: list[str],
        day: np.ndarray,
        amount_paid: np.ndarray,
        payment_currency: np.ndarray,
    ) -> None:
        self.X = X
        self.y = y
        self.ids = ids
        self.day = day
        self.amount_paid = amount_paid
        self.payment_currency = payment_currency


def load_split(
    features_path: Path,
    prepared_path: Path,
    split: str,
    columns: list[str],
    categorical: list[str],
    mappings: dict[str, dict[str, int]],
    gfeat_path: Path | None,
) -> SplitData:
    """Load one split as float32 features joined to offline labels by ID.

    ``mappings`` holds train-derived category codes; when empty (train), it
    is filled from this split's observed values. Unknown categories map to a
    dedicated trailing code. Labels and evaluation metadata (day, paid
    amount/currency for weighted recall) come only from the prepared table.
    """
    import numpy as np
    import polars as pl

    from ml.baselines import GRAPH_FEATURE_COLUMNS

    own_columns = [c for c in columns if c not in GRAPH_FEATURE_COLUMNS]
    feats = (
        pl.scan_parquet(features_path)
        .filter(pl.col("split") == split)
        .select(
            ["transaction_id"]
            + [
                pl.col(c) if c in categorical else pl.col(c).cast(pl.Float32)
                for c in own_columns
            ]
        )
    )
    if gfeat_path is not None and any(c in GRAPH_FEATURE_COLUMNS for c in columns):
        graph = pl.scan_parquet(gfeat_path).select(
            ["transaction_id"] + [pl.col(c).cast(pl.Float32) for c in GRAPH_FEATURE_COLUMNS]
        )
        feats = feats.join(graph, on="transaction_id", how="inner", validate="1:1")
    labels = (
        pl.scan_parquet(prepared_path)
        .filter(pl.col("split") == split)
        .select(
            pl.col("transaction_id"),
            pl.col(LABEL_COLUMN).cast(pl.Int8).alias("__label"),
            pl.col("timestamp").dt.date().cast(pl.Utf8).alias("__day"),
            pl.col("amount_paid").alias("__amount_paid"),
            pl.col("payment_currency").alias("__payment_currency"),
        )
    )
    frame = (
        feats.join(labels, on="transaction_id", how="inner", validate="1:1")
        .sort("transaction_id")
        .collect()
    )
    for column in categorical:
        if column not in columns:
            continue
        if column not in mappings:
            values = sorted(v for v in frame[column].unique().to_list() if v is not None)
            mappings[column] = {value: code for code, value in enumerate(values)}
        mapping = mappings[column]
        frame = frame.with_columns(
            pl.col(column)
            .replace_strict(mapping, default=len(mapping), return_dtype=pl.Int32)
            .cast(pl.Float32)
        )
    X = frame.select(columns).to_numpy().astype(np.float32, copy=False)
    data = SplitData(
        X=np.ascontiguousarray(X),
        y=frame["__label"].to_numpy().astype(np.int8),
        ids=frame["transaction_id"].to_list(),
        day=frame["__day"].to_numpy(),
        amount_paid=frame["__amount_paid"].to_numpy().astype(np.float64),
        payment_currency=frame["__payment_currency"].to_numpy(),
    )
    del frame
    return data


def budget_alerts(scores: np.ndarray, budget: float) -> np.ndarray:
    """Indices of the top ``ceil(budget * n)`` scores (fixed-capacity rule)."""
    import numpy as np

    from ml.baselines import select_top_k

    return select_top_k(scores, max(int(np.ceil(budget * len(scores))), 1))


def currency_weighted_recall(
    alerts: np.ndarray, y: np.ndarray, amount: np.ndarray, currency: np.ndarray
) -> dict[str, dict[str, float | int]]:
    """Per payment currency: alerted positive paid amount / all positive paid amount.

    Weights are ``Amount Paid`` in the transaction's own payment currency.
    No FX conversion is applied and currencies are never summed together.
    """
    import numpy as np

    alerted = np.zeros(len(y), dtype=bool)
    alerted[alerts] = True
    positive = y == 1
    report: dict[str, dict[str, float | int]] = {}
    for cur in sorted(set(currency[positive].tolist())):
        in_cur = positive & (currency == cur)
        total = float(amount[in_cur].sum())
        caught = float(amount[in_cur & alerted].sum())
        report[cur] = {
            "positives": int(in_cur.sum()),
            "alerted_positives": int((in_cur & alerted).sum()),
            "positive_amount_paid": total,
            "alerted_positive_amount_paid": caught,
            "weighted_recall": caught / total if total > 0 else 0.0,
        }
    return report


def cohort_metrics(
    scores: np.ndarray,
    y: np.ndarray,
    amount: np.ndarray,
    currency: np.ndarray,
    threshold: float,
    budget: float,
) -> dict[str, Any]:
    """AP, threshold P/R/F1, budget P/R, and per-currency weighted recall."""
    from ml.baselines import average_precision

    positives = int(y.sum())
    predicted = scores >= threshold
    tp = int((predicted & (y == 1)).sum())
    alerts = int(predicted.sum())
    precision = tp / max(alerts, 1)
    recall = tp / max(positives, 1)
    top = budget_alerts(scores, budget)
    budget_tp = int(y[top].sum())
    return {
        "rows": len(y),
        "positives": positives,
        "prevalence": positives / max(len(y), 1),
        "average_precision": average_precision(y, scores) if positives else None,
        "at_val_threshold": {
            "threshold": threshold,
            "alerts": alerts,
            "true_positives": tp,
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / max(precision + recall, 1e-9),
        },
        "at_alert_budget": {
            "budget": budget,
            "alerts": len(top),
            "true_positives": budget_tp,
            "precision": budget_tp / max(len(top), 1),
            "recall": budget_tp / max(positives, 1),
            "currency_weighted_recall": currency_weighted_recall(top, y, amount, currency),
        },
    }


def predict(booster: Any, X: np.ndarray, column_index: list[int], chunk: int = 400_000):
    """Chunked prediction on a column subset (bounded temporary copies)."""
    import numpy as np

    out = np.empty(X.shape[0], dtype=np.float64)
    for start in range(0, X.shape[0], chunk):
        block = X[start : start + chunk][:, column_index]
        out[start : start + chunk] = booster.predict(block, num_iteration=booster.best_iteration)
    return out


def fit_config(
    config: dict[str, Any],
    all_columns: list[str],
    categorical: list[str],
    manifest: dict[str, Any],
    train: SplitData,
    val: SplitData,
) -> tuple[Any, list[str], list[int], dict[str, Any]]:
    """Train one configuration; returns booster, columns, indices, and fit info."""
    import lightgbm as lgb
    import numpy as np

    columns = config_columns(config, manifest)
    column_index = [all_columns.index(c) for c in columns]
    cat_positions = [i for i, c in enumerate(columns) if c in categorical]
    rate = float(config.get("neg_sample_rate", 1.0))
    rng = np.random.default_rng(int(config.get("sample_seed", 7)))
    keep = (train.y == 1) | (rng.random(len(train.y)) < rate)
    rows = np.flatnonzero(keep)
    X_fit = train.X[rows][:, column_index]
    y_fit = train.y[rows]
    params = {**BASE_LGBM_PARAMS, **config.get("params", {})}
    if "seed" in config:
        for key in ("seed", "bagging_seed", "feature_fraction_seed", "data_random_seed"):
            params[key] = int(config["seed"])
    start = time.perf_counter()
    dtrain = lgb.Dataset(
        X_fit, label=y_fit, feature_name=columns, categorical_feature=cat_positions,
        free_raw_data=True,
    )
    dval = lgb.Dataset(
        val.X[:, column_index], label=val.y, reference=dtrain,
        feature_name=columns, categorical_feature=cat_positions,
    )
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=int(config.get("max_rounds", DEFAULT_MAX_ROUNDS)),
        valid_sets=[dval],
        valid_names=["validation"],
        callbacks=[
            lgb.early_stopping(
                int(config.get("early_stopping_rounds", DEFAULT_EARLY_STOPPING)),
                # False emulates the Iteration 1 sklearn wrapper, which stopped
                # on whichever of several metrics stalled first.
                first_metric_only=bool(config.get("first_metric_only", True)),
                verbose=False,
            )
        ],
    )
    fit_seconds = time.perf_counter() - start
    del X_fit, dtrain, dval
    info = {
        "fit_rows": len(rows),
        "fit_positives": int(y_fit.sum()),
        "best_iteration": int(booster.best_iteration),
        "fit_seconds": round(fit_seconds, 2),
        "params": params,
    }
    return booster, columns, column_index, info


def gain_importance(booster: Any, columns: list[str]) -> dict[str, float]:
    """Total gain per feature, normalized to sum to 1, sorted descending."""
    gains = booster.feature_importance(importance_type="gain")
    total = float(gains.sum()) or 1.0
    pairs = sorted(zip(columns, gains, strict=True), key=lambda item: -item[1])
    return {name: round(float(gain) / total, 6) for name, gain in pairs}


def _load_manifest(features_dir: Path) -> tuple[Path, dict[str, Any]]:
    features_path = features_dir / FEATURES_FILENAME
    manifest = json.loads((features_dir / FEATURES_MANIFEST_FILENAME).read_text("utf-8"))
    return features_path, manifest


def _check_inputs(args: argparse.Namespace, manifest: dict[str, Any]) -> None:
    if sha256_of(args.prepared_parquet) != manifest["prepared_parquet_sha256"]:
        raise ValueError("prepared parquet changed after feature generation")
    if LABEL_COLUMN in manifest["feature_columns"]:
        raise ValueError("target column present in feature list")


def _union_columns(configs: list[dict[str, Any]], manifest: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for config in configs:
        for column in config_columns(config, manifest):
            if column not in seen:
                seen.append(column)
    return seen


def _gfeat_meta(args: argparse.Namespace, configs: list[dict[str, Any]]) -> dict | None:
    if not any(GFEAT_FAMILY in c["families"] for c in configs):
        return None
    if args.graph_features is None:
        raise ValueError("a config uses the gfeat family; pass --graph-features")
    from ml.baselines import validate_graph_features

    return validate_graph_features(args.graph_features)


def _load_fit_splits(
    args: argparse.Namespace,
    configs: list[dict[str, Any]],
    manifest: dict[str, Any],
    features_path: Path,
) -> tuple[list[str], list[str], dict[str, dict[str, int]], SplitData, SplitData]:
    all_columns = _union_columns(configs, manifest)
    categorical = [c for c in manifest["categorical_columns"] if c in all_columns]
    mappings: dict[str, dict[str, int]] = {}
    train = load_split(
        features_path, args.prepared_parquet, "train", all_columns, categorical, mappings,
        args.graph_features,
    )
    val = load_split(
        features_path, args.prepared_parquet, "validation", all_columns, categorical,
        mappings, args.graph_features,
    )
    expected = manifest["split_counts"]
    if len(train.y) != expected["train"] or len(val.y) != expected["validation"]:
        raise ValueError("feature/label rows do not reconcile for train/validation")
    return all_columns, categorical, mappings, train, val


def run_validate(args: argparse.Namespace) -> int:
    """Train each grid configuration and log validation-only metrics."""
    from ml.baselines import average_precision, select_threshold_max_f1

    features_path, manifest = _load_manifest(args.features_dir)
    configs: list[dict[str, Any]] = json.loads(args.grid.read_text(encoding="utf-8"))
    if args.only:
        configs = [c for c in configs if c["name"] in set(args.only)]
    if args.name_prefix:
        configs = [c for c in configs if c["name"].startswith(tuple(args.name_prefix))]
    ledger_path = args.output_dir / VALIDATION_LEDGER
    if args.skip_done and ledger_path.is_file():
        done = {
            json.loads(line)["config_hash"]
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        configs = [c for c in configs if config_hash(c) not in done]
    if not configs:
        print(json.dumps({"error": "no configurations selected"}))
        return 2
    try:
        _check_inputs(args, manifest)
        gfeat = _gfeat_meta(args, configs)
        start = time.perf_counter()
        all_columns, categorical, _, train, val = _load_fit_splits(
            args, configs, manifest, features_path
        )
    except (ValueError, FileNotFoundError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    load_seconds = time.perf_counter() - start
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ledger = args.output_dir / VALIDATION_LEDGER
    for config in configs:
        booster, columns, column_index, info = fit_config(
            config, all_columns, categorical, manifest, train, val
        )
        val_scores = predict(booster, val.X, column_index)
        threshold = select_threshold_max_f1(val_scores, val.y)
        val_metrics = cohort_metrics(
            val_scores, val.y, val.amount_paid, val.payment_currency, threshold, ALERT_BUDGET
        )
        train_ap = None
        if not args.skip_train_ap:
            train_ap = average_precision(train.y, predict(booster, train.X, column_index))
        record = {
            "created_utc": datetime.now(UTC).isoformat(),
            "name": config["name"],
            "config": config,
            "config_hash": config_hash(config),
            "n_features": len(columns),
            "feature_columns": columns,
            "train_average_precision": train_ap,
            "validation": val_metrics,
            "fit": info,
            "load_seconds": round(load_seconds, 2),
            "gain_importance": gain_importance(booster, columns),
            "features_manifest_sha256": sha256_of(args.features_dir / FEATURES_MANIFEST_FILENAME),
            "split_manifest_sha256": manifest["split_manifest_sha256"],
            "prepared_parquet_sha256": manifest["prepared_parquet_sha256"],
            "graph_features": gfeat,
            "git_commit": git_commit(),
        }
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        print(
            json.dumps(
                {
                    "name": config["name"],
                    "val_ap": round(val_metrics["average_precision"], 5),
                    "train_ap": None if train_ap is None else round(train_ap, 4),
                    "val_budget_recall": round(val_metrics["at_alert_budget"]["recall"], 4),
                    "val_f1": round(val_metrics["at_val_threshold"]["f1"], 4),
                    "best_iteration": info["best_iteration"],
                    "fit_seconds": info["fit_seconds"],
                }
            ),
            flush=True,
        )
        del booster
    return 0


def day_average_precision(
    scores: np.ndarray, y: np.ndarray, day: np.ndarray
) -> dict[str, dict[str, Any]]:
    """Per calendar day: rows, positives, and AP (None without positives)."""
    from ml.baselines import average_precision

    report: dict[str, dict[str, Any]] = {}
    for value in sorted(set(day.tolist())):
        mask = day == value
        positives = int(y[mask].sum())
        report[value] = {
            "rows": int(mask.sum()),
            "positives": positives,
            "average_precision": (
                average_precision(y[mask], scores[mask]) if positives else None
            ),
        }
    return report


def split_report(
    scores: np.ndarray, data: SplitData, threshold: float
) -> dict[str, Any]:
    """Full-cohort, Sept 9-10 bulk, and tail metrics for one scored split."""
    import numpy as np

    report: dict[str, Any] = {
        "all": cohort_metrics(
            scores, data.y, data.amount_paid, data.payment_currency, threshold, ALERT_BUDGET
        )
    }
    bulk = np.isin(data.day, list(BULK_DAYS))
    if bulk.any() and (~bulk).any():
        report["bulk_sept_9_10"] = cohort_metrics(
            scores[bulk], data.y[bulk], data.amount_paid[bulk],
            data.payment_currency[bulk], threshold, ALERT_BUDGET,
        )
        tail = ~bulk
        tail_alerts = scores[tail] >= threshold
        report["tail_after_sept_10"] = {
            "rows": int(tail.sum()),
            "positives": int(data.y[tail].sum()),
            "true_positives_at_val_threshold": int((tail_alerts & (data.y[tail] == 1)).sum()),
            "alerts_at_val_threshold": int(tail_alerts.sum()),
        }
        top = budget_alerts(scores, ALERT_BUDGET)
        report["all"]["at_alert_budget"]["alerts_in_tail"] = int((~bulk[top]).sum())
    return report


def run_test(args: argparse.Namespace) -> int:
    """Evaluate one pre-declared candidate on the frozen test cohort (ledgered)."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    from ml.baselines import average_precision, select_threshold_max_f1

    configs: list[dict[str, Any]] = json.loads(args.grid.read_text(encoding="utf-8"))
    matches = [c for c in configs if c["name"] == args.candidate]
    if len(matches) != 1:
        print(json.dumps({"error": f"candidate not found exactly once: {args.candidate}"}))
        return 2
    config = matches[0]
    try:
        declared = load_candidates_manifest(args.candidates_manifest)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": f"candidate manifest unreadable: {exc}"}))
        return 2
    entry = declared.get(config["name"])
    if entry is None:
        print(
            json.dumps(
                {
                    "error": (
                        f"candidate {config['name']} is not declared in "
                        f"{args.candidates_manifest}; declare it before evaluating test"
                    )
                }
            )
        )
        return 2
    actual_hash = config_hash(config)
    if entry["config_hash"] != actual_hash:
        print(
            json.dumps(
                {
                    "error": (
                        f"candidate {config['name']} config hash {actual_hash} does not match "
                        f"declared {entry['config_hash']}; treat this as a new experiment, "
                        "not an overwrite of the declared candidate"
                    )
                }
            )
        )
        return 2
    experiment_id = args.experiment_id
    run_name = config["name"] if experiment_id is None else f"{config['name']}__{experiment_id}"
    run_dir = args.output_dir / "candidates" / run_name
    duplicate = [
        row
        for row in _test_ledger_rows(args.output_dir)
        if row.get("candidate") == config["name"]
        and row.get("experiment_id") == experiment_id
    ]
    if duplicate:
        print(
            json.dumps(
                {
                    "error": (
                        f"test evaluation already recorded for candidate {config['name']} with "
                        f"experiment_id {experiment_id!r}; pass a new --experiment-id"
                    )
                }
            )
        )
        return 2
    if run_dir.exists():
        print(
            json.dumps(
                {
                    "error": (
                        f"test artifacts already exist at {run_dir}; refusing to overwrite "
                        "an existing result"
                    )
                }
            )
        )
        return 2

    features_path, manifest = _load_manifest(args.features_dir)
    try:
        _check_inputs(args, manifest)
        gfeat = _gfeat_meta(args, [config])
        all_columns, categorical, mappings, train, val = _load_fit_splits(
            args, [config], manifest, features_path
        )
    except (ValueError, FileNotFoundError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    booster, columns, column_index, info = fit_config(
        config, all_columns, categorical, manifest, train, val
    )
    train_ap = average_precision(train.y, predict(booster, train.X, column_index))
    del train
    val_scores = predict(booster, val.X, column_index)
    # Threshold locked on validation before any test label is loaded.
    threshold = select_threshold_max_f1(val_scores, val.y)
    val_report = split_report(val_scores, val, threshold)
    val_report["by_day"] = day_average_precision(val_scores, val.y, val.day)
    final_day = max(val.day.tolist())
    earlier = val.day != final_day
    val_report["excluding_final_day"] = {
        "excluded_day": final_day,
        "rows": int(earlier.sum()),
        "positives": int(val.y[earlier].sum()),
        "average_precision": average_precision(val.y[earlier], val_scores[earlier]),
    }
    val_frame = pa.table(
        {"transaction_id": val.ids, LABEL_COLUMN: val.y.astype(np.int8), "score": val_scores}
    )
    del val

    test = load_split(
        features_path, args.prepared_parquet, "test", all_columns, categorical, mappings,
        args.graph_features,
    )
    if len(test.y) != manifest["split_counts"]["test"]:
        print(json.dumps({"error": "feature/label rows do not reconcile for test"}))
        return 1
    start = time.perf_counter()
    test_scores = predict(booster, test.X, column_index)
    score_seconds = time.perf_counter() - start
    test_report = split_report(test_scores, test, threshold)
    test_report["by_day"] = day_average_precision(test_scores, test.y, test.day)

    run_dir.mkdir(parents=True, exist_ok=False)
    model_path = run_dir / "model.txt"
    score_path = run_dir / "test_scores.parquet"
    booster.save_model(str(model_path), num_iteration=booster.best_iteration)
    pq.write_table(
        pa.table(
            {
                "transaction_id": test.ids,
                LABEL_COLUMN: test.y.astype(np.int8),
                "score": test_scores,
            }
        ),
        score_path,
    )
    pq.write_table(val_frame, run_dir / "validation_scores.parquet")
    features_manifest_sha256 = sha256_of(args.features_dir / FEATURES_MANIFEST_FILENAME)
    run_config = {
        "created_utc": datetime.now(UTC).isoformat(),
        "tool": "ml.experiment test",
        "candidate": config,
        "config_hash": config_hash(config),
        "experiment_id": experiment_id,
        "feature_columns": columns,
        "categorical_columns": [c for c in columns if c in categorical],
        "category_mappings": {k: v for k, v in mappings.items() if k in columns},
        "threshold": threshold,
        "threshold_rule": "max-F1 on validation scores, applied unchanged to test",
        "alert_budget": ALERT_BUDGET,
        "fit": info,
        "model_sha256": sha256_of(model_path),
        "test_scores_sha256": sha256_of(score_path),
        "features_manifest_sha256": features_manifest_sha256,
        "split_manifest_sha256": manifest["split_manifest_sha256"],
        "prepared_parquet_sha256": manifest["prepared_parquet_sha256"],
        "graph_features": gfeat,
        "git_commit": git_commit(),
    }
    (run_dir / "config.json").write_text(json.dumps(run_config, indent=2), encoding="utf-8")
    record = {
        "created_utc": run_config["created_utc"],
        "kind": "new candidate test evaluation",
        "candidate": config["name"],
        "config_hash": run_config["config_hash"],
        "experiment_id": experiment_id,
        "git_commit": run_config["git_commit"],
        "model_sha256": run_config["model_sha256"],
        "test_scores_sha256": run_config["test_scores_sha256"],
        "features_manifest_sha256": run_config["features_manifest_sha256"],
        "split_manifest_sha256": run_config["split_manifest_sha256"],
        "prepared_parquet_sha256": run_config["prepared_parquet_sha256"],
        "train_average_precision": train_ap,
        "validation": val_report,
        "test": test_report,
        "best_iteration": info["best_iteration"],
        "fit_seconds": info["fit_seconds"],
        "test_score_seconds": round(score_seconds, 3),
        "test_rows_per_second": round(len(test.y) / max(score_seconds, 1e-9), 1),
        "gain_importance": gain_importance(booster, columns),
    }
    (run_dir / "metrics.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    with (args.output_dir / TEST_LEDGER).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    summary = test_report["all"]
    print(
        json.dumps(
            {
                "candidate": config["name"],
                "test_ap": summary["average_precision"],
                "test_at_val_threshold": summary["at_val_threshold"],
                "test_budget_recall": summary["at_alert_budget"]["recall"],
                "bulk_ap": test_report.get("bulk_sept_9_10", {}).get("average_precision"),
                "run_dir": str(run_dir),
            },
            indent=2,
        )
    )
    return 0


def run_rescore(args: argparse.Namespace) -> int:
    """Re-analyze existing frozen test scores with the Iteration 2 metric set.

    For a model already evaluated on test (e.g. the Iteration 1 baseline);
    no model is trained. Still appended to the test ledger for completeness.
    """
    import polars as pl

    scores = pl.read_parquet(args.scores).select(["transaction_id", args.score_column])
    labels = (
        pl.scan_parquet(args.prepared_parquet)
        .filter(pl.col("split") == "test")
        .select(
            "transaction_id",
            pl.col(LABEL_COLUMN).cast(pl.Int8).alias("__label"),
            pl.col("timestamp").dt.date().cast(pl.Utf8).alias("__day"),
            pl.col("amount_paid"),
            pl.col("payment_currency"),
        )
        .collect()
    )
    frame = scores.join(labels, on="transaction_id", how="inner", validate="1:1").sort(
        "transaction_id"
    )
    if frame.height != labels.height:
        print(json.dumps({"error": "scores do not cover the test cohort"}))
        return 1
    import numpy as np

    data = SplitData(
        X=np.empty((frame.height, 0), dtype=np.float32),
        y=frame["__label"].to_numpy().astype(np.int8),
        ids=frame["transaction_id"].to_list(),
        day=frame["__day"].to_numpy(),
        amount_paid=frame["amount_paid"].to_numpy(),
        payment_currency=frame["payment_currency"].to_numpy(),
    )
    report = split_report(frame[args.score_column].to_numpy(), data, args.threshold)
    record = {
        "created_utc": datetime.now(UTC).isoformat(),
        "kind": "re-analysis of existing frozen test scores (no training)",
        "candidate": args.name,
        "scores_sha256": sha256_of(args.scores),
        "threshold": args.threshold,
        "test": report,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / TEST_LEDGER).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    print(json.dumps({"candidate": args.name, "test_ap": report["all"]["average_precision"]}))
    return 0


def run_publish(args: argparse.Namespace) -> int:
    """Publish target-free inference scores for a tested candidate."""
    from ml.inference_scores import publish_scores

    run_dir = args.output_dir / "candidates" / args.candidate
    scores = run_dir / "test_scores.parquet"
    features_path = args.features_dir / FEATURES_FILENAME
    output = args.output_dir / "inference_scores.parquet"
    try:
        rows = publish_scores(scores, features_path, output, score_column="score")
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    manifest = {
        "created_utc": datetime.now(UTC).isoformat(),
        "tool": "ml.experiment publish",
        "candidate": args.candidate,
        "model_config": str(run_dir / "config.json"),
        "model_config_sha256": sha256_of(run_dir / "config.json"),
        "rows": rows,
        "output": str(output),
        "scores_sha256": sha256_of(scores),
        "features_sha256": sha256_of(features_path),
        "output_sha256": sha256_of(output),
        "columns": ["transaction_id", "timestamp", "score"],
        "labels_read": False,
    }
    # API-compatible threshold file (services/api reads thresholds[score_model]).
    model_config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    api_config = {
        "tool": "ml.experiment publish",
        "candidate": args.candidate,
        "threshold_rule": model_config["threshold_rule"],
        "thresholds": {"lgbm": model_config["threshold"]},
        "feature_columns": model_config["feature_columns"],
        "label_column": LABEL_COLUMN,
        "split_manifest_sha256": model_config["split_manifest_sha256"],
    }
    api_path = args.output_dir / "api_threshold_config.json"
    api_path.write_text(json.dumps(api_config, indent=2), encoding="utf-8")
    manifest["api_threshold_config"] = str(api_path)
    path = args.output_dir / "inference_scores-manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


def run_truncation_check(args: argparse.Namespace) -> int:
    """Real-data future-invisibility audit.

    Rebuilds every family from rows with ``timestamp <= cutoff`` only and
    compares each feature of those rows against the full-data build. Any
    difference means a feature read data from after the cutoff.
    """
    import duckdb

    features_path, manifest = _load_manifest(args.features_dir)
    families = list(manifest["families"])
    columns = manifest["feature_columns"]
    con = duckdb.connect()
    try:
        con.execute(f"SET memory_limit='{args.memory_limit}'")
        con.execute(f"SET threads={int(args.threads)}")
        con.execute(f"SET temp_directory={sql_literal(args.features_dir / 'duckdb-tmp')}")
        con.execute(
            "CREATE OR REPLACE TEMP TABLE tx_input AS SELECT "
            + ", ".join(TX_INPUT_COLUMNS)
            + f" FROM read_parquet({sql_literal(args.prepared_parquet)}) "
            f"WHERE timestamp <= TIMESTAMP '{args.cutoff}'"
        )
        rows = con.execute("SELECT COUNT(*) FROM tx_input").fetchone()[0]
        if manifest.get("accounts_csv"):
            con.execute(
                f"CREATE OR REPLACE TEMP VIEW {ACCOUNTS_VIEW} AS SELECT "
                '"Bank ID" AS bank_id, "Account Number" AS account_number, '
                '"Entity ID" AS entity_id, '
                "split_part(\"Entity Name\", ' #', 1) AS entity_type "
                f"FROM read_csv({sql_literal(Path(manifest['accounts_csv']))}, "
                "all_varchar=true, header=true)"
            )
        truncated = build_families(con, "tx_input", families)
        categorical = set(manifest["categorical_columns"])
        checks = []
        for column in columns:
            if column in categorical:
                checks.append(f"SUM((a.{column} IS DISTINCT FROM b.{column})::BIGINT)")
            else:
                checks.append(
                    f"SUM(CASE WHEN (a.{column} IS NULL) <> (b.{column} IS NULL) THEN 1 "
                    f"WHEN ABS(a.{column} - b.{column}) > 1e-9 * (1 + ABS(b.{column})) "
                    "THEN 1 ELSE 0 END)"
                )
        result = con.execute(
            "SELECT COUNT(*), " + ", ".join(checks) + f" FROM {truncated} a "
            f"JOIN read_parquet({sql_literal(features_path)}) b USING (transaction_id)"
        ).fetchone()
    finally:
        con.close()
    matched = int(result[0])
    mismatches = {c: int(v) for c, v in zip(columns, result[1:], strict=True) if v}
    report = {
        "created_utc": datetime.now(UTC).isoformat(),
        "cutoff": args.cutoff,
        "truncated_rows": int(rows),
        "matched_rows": matched,
        "columns_checked": len(columns),
        "mismatched_columns": mismatches,
        "passed": matched == rows and not mismatches,
    }
    out = args.features_dir / "truncation-check.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


def _accounts_view(con: Any, manifest: dict[str, Any]) -> None:
    if manifest.get("accounts_csv"):
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW {ACCOUNTS_VIEW} AS SELECT "
            '"Bank ID" AS bank_id, "Account Number" AS account_number, '
            '"Entity ID" AS entity_id, '
            "split_part(\"Entity Name\", ' #', 1) AS entity_type "
            f"FROM read_csv({sql_literal(Path(manifest['accounts_csv']))}, "
            "all_varchar=true, header=true)"
        )


def _mismatch_sql(columns: list[str], categorical: set[str]) -> list[str]:
    checks = []
    for column in columns:
        if column in categorical:
            checks.append(f"SUM((a.{column} IS DISTINCT FROM b.{column})::BIGINT)")
        else:
            checks.append(
                f"SUM(CASE WHEN (a.{column} IS NULL) <> (b.{column} IS NULL) THEN 1 "
                f"WHEN ABS(a.{column} - b.{column}) > 1e-9 * (1 + ABS(b.{column})) "
                "THEN 1 ELSE 0 END)"
            )
    return checks


def run_perturbation_check(args: argparse.Namespace) -> int:
    """Real-data same-minute and future perturbation audit at sampled minutes.

    For each of ``--sample-minutes`` randomly chosen minutes ``m`` with at
    least 100 rows, the feature input is: every row before ``m`` unchanged; a
    random half of the rows at ``m`` (the other half, same-minute peers, are
    deleted); and the next 7 days of rows rewritten (about 30% dropped,
    receivers swapped with the next row's receiver, amounts multiplied by 7).
    Rows after that horizon are omitted. Every kept row at ``m`` must have
    exactly the same 76 features as in the full-data build.
    """
    import duckdb

    features_path, manifest = _load_manifest(args.features_dir)
    families = list(manifest["families"])
    columns = manifest["feature_columns"]
    categorical = set(manifest["categorical_columns"])
    source = sql_literal(args.prepared_parquet)
    cols = ", ".join(TX_INPUT_COLUMNS)
    rewritten = ", ".join(
        {
            "receiver_bank": "COALESCE(LEAD(receiver_bank) OVER w, receiver_bank)",
            "receiver_account": "COALESCE(LEAD(receiver_account) OVER w, receiver_account)",
            "receiver_account_key": (
                "COALESCE(LEAD(receiver_account_key) OVER w, receiver_account_key)"
            ),
            "amount_paid": "amount_paid * 7",
            "amount_received": "amount_received * 7",
        }.get(name, name)
        + f" AS {name}"
        for name in TX_INPUT_COLUMNS
    )
    minutes_report = []
    con = duckdb.connect()
    try:
        con.execute(f"SET memory_limit='{args.memory_limit}'")
        con.execute(f"SET threads={int(args.threads)}")
        con.execute(f"SET temp_directory={sql_literal(args.features_dir / 'duckdb-tmp')}")
        _accounts_view(con, manifest)
        minutes = [
            row[0]
            for row in con.execute(
                "SELECT ts FROM (SELECT timestamp AS ts FROM read_parquet("
                f"{source}) WHERE timestamp <= TIMESTAMP '{args.cutoff}' "
                "GROUP BY timestamp HAVING COUNT(*) >= 100) "
                f"USING SAMPLE reservoir({int(args.sample_minutes)} ROWS) "
                f"REPEATABLE ({int(args.seed)}) ORDER BY ts"
            ).fetchall()
        ]
        for minute in minutes:
            m = f"TIMESTAMP '{minute:%Y-%m-%d %H:%M}'"
            con.execute(
                f"CREATE OR REPLACE TEMP TABLE tx_input AS SELECT {cols} FROM "
                f"read_parquet({source}) WHERE timestamp < {m} "
                f"OR (timestamp = {m} AND hash(transaction_id) % 2 = 0) "
                f"UNION ALL SELECT * FROM (SELECT {rewritten} FROM (SELECT {cols} FROM "
                f"read_parquet({source}) WHERE timestamp > {m} "
                f"AND timestamp <= {m} + INTERVAL 7 DAY AND hash(transaction_id) % 10 >= 3) "
                "WINDOW w AS (ORDER BY transaction_id))"
            )
            removed = con.execute(
                f"SELECT COUNT(*) FROM read_parquet({source}) WHERE timestamp = {m} "
                "AND hash(transaction_id) % 2 = 1"
            ).fetchone()[0]
            perturbed = build_families(con, "tx_input", families)
            result = con.execute(
                "SELECT COUNT(*), " + ", ".join(_mismatch_sql(columns, categorical))
                + f" FROM {perturbed} a JOIN read_parquet({sql_literal(features_path)}) b "
                f"USING (transaction_id) WHERE b.timestamp = {m}"
            ).fetchone()
            minutes_report.append(
                {
                    "minute": f"{minute:%Y-%m-%d %H:%M}",
                    "probe_rows": int(result[0]),
                    "same_minute_peers_removed": int(removed),
                    "mismatched_columns": {
                        c: int(v) for c, v in zip(columns, result[1:], strict=True) if v
                    },
                }
            )
            print(json.dumps(minutes_report[-1]), flush=True)
    finally:
        con.close()
    report = {
        "created_utc": datetime.now(UTC).isoformat(),
        "seed": args.seed,
        "cutoff": args.cutoff,
        "columns_checked": len(columns),
        "probe_rows_total": sum(r["probe_rows"] for r in minutes_report),
        "minutes": minutes_report,
        "passed": all(not r["mismatched_columns"] for r in minutes_report),
    }
    out = args.features_dir / "perturbation-check.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "minutes"}, indent=2))
    return 0 if report["passed"] else 1


def _add_fit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--features-dir", type=Path, required=True)
    parser.add_argument("--prepared-parquet", type=Path, required=True)
    parser.add_argument("--grid", type=Path, required=True, help="JSON list of run configs")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--graph-features", type=Path, default=None)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Iteration 2 detection experiments.")
    sub = parser.add_subparsers(dest="command", required=True)

    feat = sub.add_parser("features", help="build point-in-time feature families")
    feat.add_argument("--source-parquet", type=Path, required=True)
    feat.add_argument("--split-manifest", type=Path, required=True)
    feat.add_argument("--accounts-csv", type=Path, default=None)
    feat.add_argument("--output-dir", type=Path, required=True)
    feat.add_argument("--families", nargs="*", choices=list(ALL_FAMILIES), default=None)
    feat.add_argument("--threads", type=int, default=6)
    feat.add_argument("--memory-limit", default="4GB")
    feat.add_argument("--data-version", type=int, default=DATA_VERSION)

    val = sub.add_parser("validate", help="train configs; validation metrics only")
    _add_fit_args(val)
    val.add_argument("--only", nargs="*", default=None, help="config names to run")
    val.add_argument("--name-prefix", nargs="*", default=None, help="config name prefixes")
    val.add_argument("--skip-train-ap", action="store_true")
    val.add_argument(
        "--skip-done", action="store_true",
        help="skip configs whose hash already appears in the validation ledger",
    )

    test = sub.add_parser("test", help="evaluate ONE pre-declared candidate on test")
    _add_fit_args(test)
    test.add_argument("--candidate", required=True)
    test.add_argument(
        "--candidates-manifest",
        type=Path,
        default=Path(DEFAULT_CANDIDATES_MANIFEST),
        help="committed manifest of declared candidates (name plus config hash)",
    )
    test.add_argument(
        "--experiment-id",
        default=None,
        help=(
            "explicit id for a deliberate repeat evaluation; writes a separate artifact "
            "directory and ledger row while preserving earlier results"
        ),
    )

    rescore = sub.add_parser("rescore", help="re-analyze existing frozen test scores")
    rescore.add_argument("--scores", type=Path, required=True)
    rescore.add_argument("--score-column", required=True)
    rescore.add_argument("--threshold", type=float, required=True)
    rescore.add_argument("--name", required=True)
    rescore.add_argument("--prepared-parquet", type=Path, required=True)
    rescore.add_argument("--output-dir", type=Path, required=True)

    trunc = sub.add_parser("truncation-check", help="real-data future-invisibility audit")
    trunc.add_argument("--features-dir", type=Path, required=True)
    trunc.add_argument("--prepared-parquet", type=Path, required=True)
    trunc.add_argument("--cutoff", required=True, help="inclusive, e.g. 2022-09-09 03:16")
    trunc.add_argument("--threads", type=int, default=3)
    trunc.add_argument("--memory-limit", default="2GB")

    pert = sub.add_parser("perturbation-check", help="real-data same-minute/future audit")
    pert.add_argument("--features-dir", type=Path, required=True)
    pert.add_argument("--prepared-parquet", type=Path, required=True)
    pert.add_argument("--cutoff", required=True, help="inclusive, e.g. 2022-09-08 00:00")
    pert.add_argument("--sample-minutes", type=int, default=3)
    pert.add_argument("--seed", type=int, default=20221016)
    pert.add_argument("--threads", type=int, default=4)
    pert.add_argument("--memory-limit", default="3GB")

    pub = sub.add_parser("publish", help="publish target-free scores for a candidate")
    pub.add_argument("--features-dir", type=Path, required=True)
    pub.add_argument("--output-dir", type=Path, required=True)
    pub.add_argument("--candidate", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    handlers = {
        "features": run_features,
        "validate": run_validate,
        "test": run_test,
        "rescore": run_rescore,
        "truncation-check": run_truncation_check,
        "perturbation-check": run_perturbation_check,
        "publish": run_publish,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
