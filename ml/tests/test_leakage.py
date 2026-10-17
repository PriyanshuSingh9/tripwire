"""Leakage and point-in-time semantics tests (user-approved seams).

Covers: the target never appears in feature artifacts or model feature
lists; same-minute peer transactions never inform each other; strictly
earlier activity is counted; later activity is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ml.features import FEATURE_COLUMNS, create_prior_views

LABEL_COLUMN = "target_is_laundering"

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURES_PARQUET = REPO_ROOT / "artifacts/runs/hi-small-v8/baselines/features.parquet"
FEATURES_MANIFEST = REPO_ROOT / "artifacts/runs/hi-small-v8/baselines/features-manifest.json"
BASELINE_CONFIG = REPO_ROOT / "artifacts/runs/hi-small-v8/baselines/baseline_config.json"

requires_artifacts = pytest.mark.skipif(
    not (FEATURES_PARQUET.is_file() and FEATURES_MANIFEST.is_file()),
    reason="generated run artifacts are absent",
)


@requires_artifacts
def test_target_absent_from_feature_artifacts() -> None:
    """The offline target must not appear in the feature table or its manifest."""
    import pyarrow.parquet as pq

    schema_names = pq.read_schema(FEATURES_PARQUET).names
    assert LABEL_COLUMN not in schema_names
    manifest = json.loads(FEATURES_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["feature_columns"] == list(FEATURE_COLUMNS)
    assert LABEL_COLUMN not in manifest["feature_columns"]
    assert any("target label" in entry for entry in manifest["excluded"])


@requires_artifacts
def test_target_absent_from_model_feature_lists() -> None:
    """Model configs must list features without the target column."""
    if not BASELINE_CONFIG.is_file():
        pytest.skip("baseline config is absent")
    config = json.loads(BASELINE_CONFIG.read_text(encoding="utf-8"))
    assert LABEL_COLUMN not in config["feature_columns"]
    assert config["label_column"] == LABEL_COLUMN
    assert set(config["categorical_columns"]) <= set(config["feature_columns"])


def build_synthetic_tx(con: object) -> None:
    """Create a tiny tx view exercising simultaneity and ordering.

    t1/t2 share a minute (peers), t3 follows, t4 is later activity for A
    but earlier activity for nobody at t3, t5 closes A's history.
    """
    con.execute(
        "CREATE OR REPLACE TEMP VIEW tx AS SELECT * FROM (VALUES "
        "('t1', 'A', 'B', TIMESTAMP '2022-09-01 10:00'), "
        "('t2', 'A', 'C', TIMESTAMP '2022-09-01 10:00'), "
        "('t3', 'A', 'D', TIMESTAMP '2022-09-01 10:01'), "
        "('t4', 'B', 'A', TIMESTAMP '2022-09-01 10:02'), "
        "('t5', 'A', 'E', TIMESTAMP '2022-09-01 10:03')) "
        "AS t(id, sender_account_key, receiver_account_key, timestamp)"
    )


def fetch_priors(con: object) -> dict[str, tuple[int, int, int, int]]:
    """Return (sender_out, sender_in, receiver_out, receiver_in) per tx id."""
    rows = con.execute(
        "SELECT t.id, sp.prior_out, sp.prior_in, rp.prior_out, rp.prior_in "
        "FROM tx t "
        "JOIN account_prior sp ON sp.account_key = t.sender_account_key "
        "AND sp.ts = t.timestamp "
        "JOIN account_prior rp ON rp.account_key = t.receiver_account_key "
        "AND rp.ts = t.timestamp "
        "ORDER BY t.id"
    ).fetchall()
    return {tx_id: (s_out, s_in, r_out, r_in) for tx_id, s_out, s_in, r_out, r_in in rows}


def test_same_minute_peers_excluded() -> None:
    """Same-minute peer transactions must not inform each other."""
    import duckdb

    con = duckdb.connect()
    try:
        build_synthetic_tx(con)
        create_prior_views(con, "tx")
        priors = fetch_priors(con)
    finally:
        con.close()
    # t2 sees none of t1's activity despite sharing the minute.
    assert priors["t1"][:2] == (0, 0)
    assert priors["t2"][:2] == (0, 0)


def test_past_activity_included() -> None:
    """Strictly earlier sends and receives must be counted."""
    import duckdb

    con = duckdb.connect()
    try:
        build_synthetic_tx(con)
        create_prior_views(con, "tx")
        priors = fetch_priors(con)
    finally:
        con.close()
    # t3 sees both t1+t2 sends; t5 additionally sees t4's incoming transfer.
    assert priors["t3"][:2] == (2, 0)
    assert priors["t5"][:2] == (3, 1)
    # t4's sender B had received once at t1 and never sent.
    assert priors["t4"][:2] == (0, 1)


def test_future_activity_excluded() -> None:
    """Later activity must not leak into earlier priors."""
    import duckdb

    con = duckdb.connect()
    try:
        build_synthetic_tx(con)
        create_prior_views(con, "tx")
        priors = fetch_priors(con)
    finally:
        con.close()
    # t3 predates t4/t5: neither A's later send nor B's later activity counts.
    assert priors["t3"] == (2, 0, 0, 0)
    # t4 predates t5.
    assert priors["t4"] == (0, 1, 3, 0)
