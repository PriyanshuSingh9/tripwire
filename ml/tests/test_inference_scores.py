"""Tests for publishing label-free replay score artifacts."""

from __future__ import annotations

from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ml.inference_scores import publish_scores


def write_table(path, table: pa.Table) -> None:
    pq.write_table(table, path)


def test_publish_scores_does_not_copy_evaluation_labels(tmp_path) -> None:
    scores = tmp_path / "scores.parquet"
    features = tmp_path / "features.parquet"
    output = tmp_path / "inference.parquet"
    write_table(
        scores,
        pa.table(
            {
                "transaction_id": ["tx-a", "tx-b"],
                "target_is_laundering": [1, 0],
                "lgbm_score": [0.91, 0.07],
            }
        ),
    )
    write_table(
        features,
        pa.table(
            {
                "transaction_id": ["tx-a", "tx-b"],
                "timestamp": [
                    datetime(2022, 9, 1, 0, 0),
                    datetime(2022, 9, 1, 0, 1),
                ],
                "split": ["test", "test"],
            }
        ),
    )

    assert publish_scores(scores, features, output) == 2

    result = pq.read_table(output)
    assert result.column_names == ["transaction_id", "timestamp", "score"]
    assert result["score"].to_pylist() == [0.91, 0.07]
    assert "target_is_laundering" not in result.column_names


def test_publish_scores_rejects_unreconciled_rows(tmp_path) -> None:
    scores = tmp_path / "scores.parquet"
    features = tmp_path / "features.parquet"
    write_table(
        scores,
        pa.table({"transaction_id": ["tx-a"], "lgbm_score": [0.91]}),
    )
    write_table(
        features,
        pa.table(
            {
                "transaction_id": ["tx-b"],
                "timestamp": [datetime(2022, 9, 1, 0, 0)],
                "split": ["test"],
            }
        ),
    )

    with pytest.raises(ValueError, match="do not reconcile"):
        publish_scores(scores, features, tmp_path / "out.parquet")
