"""Direct graph tool latency, serial, with the exact arguments the API uses.

Calls go through ``api.investigate.FalkorGraphTools`` (the production
adapter over ``graph.queries``), so direction, dwell, and timeout match an
investigation request. Each tool runs ``warmups`` unmeasured calls then
``repeats`` measured calls on one connection. Reported latency is the tool's
own ``query_duration_ms`` (all database calls inside the tool); client wall
time including result decoding is reported alongside.
"""

from __future__ import annotations

import time
from typing import Any

from bench.stats import summarize_ms


class CountingGraph:
    """Graph handle proxy counting the database calls this suite issues.

    The count lets the suite separate its own server work from other
    clients' work in server-wide FalkorDB counters.
    """

    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self.calls = 0

    def query(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self._graph.query(*args, **kwargs)

    def ro_query(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self._graph.ro_query(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)


def _count(result: dict[str, Any]) -> dict[str, int]:
    return {
        key: len(result[key])
        for key in ("transactions", "accounts", "paths", "matches")
        if isinstance(result.get(key), list)
    }


def anchor_keys(tools: Any, transaction_id: str, window: int, limit: int) -> tuple[str, str]:
    found = tools.inspect_counterparties(
        transaction_id, time_window_seconds=window, result_limit=limit
    )
    for row in found.get("transactions", []):
        if str(row["transaction_id"]) == transaction_id:
            return str(row["source_account_key"]), str(row["destination_account_key"])
    raise ValueError("anchor transfer not returned by inspect_counterparties")


def run_graph_tool_workloads(
    *,
    host: str,
    port: int,
    graph_name: str,
    transaction_id: str,
    max_hops: int,
    window: int,
    limit: int,
    timeout_ms: int,
    warmups: int,
    repeats: int,
    log: Any,
    meter: Any = None,
) -> dict[str, Any]:
    import psutil
    from api.investigate import FalkorGraphTools
    from falkordb import FalkorDB

    from bench.env import ResourceWindow

    client = FalkorDB(host=host, port=port)
    graph = CountingGraph(client.select_graph(graph_name))
    tools = FalkorGraphTools(lambda: graph, timeout_ms=timeout_ms)
    try:
        sender, receiver = anchor_keys(tools, transaction_id, window, limit)
        calls: dict[str, Any] = {
            "trace_funds": lambda: tools.trace_funds(
                transaction_id, max_hops=max_hops, time_window_seconds=window, result_limit=limit
            ),
            "inspect_counterparties": lambda: tools.inspect_counterparties(
                transaction_id, time_window_seconds=window, result_limit=limit
            ),
            "fan_out": lambda: tools.detect_flow_patterns(
                sender, pattern="fan_out", time_window_seconds=window, result_limit=limit
            ),
            "fan_in": lambda: tools.detect_flow_patterns(
                receiver, pattern="fan_in", time_window_seconds=window, result_limit=limit
            ),
            "pass_through": lambda: tools.detect_flow_patterns(
                receiver, pattern="pass_through", time_window_seconds=window, result_limit=limit
            ),
        }
        results: dict[str, Any] = {}
        for name, call in calls.items():
            log(f"graph tool: {name}")
            for _ in range(warmups):
                call()
            tool_ms: list[float] = []
            wall_ms: list[float] = []
            counts: list[dict[str, int]] = []
            calls_before = graph.calls
            with ResourceWindow(psutil.Process(), meter, interval=0.2) as meter_window:
                for _ in range(repeats):
                    started = time.perf_counter()
                    out = call()
                    wall_ms.append((time.perf_counter() - started) * 1000.0)
                    tool_ms.append(float(out["query_duration_ms"]))
                    counts.append(_count(out))
            own_calls = graph.calls - calls_before
            entry = {**summarize_ms(tool_ms), "warmups": warmups}
            entry["client_wall"] = summarize_ms(wall_ms)
            entry["result_counts"] = counts[-1]
            entry["database_calls_per_invocation"] = own_calls / repeats
            resources = meter_window.result()
            if meter is not None:
                foreign = resources["falkordb_graph_query_calls"] - own_calls
                entry["foreign_graph_calls_during_window"] = foreign
                entry["falkordb_cpu_seconds_per_invocation"] = (
                    resources["falkordb_cpu_seconds"] / repeats
                )
                entry["falkordb_max_blocked_clients"] = resources["falkordb_max_blocked_clients"]
                entry["foreign_host_cores"] = resources["foreign_host_cores"]
                entry["falkordb_cpu_isolated"] = (
                    foreign == 0 and resources["falkordb_max_blocked_clients"] <= 1
                )
            if name in ("fan_out", "fan_in", "pass_through"):
                matches = [c.get("matches", 0) for c in counts]
                entry["matches_min"] = min(matches)
                entry["matches_max"] = max(matches)
            results[name] = entry
        return {
            "anchor_transaction_id": transaction_id,
            "parameters": {
                "max_hops": max_hops,
                "time_window_seconds": window,
                "result_limit": limit,
                "timeout_ms": timeout_ms,
                "trace_direction": "both",
                "dwell_seconds": window,
            },
            "tools": results,
        }
    finally:
        client.close()
