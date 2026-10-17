"""Repeatable latency benchmark for the bounded investigation tools.

Runs a warmup + N-sample protocol for every approved tool against a named
FalkorDB graph and writes one JSON summary (P50/P95/mean/max per tool and per
anchor class, raw samples, graph size and memory, hardware string) under
``artifacts/benchmarks/`` (Git-ignored).

Anchors are selected deterministically from the graph itself, never from
labels:

- ``high``: the latest sent transaction of each of the top out-degree
  accounts, and the latest received transaction of each of the top in-degree
  accounts (stress the heavy tail);
- ``typical``: transactions at seeded uniform offsets in node order (a
  transaction-weighted sample, similar to alert-driven investigations).

Each anchor transaction drives the same calls the API investigation makes:
``trace_funds`` (direction ``both``), ``inspect_counterparties`` by
transaction, ``fan_out`` at the sender, and ``fan_in`` and ``pass_through``
at the receiver. ``inspect_counterparties`` by account (sender) and a 3-hop
trace are measured as additional stress tools. ``investigation`` is the sum
of the five API calls for one anchor.

The output is aggregate-only: anchor IDs are not written unless
``--record-anchors`` is passed (use it only on synthetic fixtures).

Reproducible entry (from the repository root)::

    uv run --project graph traceaml-graph-bench --graph-name agentgraph-opt-1m \\
        --dataset-label "synthetic demo-1m fixture"
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import platform
import statistics
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from graph import queries as queries_module

OUTPUT_DIR_DEFAULT = Path("artifacts/benchmarks")
HOST_DEFAULT = "localhost"
PORT_DEFAULT = 6379

TOP_DEGREE_SENT = """\
MATCH (a:Account)-[:SENT]->(t:Transaction)
WITH a, count(t) AS degree
ORDER BY degree DESC, a.account_key
LIMIT $k
RETURN a.account_key AS account_key, degree
"""

TOP_DEGREE_RECEIVED = """\
MATCH (t:Transaction)-[:RECEIVED]->(a:Account)
WITH a, count(t) AS degree
ORDER BY degree DESC, a.account_key
LIMIT $k
RETURN a.account_key AS account_key, degree
"""

LATEST_SENT_TX = """\
MATCH (a:Account {account_key: $account_key})-[:SENT]->(t:Transaction)
RETURN t.transaction_id
ORDER BY t.timestamp_epoch DESC, t.transaction_id
LIMIT 1
"""

LATEST_RECEIVED_TX = """\
MATCH (t:Transaction)-[:RECEIVED]->(a:Account {account_key: $account_key})
RETURN t.transaction_id
ORDER BY t.timestamp_epoch DESC, t.transaction_id
LIMIT 1
"""

TX_AT_OFFSET = "MATCH (t:Transaction) RETURN t.transaction_id SKIP $offset LIMIT 1"
COUNT_TX = "MATCH (t:Transaction) RETURN count(t)"
COUNT_ACCOUNTS = "MATCH (a:Account) RETURN count(a)"

ENDPOINTS = """\
MATCH (s:Account)-[:SENT]->(t:Transaction {transaction_id: $transaction_id})-[:RECEIVED]->(r:Account)
RETURN s.account_key, r.account_key
"""

DEGREE_OF = """\
MATCH (a:Account {account_key: $account_key})
OPTIONAL MATCH (a)-[s:SENT]->()
WITH a, count(s) AS out_degree
OPTIONAL MATCH (a)<-[r:RECEIVED]-()
RETURN out_degree, count(r) AS in_degree
"""


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (numpy ``linear`` method)."""
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    rank = (len(ordered) - 1) * q
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def summarize(values: list[float]) -> dict[str, float | int]:
    """P50/P95/mean/max summary of millisecond samples."""
    if not values:
        return {"samples": 0}
    return {
        "samples": len(values),
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "mean_ms": round(statistics.fmean(values), 3),
        "max_ms": round(max(values), 3),
    }


def hardware_string() -> str:
    """Return a human-readable CPU / core / RAM description."""
    cpu = platform.processor() or platform.machine()
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                cpu = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    elif Path("/proc/cpuinfo").is_file():
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    return f"{cpu}, {os.cpu_count()} logical CPUs, {_total_ram_gib():.1f} GiB RAM, {platform.system()} {platform.release()}"


def _total_ram_gib() -> float:
    if sys.platform == "win32":

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.ullTotalPhys / 2**30
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (AttributeError, ValueError, OSError):
        return float("nan")


