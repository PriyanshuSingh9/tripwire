"""Unit tests for the vectorized offline graph feature builder.

No Docker, no dataset. Oracles: hand-checked expectations, a direct
brute-force evaluation of the written semantics, and the streaming builder
itself running against the in-memory fake feature graph.
"""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_feature_stream import FakeFeatureGraph, write_input_parquet

from graph import feature_batch, feature_stream, synthetic_transactions

WINDOWS = (60, 90, 3_600, 86_400)


def run_batch(tmp_path: Path, parquet: Path, *extra: str, name: str = "batch") -> pa.Table:
    """Run the batch CLI and return its output table."""
    out = tmp_path / f"{name}.parquet"
    code = feature_batch.main([
        "--parquet", str(parquet),
        "--features-out", str(out),
        "--manifest-out", str(tmp_path / f"{name}.json"),
        "--spill-dir", str(tmp_path / "spill"),
        *extra,
    ])
    assert code == 0
    return pq.read_table(out)


def by_id(table: pa.Table) -> dict[str, dict[str, int]]:
    return {row.pop("transaction_id"): row for row in table.to_pylist()}


def brute_force(parquet: Path, window: int) -> dict[str, dict[str, int]]:
    """Directly evaluate the documented semantics (independent of both builders)."""
    source = pq.read_table(parquet, columns=list(feature_stream.FEATURE_COLUMNS)).to_pylist()
    rows = [
        (r["transaction_id"], feature_stream.minute_of_epoch(feature_stream.epoch_of(r["timestamp"])),
         r["sender_account_key"], r["receiver_account_key"])
        for r in source
    ]
    pair_minutes: dict[tuple[str, str], list[int]] = defaultdict(list)
    for _, minute, sender, receiver in rows:
        pair_minutes[(sender, receiver)].append(minute)
    for minutes in pair_minutes.values():
        minutes.sort()
    out_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    in_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for sender, receiver in pair_minutes:
        out_pairs[sender].append((sender, receiver))
        in_pairs[receiver].append((sender, receiver))

    def active(pair: tuple[str, str], minute: int) -> bool:
        minutes = pair_minutes[pair]
        latest = bisect.bisect_left(minutes, minute) - 1  # strictly earlier minutes only
        return latest >= 0 and minutes[latest] >= minute - window

    def count(index: dict[str, list[tuple[str, str]]], account: str, minute: int) -> int:
        return sum(1 for pair in index.get(account, []) if active(pair, minute))

    result = {}
    for txid, minute, sender, receiver in rows:
        reverse = pair_minutes.get((receiver, sender), [])
        result[txid] = {
            "gfeat_sender_out_24h": count(out_pairs, sender, minute),
            "gfeat_sender_in_24h": count(in_pairs, sender, minute),
            "gfeat_receiver_out_24h": count(out_pairs, receiver, minute),
            "gfeat_receiver_in_24h": count(in_pairs, receiver, minute),
            "gfeat_prior_reverse_flow": int(bool(reverse) and reverse[0] < minute),
        }
    return result


