"""Experiment runner tests: metric definitions and a synthetic end-to-end run."""

from __future__ import annotations

import copy
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ml.experiment import (
    TEST_LEDGER,
    VALIDATION_LEDGER,
    SplitData,
    cohort_metrics,
    config_columns,
    config_hash,
    currency_weighted_recall,
    main,
    split_report,
)


def test_currency_weighted_recall_never_mixes_currencies() -> None:
    y = np.array([1, 1, 1, 0, 1])
    amount = np.array([100.0, 300.0, 50.0, 999.0, 7.0])
    currency = np.array(["USD", "USD", "EUR", "USD", "EUR"])
    alerts = np.array([1, 3, 4])
    report = currency_weighted_recall(alerts, y, amount, currency)
    assert set(report) == {"USD", "EUR"}
    assert report["USD"]["weighted_recall"] == pytest.approx(300 / 400)
    assert report["EUR"]["weighted_recall"] == pytest.approx(7 / 57)
    assert report["USD"]["positives"] == 2
    assert report["EUR"]["alerted_positives"] == 1


def test_cohort_metrics_budget_and_threshold() -> None:
    scores = np.linspace(1.0, 0.0, 2000)
    y = np.zeros(2000, dtype=np.int8)
    y[[0, 1, 5]] = 1
    amount = np.ones(2000)
    currency = np.array(["USD"] * 2000)
    metrics = cohort_metrics(scores, y, amount, currency, threshold=scores[5], budget=0.001)
    assert metrics["at_alert_budget"]["alerts"] == 2
    assert metrics["at_alert_budget"]["true_positives"] == 2
    assert metrics["at_val_threshold"]["alerts"] == 6
    assert metrics["at_val_threshold"]["recall"] == 1.0


def test_split_report_separates_bulk_and_tail() -> None:
    n = 3000
    day = np.array(["2022-09-09"] * 2000 + ["2022-09-10"] * 990 + ["2022-09-12"] * 10)
    y = np.zeros(n, dtype=np.int8)
    y[-10:] = 1
    y[:3] = 1
    scores = np.zeros(n)
    scores[-10:] = 1.0
    data = SplitData(
        X=np.empty((n, 0), dtype=np.float32), y=y, ids=[str(i) for i in range(n)], day=day,
        amount_paid=np.ones(n), payment_currency=np.array(["USD"] * n),
    )
    report = split_report(scores, data, threshold=0.5)
    assert report["bulk_sept_9_10"]["rows"] == 2990
    assert report["bulk_sept_9_10"]["positives"] == 3
    assert report["tail_after_sept_10"] == {
        "rows": 10, "positives": 10,
        "true_positives_at_val_threshold": 10, "alerts_at_val_threshold": 10,
    }
    assert report["all"]["at_alert_budget"]["alerts_in_tail"] == 3


def test_config_columns_drops_and_rejects_unknown() -> None:
    manifest = {"families": {"a": {"columns": ["x", "y"]}, "b": {"columns": ["z"]}}}
    assert config_columns({"families": ["a", "b"], "drop_columns": ["y"]}, manifest) == [
        "x", "z",
    ]
    with pytest.raises(ValueError, match="drop_columns"):
        config_columns({"families": ["a"], "drop_columns": ["z"]}, manifest)