def graph_memory(db: Any, graph_name: str) -> dict[str, Any]:
    """Return FalkorDB-side memory figures (best effort; never raises)."""
    out: dict[str, Any] = {}
    connection = getattr(db, "connection", None)
    if connection is None:
        return out
    try:
        raw = connection.execute_command("GRAPH.MEMORY", "USAGE", graph_name)
        pairs = [
            (raw[i].decode() if isinstance(raw[i], bytes) else str(raw[i]), raw[i + 1])
            for i in range(0, len(raw) - 1, 2)
        ]
        out["graph_memory_usage"] = {key: value for key, value in pairs}
    except Exception as exc:  # noqa: BLE001 - optional diagnostics only
        out["graph_memory_usage_error"] = type(exc).__name__
    try:
        info = connection.info("memory")
        out["redis_used_memory_bytes"] = int(info.get("used_memory", 0))
        out["redis_used_memory_human"] = info.get("used_memory_human")
    except Exception as exc:  # noqa: BLE001 - optional diagnostics only
        out["redis_info_error"] = type(exc).__name__
    try:
        modules = connection.execute_command("MODULE", "LIST")
        for module in modules:
            fields = dict(zip(module[0::2], module[1::2]))
            name = fields.get(b"name", fields.get("name"))
            if name in (b"graph", "graph"):
                out["falkordb_module_version"] = fields.get(b"ver", fields.get("ver"))
    except Exception as exc:  # noqa: BLE001 - optional diagnostics only
        out["module_list_error"] = type(exc).__name__
    return out


def _scalar_rows(graph: Any, cypher: str, params: dict[str, Any] | None = None,
                 timeout_ms: int = 120_000) -> list[list[Any]]:
    return graph.query(cypher, params=params or {}, timeout=timeout_ms).result_set