@pytest.fixture(scope="module")
def adversarial_parquet(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("adv") / "adv.parquet"
    pq.write_table(synthetic_transactions.adversarial_table(seed=11, random_rows=600), path)
    return path


@pytest.mark.parametrize("window", WINDOWS)
def test_matches_brute_force_semantics(tmp_path, adversarial_parquet, window) -> None:
    table = run_batch(tmp_path, adversarial_parquet, "--window-seconds", str(window))
    assert by_id(table) == brute_force(adversarial_parquet, window)


@pytest.mark.parametrize("window", (90, 86_400))
def test_matches_streaming_builder_on_fake_graph(
    tmp_path, adversarial_parquet, window, monkeypatch
) -> None:
    monkeypatch.setattr(feature_stream, "connect", lambda *args: FakeFeatureGraph())
    stream_out = tmp_path / "stream.parquet"
    assert feature_stream.main([
        "--parquet", str(adversarial_parquet),
        "--features-out", str(stream_out),
        "--manifest-out", str(tmp_path / "stream.json"),
        "--spill-dir", str(tmp_path / "stream-spill"),
        "--window-seconds", str(window),
    ]) == 0
    expected = pq.read_table(stream_out)
    actual = run_batch(tmp_path, adversarial_parquet, "--window-seconds", str(window))
    assert actual.schema == expected.schema
    assert actual.sort_by("transaction_id").equals(expected.sort_by("transaction_id"))


def test_hand_cases(tmp_path) -> None:
    parquet = tmp_path / "in.parquet"
    write_input_parquet(parquet, [
        ("t1", datetime(2022, 9, 1, 0, 0), "A", "B"),
        ("t2", datetime(2022, 9, 2, 0, 0), "B", "C"),     # exactly +24 h: A counts
        ("t3", datetime(2022, 9, 2, 0, 1), "B", "D"),     # +24 h 1 min: A expired
        ("t4", datetime(2022, 9, 2, 0, 0, 59), "C", "B"),  # same minute as t2
        ("t5", datetime(2022, 9, 2, 0, 2), "C", "B"),     # reverse of t2
        ("t6", datetime(2022, 9, 2, 0, 3), "S", "S"),
        ("t7", datetime(2022, 9, 2, 0, 3), "S", "S"),     # duplicate self-transfer
        ("t8", datetime(2022, 9, 2, 0, 4), "S", "S"),
    ])
    rows = by_id(run_batch(tmp_path, parquet))
    assert rows["t2"]["gfeat_sender_in_24h"] == 1
    assert rows["t3"]["gfeat_sender_in_24h"] == 1  # C (t4) only; A has expired
    assert rows["t3"]["gfeat_sender_out_24h"] == 1  # B->C at the previous minute
    assert rows["t4"]["gfeat_prior_reverse_flow"] == 0  # same-minute peer invisible
    assert rows["t4"]["gfeat_receiver_out_24h"] == 0
    assert rows["t5"]["gfeat_prior_reverse_flow"] == 1
    assert rows["t5"]["gfeat_sender_in_24h"] == 1
    assert rows["t6"] == rows["t7"]
    assert rows["t6"]["gfeat_sender_out_24h"] == 0
    assert rows["t8"] == {
        "gfeat_sender_out_24h": 1, "gfeat_sender_in_24h": 1,
        "gfeat_receiver_out_24h": 1, "gfeat_receiver_in_24h": 1,
        "gfeat_prior_reverse_flow": 1,
    }


def test_limit_drops_trailing_minute_like_streaming_builder(tmp_path) -> None:
    parquet = tmp_path / "in.parquet"
    write_input_parquet(parquet, [
        ("t1", datetime(2022, 9, 1, 0, 1), "A::1", "B::2"),
        ("t2", datetime(2022, 9, 1, 0, 1), "A::1", "C::3"),
        ("t3", datetime(2022, 9, 1, 0, 2), "B::2", "C::3"),
        ("t4", datetime(2022, 9, 1, 0, 2), "C::3", "A::1"),
    ])
    table = run_batch(tmp_path, parquet, "--limit", "3")
    assert table.column("transaction_id").to_pylist() == ["t1", "t2"]
    manifest = json.loads((tmp_path / "batch.json").read_text(encoding="utf-8"))
    assert manifest["output"]["dropped_trailing_partial"] == 1
    assert manifest["output"]["rows_seen"] == 3
    # A limit at or above the row count is a full build (nothing dropped).
    full = run_batch(tmp_path, parquet, "--limit", "4", name="full")
    assert full.num_rows == 4


def test_manifest_and_schema(tmp_path, adversarial_parquet) -> None:
    table = run_batch(tmp_path, adversarial_parquet)
    assert tuple(table.column_names) == feature_stream.OUTPUT_SCHEMA_NAMES
    assert table.schema == feature_batch.output_schema()
    ids = table.column("transaction_id").to_pylist()
    assert len(ids) == len(set(ids)) == pq.read_metadata(adversarial_parquet).num_rows
    manifest = json.loads((tmp_path / "batch.json").read_text(encoding="utf-8"))
    assert manifest["tool"] == "graph.feature_batch"
    assert manifest["columns_read"] == list(feature_stream.FEATURE_COLUMNS)
    assert manifest["columns_excluded_proof"]["never_selected"] == ["target_is_laundering", "split"]
    assert len(manifest["source"]["sha256"]) == 64
    assert manifest["output"]["rows_written"] == len(ids)
    assert "total_seconds" in manifest["timings"]
    assert "UTC" in manifest["utc_assumption"]


def test_never_reads_label_or_split_columns(tmp_path, adversarial_parquet) -> None:
    full = pq.read_table(adversarial_parquet)
    poisoned = full.set_column(
        full.column_names.index("target_is_laundering"), "target_is_laundering",
        pa.array([None] * full.num_rows, type=pa.int8()),
    ).drop_columns(["split"])
    poisoned_path = tmp_path / "poisoned.parquet"
    pq.write_table(poisoned, poisoned_path)
    assert run_batch(tmp_path, poisoned_path, name="poisoned").equals(
        run_batch(tmp_path, adversarial_parquet, name="clean")
    )


def test_rejects_null_account_keys(tmp_path, capsys) -> None:
    parquet = tmp_path / "in.parquet"
    pq.write_table(pa.table({
        "transaction_id": ["t1"],
        "timestamp": pa.array([datetime(2022, 9, 1)], type=pa.timestamp("us")),
        "sender_account_key": pa.array([None], type=pa.string()),
        "receiver_account_key": ["B"],
    }), parquet)
    code = feature_batch.main([
        "--parquet", str(parquet), "--features-out", str(tmp_path / "o.parquet"),
        "--manifest-out", str(tmp_path / "o.json"), "--spill-dir", str(tmp_path / "s"),
    ])
    assert code == 1
    assert "null values" in capsys.readouterr().out


def test_minute_epochs_mirror_streaming_conversion() -> None:
    import numpy as np

    moments = [datetime(2022, 9, 1, 0, 0, 59, 999_999), datetime(1969, 12, 31, 23, 59, 30, 500_000)]
    micros = np.array(
        [synthetic_transactions.micros_of(moment) for moment in moments], dtype=np.int64
    )
    expected = [feature_stream.minute_of_epoch(feature_stream.epoch_of(m)) for m in moments]
    assert feature_batch.minute_epochs_from_micros(micros).tolist() == expected


def test_empty_input_computes_nothing() -> None:
    import numpy as np

    data = feature_batch.BatchInput(
        transaction_id=pa.array([], type=pa.string()),
        minute_epoch=np.zeros(0, dtype=np.int64),
        sender=np.zeros(0, dtype=np.int64),
        receiver=np.zeros(0, dtype=np.int64),
        account_count=0,
    )
    features = feature_batch.compute_features(data, window_seconds=60)
    assert features.sender_out.size == 0
    assert feature_batch.features_table(data, features).num_rows == 0