def write_prepared(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Synthetic prepared table + split manifest + account registry."""
    rng = random.Random(5)
    accounts = [(f"0{b}", f"A{i}") for i in range(40) for b in (1, 2) if (i + b) % 2 == 0]
    start = datetime(2022, 9, 1)
    rows = []
    for index in range(6000):
        sender, receiver = rng.sample(accounts, 2)
        minute = rng.randrange(0, 12 * 24 * 60)
        label = int(rng.random() < 0.03)
        amount = rng.uniform(10, 5000) * (3 if label else 1)
        rows.append((minute, sender, receiver, amount, label))
    rows.sort(key=lambda r: r[0])
    cut1, cut2 = 7 * 24 * 60, 9 * 24 * 60
    table = {
        name: [] for name in (
            "transaction_id", "timestamp", "sender_bank", "sender_account", "receiver_bank",
            "receiver_account", "sender_account_key", "receiver_account_key", "amount_paid",
            "payment_currency", "amount_received", "receiving_currency", "payment_format",
            "target_is_laundering", "source_row_number", "split",
        )
    }
    for index, (minute, (sb, sa), (rb, ra), amount, label) in enumerate(rows, start=1):
        split = "train" if minute <= cut1 else "validation" if minute <= cut2 else "test"
        values = (
            f"hi8-tx-{index:08d}", start + timedelta(minutes=minute), sb, sa, rb, ra,
            f"{sb}::{sa}", f"{rb}::{ra}", round(amount, 2), "US Dollar", round(amount, 2),
            "US Dollar", "ACH" if label or rng.random() < 0.3 else "Cheque", label, index, split,
        )
        for name, value in zip(table, values, strict=True):
            table[name].append(value)
    prepared = tmp_path / "transactions.parquet"
    pq.write_table(pa.table(table), prepared)
    splits = {
        s: {"rows": table["split"].count(s)} for s in ("train", "validation", "test")
    }
    manifest = tmp_path / "split-manifest.json"
    manifest.write_text(json.dumps({"splits": splits}), encoding="utf-8")
    registry = tmp_path / "accounts.csv"
    lines = ["Bank Name,Bank ID,Account Number,Entity ID,Entity Name"]
    for i, (bank, account) in enumerate(accounts):
        lines.append(f"B,{bank.lstrip('0')},{account},E{i // 3},Corporation #{i}")
    registry.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return prepared, manifest, registry


SMALL_PARAMS = {"min_data_in_leaf": 5, "num_leaves": 7, "num_threads": 2}


def make_grid(path: Path) -> list[dict]:
    """Write a two-candidate validation grid and return its configs."""
    configs = [
        {"name": "base", "families": ["base"], "params": SMALL_PARAMS, "max_rounds": 30,
         "early_stopping_rounds": 10},
        {"name": "full", "families": ["base", "txlocal", "velocity", "amount", "counterparty",
                                      "cycle", "kyc"],
         "neg_sample_rate": 0.5, "params": SMALL_PARAMS, "max_rounds": 30,
         "early_stopping_rounds": 10},
    ]
    path.write_text(json.dumps(configs), encoding="utf-8")
    return configs


def write_candidates(path: Path, configs: list[dict], names: list[str]) -> None:
    """Write a candidate manifest pinning the config hash of the named configs."""
    entries = [
        {"id": config["name"], "name": config["name"], "config_hash": config_hash(config)}
        for config in configs
        if config["name"] in names
    ]
    path.write_text(
        json.dumps(
            {"declared_before_test": True, "primary": entries[0]["name"], "candidates": entries}
        ),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Synthetic prepared table plus a feature build shared across tests."""
    root = tmp_path_factory.mktemp("experiment")
    prepared, split_manifest, registry = write_prepared(root)
    features_dir = root / "features"
    assert main([
        "features", "--source-parquet", str(prepared), "--split-manifest", str(split_manifest),
        "--accounts-csv", str(registry), "--output-dir", str(features_dir), "--threads", "2",
    ]) == 0
    grid = root / "grid.json"
    configs = make_grid(grid)
    return {"root": root, "prepared": prepared, "features_dir": features_dir,
            "grid": grid, "configs": configs}


def fit_args(env: dict, run_dir: Path) -> list[str]:
    """Shared validate/test argument list for a run directory."""
    return [
        "--features-dir", str(env["features_dir"]), "--prepared-parquet", str(env["prepared"]),
        "--grid", str(env["grid"]), "--output-dir", str(run_dir),
    ]


def test_end_to_end_runner(env: dict) -> None:
    run_dir = env["root"] / "run-e2e"
    candidates = env["root"] / "candidates-e2e.json"
    write_candidates(candidates, env["configs"], ["full"])
    assert "target_is_laundering" not in pq.read_schema(
        env["features_dir"] / "features.parquet"
    ).names

    assert main(["validate", *fit_args(env, run_dir)]) == 0
    records = [
        json.loads(line)
        for line in (run_dir / VALIDATION_LEDGER).read_text(encoding="utf-8").splitlines()
    ]
    assert [r["name"] for r in records] == ["base", "full"]
    assert all("test" not in r for r in records)
    assert not (run_dir / TEST_LEDGER).exists()

    assert main([
        "test", *fit_args(env, run_dir), "--candidates-manifest", str(candidates),
        "--candidate", "full",
    ]) == 0
    ledger = (run_dir / TEST_LEDGER).read_text(encoding="utf-8").splitlines()
    assert len(ledger) == 1
    entry = json.loads(ledger[0])
    assert entry["candidate"] == "full"
    # Model, score, features and split hashes are recorded together.
    run_config = json.loads(
        (run_dir / "candidates" / "full" / "config.json").read_text(encoding="utf-8")
    )
    for key in ("model_sha256", "test_scores_sha256", "split_manifest_sha256",
                "features_manifest_sha256"):
        assert len(run_config[key]) == 64
        assert entry[key] == run_config[key]
    # Retraining the candidate reproduces the validation run deterministically.
    assert entry["validation"]["all"]["average_precision"] == pytest.approx(
        records[1]["validation"]["average_precision"], abs=0, rel=0
    )

    assert main([
        "publish", "--features-dir", str(env["features_dir"]), "--output-dir", str(run_dir),
        "--candidate", "full",
    ]) == 0
    published = pq.read_table(run_dir / "inference_scores.parquet")
    assert published.column_names == ["transaction_id", "timestamp", "score"]
    api_config = json.loads((run_dir / "api_threshold_config.json").read_text(encoding="utf-8"))
    assert set(api_config["thresholds"]) == {"lgbm"}
    assert "target_is_laundering" not in api_config["feature_columns"]
    assert (run_dir / "candidates" / "full" / "validation_scores.parquet").is_file()


def test_unknown_candidate_refused(env: dict) -> None:
    """A candidate absent from the declared manifest fails closed before training."""
    run_dir = env["root"] / "run-unknown"
    candidates = env["root"] / "candidates-unknown.json"
    write_candidates(candidates, env["configs"], ["base"])
    assert main([
        "test", *fit_args(env, run_dir), "--candidates-manifest", str(candidates),
        "--candidate", "full",
    ]) == 2
    assert not (run_dir / TEST_LEDGER).exists()


def test_repeat_test_evaluation_refused(env: dict) -> None:
    """The same candidate and experiment id cannot append a second ledger row."""
    run_dir = env["root"] / "run-repeat"
    candidates = env["root"] / "candidates-repeat.json"
    write_candidates(candidates, env["configs"], ["full"])
    test_args = [
        "test", *fit_args(env, run_dir), "--candidates-manifest", str(candidates),
        "--candidate", "full",
    ]
    assert main(test_args) == 0
    ledger_path = run_dir / TEST_LEDGER
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 1
    assert main(test_args) == 2
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 1


def test_changed_config_hash_is_new_experiment(env: dict) -> None:
    """A modified config is a new experiment; it never overwrites old artifacts."""
    run_dir = env["root"] / "run-hash"
    candidates = env["root"] / "candidates-hash.json"
    write_candidates(candidates, env["configs"], ["full"])
    assert main([
        "test", *fit_args(env, run_dir), "--candidates-manifest", str(candidates),
        "--candidate", "full",
    ]) == 0
    original = (run_dir / "candidates" / "full" / "metrics.json").read_bytes()

    changed = copy.deepcopy(env["configs"])
    for config in changed:
        if config["name"] == "full":
            config["params"] = {**SMALL_PARAMS, "num_leaves": 9}
    changed_grid = env["root"] / "grid-hash.json"
    changed_grid.write_text(json.dumps(changed), encoding="utf-8")
    changed_args = [
        "--features-dir", str(env["features_dir"]), "--prepared-parquet", str(env["prepared"]),
        "--grid", str(changed_grid), "--output-dir", str(run_dir),
        "--candidates-manifest", str(candidates), "--candidate", "full",
    ]
    # The changed config no longer matches the declared hash: fail closed.
    assert main(["test", *changed_args]) == 2
    assert (run_dir / "candidates" / "full" / "metrics.json").read_bytes() == original
    assert not (run_dir / "candidates" / "full__exp2").exists()

    # Re-declaring the changed config under an explicit experiment id is a new
    # experiment and preserves the earlier artifacts.
    write_candidates(candidates, changed, ["full"])
    assert main(["test", *changed_args, "--experiment-id", "exp2"]) == 0
    assert (run_dir / "candidates" / "full" / "metrics.json").read_bytes() == original
    assert (run_dir / "candidates" / "full__exp2" / "metrics.json").is_file()
    rows = [
        json.loads(line)
        for line in (run_dir / TEST_LEDGER).read_text(encoding="utf-8").splitlines()
    ]
    assert [row["experiment_id"] for row in rows] == [None, "exp2"]
