"""Graph-feature ablation path tests (synthetic fixtures only, no 5M data).

Covers: the graph file schema allowlist (target/split/extra columns fail
closed), duplicate and missing transaction IDs, the dense layout contract
that keeps categorical indices at [6,7,8], and a miniature end-to-end run
proving the flag adds a comparable lgbm_graph run while leaving the frozen
logreg/lgbm pair untouched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ml.baselines import (
    CATEGORICAL_INDICES,
    GRAPH_FEATURE_COLUMNS,
    NUMERIC_COLUMNS,
    check_joined_height,
    join_graph_features,
    validate_graph_features,
)

N_GRAPH = len(GRAPH_FEATURE_COLUMNS)


def write_graph_table(path: Path, ids: list[str], extra: dict[str, list] | None = None) -> None:
    """Write a minimal graph feature table with sequential integer values."""
    columns: dict[str, list] = {"transaction_id": ids}
    for position, name in enumerate(GRAPH_FEATURE_COLUMNS):
        if name == "gfeat_prior_reverse_flow":
            columns[name] = [i % 2 for i in range(len(ids))]
        else:
            columns[name] = [i + position for i in range(len(ids))]
    columns.update(extra or {})
    pq.write_table(pa.table(columns), path)


def test_schema_allowlist_rejects_label_column(tmp_path: Path) -> None:
    """Any label-like or extra column fails the file closed."""
    path = tmp_path / "graph.parquet"
    write_graph_table(path, ["tx-1"], extra={"target_is_laundering": [0]})
    with pytest.raises(ValueError, match="schema mismatch"):
        validate_graph_features(path)


def test_schema_rejects_missing_and_wrong_dtype(tmp_path: Path) -> None:
    """A missing feature or a non-integer column fails validation."""
    missing = tmp_path / "missing.parquet"
    table = pa.table({"transaction_id": ["tx-1"]})
    pq.write_table(table, missing)
    with pytest.raises(ValueError, match="schema mismatch"):
        validate_graph_features(missing)

    wrong_dtype = tmp_path / "wrong_dtype.parquet"
    pq.write_table(
        pa.table(
            {
                "transaction_id": ["tx-1"],
                **{name: [1.5] for name in GRAPH_FEATURE_COLUMNS},
            }
        ),
        wrong_dtype,
    )
    with pytest.raises(ValueError, match="not int64"):
        validate_graph_features(wrong_dtype)


def test_valid_file_returns_metadata(tmp_path: Path) -> None:
    """A clean file yields rows, columns, and its sha256."""
    path = tmp_path / "graph.parquet"
    write_graph_table(path, ["tx-1", "tx-2"])
    meta = validate_graph_features(path)
    assert meta["rows"] == 2
    assert meta["columns"] == ["transaction_id", *GRAPH_FEATURE_COLUMNS]
    assert meta["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_join_rejects_duplicates_and_missing() -> None:
    """Duplicate graph IDs fail the join; height checks catch missing IDs."""
    import polars as pl

    base = pl.DataFrame({"transaction_id": ["tx-1", "tx-2"], "split": ["train"] * 2})
    dup = pl.DataFrame(
        {
            "transaction_id": ["tx-1", "tx-1"],
            **{name: [1, 2] for name in GRAPH_FEATURE_COLUMNS},
        }
    )
    with pytest.raises(ValueError, match="graph ID join failed"):
        join_graph_features(base, dup)

    check_joined_height(2, 2, "train")
    with pytest.raises(ValueError, match="missing IDs"):
        check_joined_height(1, 2, "train")


def test_dense_layout_keeps_categorical_indices() -> None:
    """Graph numerics append after the codes: cats stay at [6,7,8]."""
    baseline_numerics = list(NUMERIC_COLUMNS)
    assert len(baseline_numerics) == 6
    dense_order = baseline_numerics + ["code_0", "code_1", "code_2"] + list(GRAPH_FEATURE_COLUMNS)
    assert dense_order[6:9] == ["code_0", "code_1", "code_2"]
    assert list(CATEGORICAL_INDICES) == [6, 7, 8]
    assert len(dense_order) == 6 + 3 + N_GRAPH


def build_mini_fixture(tmp_path: Path, n_per_split: int = 200) -> dict[str, Path]:
    """Write synthetic features/prepared/graph tables plus manifests."""
    import numpy as np

    rng = np.random.default_rng(7)
    splits = (
        ["train"] * (2 * n_per_split) + ["validation"] * n_per_split + ["test"] * n_per_split
    )
    total = len(splits)
    ids = [f"tx-{i:05d}" for i in range(total)]
    labels = [(i % 11 == 0) for i in range(total)]

    features = {
        "transaction_id": ids,
        "split": splits,
        "log_amount_paid": rng.normal(5.0, 1.0, total).tolist(),
        "log_amount_received": rng.normal(5.0, 1.0, total).tolist(),
        "payment_currency": rng.choice(["US Dollar", "Euro"], total).tolist(),
        "receiving_currency": rng.choice(["US Dollar", "Euro"], total).tolist(),
        "payment_format": rng.choice(["Wire", "Cash"], total).tolist(),
        "sender_prior_out": rng.integers(0, 20, total).tolist(),
        "sender_prior_in": rng.integers(0, 20, total).tolist(),
        "receiver_prior_out": rng.integers(0, 20, total).tolist(),
        "receiver_prior_in": rng.integers(0, 20, total).tolist(),
    }
    features_path = tmp_path / "features.parquet"
    pq.write_table(pa.table(features), features_path)

    prepared = {
        "transaction_id": ids,
        "split": splits,
        "target_is_laundering": [int(v) for v in labels],
    }
    prepared_path = tmp_path / "prepared.parquet"
    pq.write_table(pa.table(prepared), prepared_path)
    prepared_sha = hashlib.sha256(prepared_path.read_bytes()).hexdigest()

    graph_path = tmp_path / "graph.parquet"
    write_graph_table(graph_path, ids)

    manifest = {
        "source_parquet": str(prepared_path),
        "source_sha256": "0" * 64,
        "prepared_parquet_sha256": prepared_sha,
        "split_manifest_sha256": "0" * 64,
        "feature_columns": [
            "log_amount_paid",
            "log_amount_received",
            "payment_currency",
            "receiving_currency",
            "payment_format",
            "sender_prior_out",
            "sender_prior_in",
            "receiver_prior_out",
            "receiver_prior_in",
        ],
        "split_counts": {
            "train": 2 * n_per_split,
            "validation": n_per_split,
            "test": n_per_split,
        },
    }
    manifest_path = tmp_path / "features-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return {
        "features": features_path,
        "manifest": manifest_path,
        "graph": graph_path,
        "out": tmp_path / "out",
    }


def run_baselines(fixture: dict[str, Path], *extra_args: str) -> dict:
    """Run the trainer CLI on a fixture and return its metrics."""
    from ml.baselines import main

    out = fixture["out"]
    argv = [
        "--features",
        str(fixture["features"]),
        "--features-manifest",
        str(fixture["manifest"]),
        "--output-dir",
        str(out),
        "--alert-budget",
        "0.05",
        *extra_args,
    ]
    assert main(argv) == 0
    return json.loads((out / "baseline_metrics.json").read_text(encoding="utf-8"))


def test_mini_run_without_flag_has_frozen_pair(tmp_path: Path) -> None:
    """Omitted flag yields exactly the logreg/lgbm pair, no graph keys."""
    fixture = build_mini_fixture(tmp_path)
    metrics = run_baselines(fixture)
    assert set(metrics) == {"logreg", "lgbm"}
    config = json.loads((fixture["out"] / "baseline_config.json").read_text(encoding="utf-8"))
    assert config["graph_features"] is None
    assert "lgbm_graph_score" not in [
        field.name
        for field in pq.read_schema(fixture["out"] / "test_scores.parquet")
    ]


def test_mini_run_with_flag_adds_comparable_graph_run(tmp_path: Path) -> None:
    """Flagged run adds lgbm_graph on identical splits without moving the pair."""
    fixture = build_mini_fixture(tmp_path)
    plain = run_baselines(fixture)
    flagged = run_baselines(
        fixture, "--graph-features", str(fixture["graph"])
    )
    assert set(flagged) == {"logreg", "lgbm", "lgbm_graph"}
    for name in ("logreg", "lgbm"):
        assert (
            flagged[name]["average_precision"] == plain[name]["average_precision"]
        )
        assert (
            flagged[name]["test_at_val_threshold"] == plain[name]["test_at_val_threshold"]
        )
    graph_entry = flagged["lgbm_graph"]
    assert set(graph_entry) == set(flagged["lgbm"])
    assert graph_entry["splits"]["test"]["rows"] == 200
    schema_names = pq.read_schema(fixture["out"] / "test_scores.parquet").names
    assert "lgbm_graph_score" in schema_names
    config = json.loads((fixture["out"] / "baseline_config.json").read_text(encoding="utf-8"))
    assert config["graph_features"]["rows"] == 800
    assert config["graph_features"]["columns"] == [
        "transaction_id",
        *GRAPH_FEATURE_COLUMNS,
    ]
    assert config["categorical_feature_indices"] == [6, 7, 8]
