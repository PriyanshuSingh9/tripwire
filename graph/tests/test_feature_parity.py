"""Row-for-row parity: FalkorDB streaming builder vs vectorized batch builder.

Runs both builders on the same seeded adversarial synthetic Parquet and
asserts identical output tables. Requires a reachable FalkorDB (default
``localhost:6379`` or ``TRACEAML_FALKORDB_HOST``/``TRACEAML_FALKORDB_PORT``);
skips otherwise. Uses a throwaway feature graph named ``agentfeat-parity-*``
that is deleted afterwards.
"""

from __future__ import annotations

import os
import uuid

import pyarrow.parquet as pq
import pytest

from graph import feature_batch, feature_stream, synthetic_transactions

HOST = os.environ.get("TRACEAML_FALKORDB_HOST", "localhost")
PORT = int(os.environ.get("TRACEAML_FALKORDB_PORT", "6379"))


def falkordb_client():
    try:
        from falkordb import FalkorDB

        client = FalkorDB(host=HOST, port=PORT, socket_timeout=2)
        client.list_graphs()
        return client
    except Exception:  # noqa: BLE001 - any connection failure means skip
        return None


CLIENT = falkordb_client()
pytestmark = pytest.mark.skipif(CLIENT is None, reason="FalkorDB not reachable")


@pytest.fixture()
def graph_name():
    name = f"agentfeat-parity-{uuid.uuid4().hex[:12]}"
    yield name
    if CLIENT is not None and name in CLIENT.list_graphs():
        CLIENT.select_graph(name).delete()


@pytest.mark.parametrize(
    ("window", "limit"),
    [(86_400, None), (3_600, None), (86_400, 1_500)],
)
def test_streaming_and_batch_builders_agree(tmp_path, graph_name, window, limit) -> None:
    parquet = tmp_path / "adversarial.parquet"
    pq.write_table(synthetic_transactions.adversarial_table(seed=7), parquet)
    common = ["--parquet", str(parquet), "--window-seconds", str(window)]
    if limit is not None:
        common += ["--limit", str(limit)]

    assert feature_stream.main([
        *common,
        "--features-out", str(tmp_path / "stream.parquet"),
        "--manifest-out", str(tmp_path / "stream.json"),
        "--spill-dir", str(tmp_path / "stream-spill"),
        "--host", HOST, "--port", str(PORT), "--feature-graph", graph_name,
    ]) == 0
    assert feature_batch.main([
        *common,
        "--features-out", str(tmp_path / "batch.parquet"),
        "--manifest-out", str(tmp_path / "batch.json"),
        "--spill-dir", str(tmp_path / "batch-spill"),
    ]) == 0

    stream = pq.read_table(tmp_path / "stream.parquet").sort_by("transaction_id")
    batch = pq.read_table(tmp_path / "batch.parquet").sort_by("transaction_id")
    assert batch.schema == stream.schema
    assert batch.num_rows == stream.num_rows > 0
    mismatches = [
        (left, right)
        for left, right in zip(stream.to_pylist(), batch.to_pylist(), strict=True)
        if left != right
    ]
    assert mismatches == []
    # Non-trivial coverage: every feature takes more than one value.
    for name in feature_stream.FEATURE_NAMES:
        assert len(set(batch.column(name).to_pylist())) > 1, name
