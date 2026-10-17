"""LightGBM scoring workloads on the frozen held-out cohort.

Matrix preparation mirrors ``ml.baselines``: the six numeric columns in
``NUMERIC_COLUMNS`` order, then the three categoricals integer-coded from
sorted train-split values with a trailing unknown code, rows sorted by
``transaction_id``. Only the target-free offline feature table is read
(``transaction_id``, ``split``, feature columns); labels are never loaded.
Timings use an already prepared float64 matrix and exclude feature
retrieval, encoding, graph access, and serialization.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from bench.env import ResourceWindow
from bench.stats import summarize_ms

NUMERIC_COLUMNS: tuple[str, ...] = (
    "log_amount_paid",
    "log_amount_received",
    "sender_prior_out",
    "sender_prior_in",
    "receiver_prior_out",
    "receiver_prior_in",
)
CATEGORICAL_COLUMNS: tuple[str, ...] = ("payment_currency", "receiving_currency", "payment_format")
FORBIDDEN = ("target_is_laundering", "is_laundering", "label", "typology", "pattern")


def _sql(path: Path) -> str:
    return str(path).replace("'", "''")


def prepare_test_matrix(features: Path) -> tuple[list[str], np.ndarray]:
    """Return held-out transaction IDs and the dense LightGBM matrix."""
    import duckdb

    con = duckdb.connect()
    try:
        names = {
            row[0]
            for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{_sql(features)}')"
            ).fetchall()
        }
        leaked = [name for name in FORBIDDEN if name in names]
        if leaked:
            raise ValueError(f"feature table carries label columns: {leaked}")
        mappings: dict[str, dict[str, int]] = {}
        for column in CATEGORICAL_COLUMNS:
            values = [
                row[0]
                for row in con.execute(
                    f"SELECT DISTINCT {column} FROM read_parquet('{_sql(features)}') "
                    "WHERE split = 'train'"
                ).fetchall()
            ]
            mappings[column] = {value: code for code, value in enumerate(sorted(values))}
        selected = ", ".join(["transaction_id", *NUMERIC_COLUMNS, *CATEGORICAL_COLUMNS])
        table = con.execute(
            f"SELECT {selected} FROM read_parquet('{_sql(features)}') WHERE split = 'test' "
            "ORDER BY transaction_id"
        ).fetch_arrow_table()
    finally:
        con.close()
    ids = table.column("transaction_id").to_pylist()
    numeric = np.column_stack(
        [table.column(name).to_numpy().astype(np.float64) for name in NUMERIC_COLUMNS]
    )
    codes = np.column_stack(
        [
            np.array(
                [
                    mappings[name].get(value, len(mappings[name]))
                    for value in table.column(name).to_pylist()
                ],
                dtype=np.float64,
            )
            for name in CATEGORICAL_COLUMNS
        ]
    )
    return ids, np.ascontiguousarray(np.hstack([numeric, codes]))


def saved_scores(scores_path: Path, ids: list[str]) -> np.ndarray:
    """Saved artifact scores aligned to ``ids`` (target-free columns only)."""
    import pyarrow.parquet as pq

    table = pq.read_table(scores_path, columns=["transaction_id", "score"])
    lookup = dict(
        zip(table.column("transaction_id").to_pylist(), table.column("score").to_pylist(), strict=True)
    )
    missing = sum(1 for tid in ids if tid not in lookup)
    if missing:
        raise ValueError(f"{missing} held-out IDs are absent from the score artifact")
    return np.array([lookup[tid] for tid in ids], dtype=np.float64)


def _time_calls(fn: Any, repeats: int, warmups: int) -> list[float]:
    for _ in range(warmups):
        fn()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - started) / 1e6)
    return samples


def run_model_workloads(
    model_path: Path,
    features: Path,
    scores_path: Path,
    *,
    single_repeats: int,
    single_warmups: int,
    batch_plan: dict[int, int],
    cohort_repeats: int,
    log: Any,
) -> dict[str, Any]:
    import lightgbm as lgb

    log("model: preparing held-out matrix")
    prep_started = time.perf_counter()
    ids, matrix = prepare_test_matrix(features)
    prep_seconds = time.perf_counter() - prep_started
    booster = lgb.Booster(model_file=str(model_path))
    if booster.num_feature() != matrix.shape[1]:
        raise ValueError(
            f"model expects {booster.num_feature()} features, matrix has {matrix.shape[1]}"
        )
    all_threads = os.cpu_count() or 1
    result: dict[str, Any] = {
        "rows": len(ids),
        "features": int(matrix.shape[1]),
        "num_trees": booster.num_trees(),
        "matrix_prep_seconds": prep_seconds,
        "timing_scope": (
            "booster.predict on a prepared float64 matrix; excludes feature retrieval, "
            "categorical encoding, graph access, and API serialization"
        ),
    }

    def predict(rows: np.ndarray, threads: int) -> np.ndarray:
        return booster.predict(rows, num_threads=threads)

    log("model: single prepared row")
    single = matrix[:1]
    result["single_prepared_row"] = {
        **summarize_ms(
            _time_calls(lambda: predict(single, 1), single_repeats, single_warmups)
        ),
        "warmups": single_warmups,
        "threads": 1,
    }
    batches: dict[str, Any] = {}
    for size, repeats in batch_plan.items():
        log(f"model: batch {size}")
        block = matrix[:size]
        batches[str(size)] = {
            **summarize_ms(_time_calls(lambda block=block: predict(block, 1), repeats, 3)),
            "warmups": 3,
            "threads": 1,
            "rows": size,
        }
        batches[str(size)]["rows_per_second_at_p50"] = size / (
            batches[str(size)]["p50_ms"] / 1000.0
        )
    result["batches"] = batches

    expected = saved_scores(scores_path, ids)
    process = psutil.Process()
    cohort: dict[str, Any] = {}
    for threads in sorted({1, all_threads}):
        log(f"model: full cohort with {threads} thread(s)")
        predict(matrix[:1000], threads)
        durations = []
        windows = []
        max_error = 0.0
        for _ in range(cohort_repeats):
            with ResourceWindow(process, interval=0.05) as window:
                started = time.perf_counter()
                scores = predict(matrix, threads)
                durations.append(time.perf_counter() - started)
            windows.append(window.result())
            max_error = max(max_error, float(np.max(np.abs(scores - expected))))
        median = float(np.median(durations))
        cpu = float(np.median([w["process_cpu_seconds"] for w in windows]))
        cohort[str(threads)] = {
            "rows": len(ids),
            "repeats": cohort_repeats,
            "threads": threads,
            "median_seconds": median,
            "min_seconds": min(durations),
            "max_seconds": max(durations),
            "transactions_per_second": len(ids) / median,
            "max_abs_error_vs_saved_scores": max_error,
            "cpu_seconds_median": cpu,
            "cpu_seconds_per_1000_transactions": cpu / len(ids) * 1000.0,
            "peak_rss_mib": max(w["process_rss_peak_mib"] for w in windows),
            "foreign_host_cores_max": max(w["foreign_host_cores"] for w in windows),
            "matrix_mib": matrix.nbytes / 2**20,
        }
    result["full_test_cohort"] = cohort
    result["score_parity"] = {
        "max_abs_error": max(entry["max_abs_error_vs_saved_scores"] for entry in cohort.values()),
        "exact": all(entry["max_abs_error_vs_saved_scores"] == 0.0 for entry in cohort.values()),
    }
    return result
