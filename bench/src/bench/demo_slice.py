"""Target-free investigation demo slice and its FalkorDB graph.

Selection rule (identical to Iteration 1, which produced 998,021 rows):
every transaction whose ``timestamp`` lies in the closed interval
``[anchor_ts - 24 h, anchor_ts + 24 h]``, where ``anchor_ts`` is the
timestamp of the anchor transaction (default ``hi8-tx-03863669``, the
highest-scored held-out transaction). Rows are written in source file order.

Only the graph loader's allowlisted columns (``graph.load_graph.LOAD_COLUMNS``)
are projected, so ``target_is_laundering``, ``split``, and typology fields
never reach the slice file or the graph. The graph itself is built by the
graph package's loader (``--mode create --window-around ANCHOR
--window-seconds 86400``, the same rule), so loader improvements are picked
up; the slice file here is an independent row-count and hash cross-check.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SLICE_HALF_WIDTH_SECONDS = 86_400


@dataclass(frozen=True)
class SliceInfo:
    path: Path
    anchor_transaction_id: str
    anchor_timestamp: str
    window_start: str
    window_end: str
    rows: int
    sha256: str


def _sql(path: Path) -> str:
    return str(path).replace("'", "''")


def build_slice(source: Path, out: Path, anchor_transaction_id: str) -> SliceInfo:
    """Write the target-free slice Parquet and return its description."""
    import duckdb
    from graph.load_graph import LOAD_COLUMNS

    from bench.env import sha256_file

    forbidden = {"target_is_laundering", "split"}
    if forbidden & set(LOAD_COLUMNS):
        raise ValueError("loader allowlist contains offline-only columns")
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        row = con.execute(
            f"SELECT timestamp FROM read_parquet('{_sql(source)}') WHERE transaction_id = ?",
            [anchor_transaction_id],
        ).fetchone()
        if row is None:
            raise ValueError(f"anchor transaction not found: {anchor_transaction_id}")
        anchor_ts = row[0]
        columns = ", ".join(LOAD_COLUMNS)
        con.execute(
            f"COPY (SELECT {columns} FROM read_parquet('{_sql(source)}') "
            f"WHERE timestamp BETWEEN TIMESTAMP '{anchor_ts}' - INTERVAL {SLICE_HALF_WIDTH_SECONDS} "
            f"SECOND AND TIMESTAMP '{anchor_ts}' + INTERVAL {SLICE_HALF_WIDTH_SECONDS} SECOND "
            f"ORDER BY source_row_number) TO '{_sql(out)}' (FORMAT PARQUET)"
        )
        rows, start, end = con.execute(
            f"SELECT count(*), min(timestamp), max(timestamp) FROM read_parquet('{_sql(out)}')"
        ).fetchone()
    finally:
        con.close()
    return SliceInfo(
        path=out,
        anchor_transaction_id=anchor_transaction_id,
        anchor_timestamp=str(anchor_ts),
        window_start=str(start),
        window_end=str(end),
        rows=int(rows),
        sha256=sha256_file(out),
    )


def graph_counts(host: str, port: int, graph_name: str) -> dict[str, int] | None:
    """Return node/edge counts for a graph, or None when it does not exist."""
    from falkordb import FalkorDB

    client = FalkorDB(host=host, port=port)
    try:
        if graph_name not in client.list_graphs():
            return None
        graph = client.select_graph(graph_name)
        counts = {}
        for key, cypher in (
            ("transactions", "MATCH (t:Transaction) RETURN count(t)"),
            ("accounts", "MATCH (a:Account) RETURN count(a)"),
            ("sent_edges", "MATCH ()-[r:SENT]->() RETURN count(r)"),
            ("received_edges", "MATCH ()-[r:RECEIVED]->() RETURN count(r)"),
        ):
            counts[key] = int(graph.ro_query(cypher, timeout=120_000).result_set[0][0])
        return counts
    finally:
        client.close()


def ensure_demo_graph(
    slice_info: SliceInfo,
    *,
    source: Path,
    host: str,
    port: int,
    graph_name: str,
    force_reload: bool,
    log: Any,
) -> dict[str, Any]:
    """Load the slice into ``graph_name`` unless it already matches the slice."""
    if not graph_name.startswith("agentbench-"):
        raise ValueError("benchmark graphs must be named agentbench-*")
    counts = graph_counts(host, port, graph_name)
    if counts is not None and counts["transactions"] == slice_info.rows and not force_reload:
        log(f"graph {graph_name} already holds {slice_info.rows} transactions; reusing")
        return {"reloaded": False, "load_summary": None, "counts": counts}
    if counts is not None:
        from falkordb import FalkorDB

        client = FalkorDB(host=host, port=port)
        try:
            client.select_graph(graph_name).delete()
        finally:
            client.close()
    log(f"loading {slice_info.rows} rows into {graph_name} with traceaml-load-graph --window-around")
    started = time.perf_counter()
    # The graph package's own windowed create-mode loader builds the slice;
    # the bench slice file above is kept only as a row-count and hash check.
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "graph.load_graph",
            "--parquet",
            str(source),
            "--graph-name",
            graph_name,
            "--host",
            host,
            "--port",
            str(port),
            "--mode",
            "create",
            "--window-around",
            slice_info.anchor_transaction_id,
            "--window-seconds",
            str(SLICE_HALF_WIDTH_SECONDS),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(f"graph load failed: {completed.stdout[-2000:]}{completed.stderr[-2000:]}")
    summary = json.loads(completed.stdout[completed.stdout.index("{") :])
    counts = graph_counts(host, port, graph_name)
    if counts is None or counts["transactions"] != slice_info.rows:
        raise RuntimeError("loaded graph does not reconcile with the bench slice row count")
    return {
        "reloaded": True,
        "load_wall_seconds": elapsed,
        "load_summary": summary,
        "counts": counts,
    }