def select_anchors(
    graph: Any, *, per_class: int, seed: int, explicit: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """Pick ``case`` (explicit), ``high`` and ``typical`` anchors deterministically."""
    import random

    anchors: list[dict[str, Any]] = []
    half = max(1, per_class // 2)
    seen: set[str] = set()
    for transaction_id in explicit:
        if transaction_id not in seen:
            seen.add(transaction_id)
            anchors.append({"class": "case", "role": "explicit", "transaction_id": transaction_id})
    explicit_only = per_class == 0
    degree_sources = () if explicit_only else (
        (TOP_DEGREE_SENT, LATEST_SENT_TX, "top_sender"),
        (TOP_DEGREE_RECEIVED, LATEST_RECEIVED_TX, "top_receiver"),
    )
    for cypher, latest, role in degree_sources:
        for key, degree in _scalar_rows(graph, cypher, {"k": half}):
            rows = _scalar_rows(graph, latest, {"account_key": key})
            if rows and rows[0][0] not in seen:
                seen.add(rows[0][0])
                anchors.append(
                    {"class": "high", "role": role, "transaction_id": rows[0][0],
                     "hub_degree": int(degree)}
                )
    total = int(_scalar_rows(graph, COUNT_TX)[0][0])
    rng = random.Random(seed)
    attempts = 0
    typical = 0
    while not explicit_only and typical < per_class and attempts < per_class * 10 and total > 0:
        attempts += 1
        offset = rng.randrange(total)
        rows = _scalar_rows(graph, TX_AT_OFFSET, {"offset": offset})
        if rows and rows[0][0] not in seen:
            seen.add(rows[0][0])
            anchors.append({"class": "typical", "role": "uniform", "transaction_id": rows[0][0]})
            typical += 1
    for anchor in anchors:
        rows = _scalar_rows(graph, ENDPOINTS, {"transaction_id": anchor["transaction_id"]})
        if not rows:
            raise ValueError(f"anchor transaction not in graph: {anchor['transaction_id']}")
        anchor["sender_key"], anchor["receiver_key"] = rows[0]
        for side in ("sender", "receiver"):
            out_degree, in_degree = _scalar_rows(
                graph, DEGREE_OF, {"account_key": anchor[f"{side}_key"]}
            )[0]
            anchor[f"{side}_degree"] = int(out_degree) + int(in_degree)
    return anchors


ToolCall = Callable[[Any, dict[str, Any]], dict[str, Any]]


def tool_calls(
    q: Any, *, max_hops: int, window: int, limit: int, timeout_ms: int
) -> dict[str, ToolCall]:
    """Return the measured tools as ``name -> f(graph, anchor)``."""

    def trace(hops: int) -> ToolCall:
        return lambda g, a: q.trace_funds(
            g, a["transaction_id"], direction="both", max_hops=hops,
            time_window_seconds=window, result_limit=limit, timeout_ms=timeout_ms,
        )

    def pattern(name: str, side: str) -> ToolCall:
        return lambda g, a: q.detect_flow_patterns(
            g, account_key=a[f"{side}_key"], pattern=name, time_window_seconds=window,
            dwell_seconds=window, result_limit=limit, timeout_ms=timeout_ms,
        )

    return {
        "trace_funds": trace(max_hops),
        "trace_funds_3hop": trace(3),
        "inspect_counterparties": lambda g, a: q.inspect_counterparties(
            g, transaction_id=a["transaction_id"], time_window_seconds=window,
            result_limit=limit, timeout_ms=timeout_ms,
        ),
        "inspect_counterparties_account": lambda g, a: q.inspect_counterparties(
            g, account_key=a["sender_key"], time_window_seconds=window,
            result_limit=limit, timeout_ms=timeout_ms,
        ),
        "fan_out": pattern("fan_out", "sender"),
        "fan_in": pattern("fan_in", "receiver"),
        "pass_through": pattern("pass_through", "receiver"),
    }


INVESTIGATION_TOOLS = ("trace_funds", "inspect_counterparties", "fan_out", "fan_in", "pass_through")


def _result_size(name: str, result: dict[str, Any]) -> int:
    if name in ("fan_out", "fan_in", "pass_through"):
        return len(result.get("matches", []))
    if name.startswith("trace_funds"):
        return len(result.get("paths", []))
    return len(result.get("transactions", []))


def run_protocol(
    graph: Any,
    anchors: list[dict[str, Any]],
    calls: dict[str, ToolCall],
    *,
    warmup: int,
    samples: int,
) -> dict[str, Any]:
    """Warm up then sample every tool on every anchor; summarize."""
    raw: dict[str, list[dict[str, Any]]] = {name: [] for name in calls}
    raw["investigation"] = []
    errors: dict[str, int] = {}
    for anchor in anchors:
        for name, call in calls.items():
            for _ in range(warmup):
                with contextlib.suppress(Exception):  # errors are counted when measured
                    call(graph, anchor)
        for _ in range(samples):
            investigation_ms = 0.0
            investigation_ok = True
            for name, call in calls.items():
                started = time.perf_counter()
                try:
                    result = call(graph, anchor)
                except Exception as exc:  # noqa: BLE001 - recorded, never fatal
                    key = f"{name}:{type(exc).__name__}"
                    errors[key] = errors.get(key, 0) + 1
                    if name in INVESTIGATION_TOOLS:
                        investigation_ok = False
                    continue
                wall_ms = (time.perf_counter() - started) * 1000.0
                tool_ms = float(result.get("query_duration_ms", wall_ms))
                raw[name].append(
                    {"class": anchor["class"], "ms": tool_ms, "wall_ms": wall_ms,
                     "result_size": _result_size(name, result)}
                )
                if name in INVESTIGATION_TOOLS:
                    investigation_ms += tool_ms
            if investigation_ok:
                raw["investigation"].append(
                    {"class": anchor["class"], "ms": investigation_ms, "wall_ms": investigation_ms,
                     "result_size": 0}
                )
    tools: dict[str, Any] = {}
    for name, rows in raw.items():
        entry: dict[str, Any] = {"all": summarize([r["ms"] for r in rows])}
        for cls in ("case", "high", "typical"):
            entry[cls] = summarize([r["ms"] for r in rows if r["class"] == cls])
        sizes = [r["result_size"] for r in rows]
        if sizes and name != "investigation":
            entry["result_size_min"] = min(sizes)
            entry["result_size_max"] = max(sizes)
            entry["nonempty_share"] = round(sum(1 for s in sizes if s > 0) / len(sizes), 3)
        entry["samples_ms"] = [round(r["ms"], 3) for r in rows]
        tools[name] = entry
    return {"tools": tools, "errors": errors}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Warmup + N-sample latency benchmark for the bounded graph tools."
    )
    parser.add_argument("--graph-name", required=True)
    parser.add_argument("--host", default=os.environ.get("TRACEAML_FALKORDB_HOST", HOST_DEFAULT))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("TRACEAML_FALKORDB_PORT", PORT_DEFAULT))
    )
    parser.add_argument("--dataset-label", default="unspecified",
                        help="Free-text label, e.g. 'synthetic demo-1m fixture'.")
    parser.add_argument("--anchors-per-class", type=int, default=6,
                        help="High-degree and typical anchors each (0: explicit anchors only).")
    parser.add_argument("--anchor-transactions", default="",
                        help="Comma-separated transaction IDs measured as class 'case'.")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20_221_001)
    parser.add_argument("--max-hops", type=int, default=2)
    parser.add_argument("--time-window-seconds", type=int, default=86_400)
    parser.add_argument("--result-limit", type=int, default=50)
    parser.add_argument("--timeout-ms", type=int, default=queries_module.DEFAULT_TIMEOUT_MS)
    parser.add_argument(
        "--implementation", choices=("current", "legacy"), default="current",
        help="'legacy' runs the iteration-1 query shapes for before/after comparison.",
    )
    parser.add_argument("--tools", default="",
                        help="Comma-separated subset of tools (default: all).")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--record-anchors", action="store_true",
                        help="Write anchor IDs into the JSON (synthetic fixtures only).")
    args = parser.parse_args(argv)
    if args.anchors_per_class < 0 or (
        args.anchors_per_class == 0 and not args.anchor_transactions
    ):
        parser.error("--anchors-per-class must be positive unless --anchor-transactions is set")
    for name in ("samples",):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.warmup < 0:
        parser.error("--warmup must be non-negative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from falkordb import FalkorDB

    if args.implementation == "legacy":
        from graph import legacy_queries as q
    else:
        q = queries_module

    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph_name)
    size = {
        "transactions": int(_scalar_rows(graph, COUNT_TX)[0][0]),
        "accounts": int(_scalar_rows(graph, COUNT_ACCOUNTS)[0][0]),
    }
    if size["transactions"] == 0:
        print(json.dumps({"error": f"graph {args.graph_name} has no transactions"}))
        return 2
    selected_started = time.perf_counter()
    explicit = tuple(t.strip() for t in args.anchor_transactions.split(",") if t.strip())
    anchors = select_anchors(
        graph, per_class=args.anchors_per_class, seed=args.seed, explicit=explicit
    )
    selection_seconds = time.perf_counter() - selected_started
    calls = tool_calls(
        q, max_hops=args.max_hops, window=args.time_window_seconds,
        limit=args.result_limit, timeout_ms=args.timeout_ms,
    )
    if args.tools:
        wanted = {name.strip() for name in args.tools.split(",") if name.strip()}
        unknown = wanted - set(calls)
        if unknown:
            print(json.dumps({"error": f"unknown tools: {sorted(unknown)}"}))
            return 2
        calls = {name: call for name, call in calls.items() if name in wanted}
    started = time.perf_counter()
    measured = run_protocol(graph, anchors, calls, warmup=args.warmup, samples=args.samples)
    measured_at = datetime.now(UTC)
    anchor_summary = [
        {key: value for key, value in anchor.items()
         if args.record_anchors
        or key in ("class", "role", "hub_degree", "sender_degree", "receiver_degree")
        or (anchor["class"] == "case" and key == "transaction_id")}
        for anchor in anchors
    ]
    report = {
        "measured_at": measured_at.isoformat(timespec="seconds"),
        "dataset_label": args.dataset_label,
        "graph_name": args.graph_name,
        "implementation": args.implementation,
        "hardware": hardware_string(),
        "graph_size": size,
        "memory": graph_memory(db, args.graph_name),
        "protocol": {
            "warmup_per_anchor_tool": args.warmup,
            "samples_per_anchor_tool": args.samples,
            "anchors_per_class": args.anchors_per_class,
            "seed": args.seed,
            "max_hops": args.max_hops,
            "time_window_seconds": args.time_window_seconds,
            "result_limit": args.result_limit,
            "timeout_ms": args.timeout_ms,
            "metric": "tool-reported query_duration_ms (all database calls of one tool)",
            "execution": "serial, one call at a time",
        },
        "anchor_selection_seconds": round(selection_seconds, 3),
        "anchors": anchor_summary,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        **measured,
    }
    output = args.output or OUTPUT_DIR_DEFAULT / (
        f"{args.graph_name}-{args.implementation}-{measured_at:%Y%m%dT%H%M%SZ}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    table = {
        name: {k: entry["all"].get(k) for k in ("p50_ms", "p95_ms", "max_ms")}
        for name, entry in measured["tools"].items()
    }
    print(json.dumps({"output": output.as_posix(), "summary": table,
                      "errors": measured["errors"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
