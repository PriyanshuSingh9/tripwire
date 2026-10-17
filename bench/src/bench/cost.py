"""Explicit compute cost model from measured CPU time and memory.

All dollar figures are estimates. They convert locally measured CPU-seconds
and resident memory into cloud list prices; they are not a bill and do not
include storage, network, managed-service, or staff costs. No LLM provider
is called anywhere in the measured paths, so LLM cost is zero by
construction.

Price basis (on-demand, Linux, no committed-use or sustained-use discount):
Google Compute Engine N2D (AMD EPYC) in us-central1. Listed instance prices
n2d-standard-4 = $0.1690/h (4 vCPU, 16 GiB) and n2d-standard-16 =
$0.6759/h (16 vCPU, 64 GiB), as shown on the Holori GCP price catalogue
(https://calculator.holori.com/gcp/vm/n2d-standard-4, page updated
2026-10-15, retrieved 2026-10-16). The component rates below,
$0.027502 per vCPU-hour and $0.003686 per GiB-hour, are Google's published
N2D predefined vCPU and memory rates for us-central1
(https://cloud.google.com/compute/vm-instance-pricing); they reproduce
every listed N2D standard price above to four decimal places. Re-check both
sources before quoting the figures externally.

Two views are reported:

- Metered: ``cpu_seconds * vcpu_rate + resident_GiB * wall_seconds *
  gib_rate``. This is the marginal resource cost of one unit of work.
- Provisioned: an always-on n2d-standard-16 (the same 16 hardware threads
  as the local host) divided by the measured peak throughput. This is the
  cost per unit when the instance is kept fully busy; idle time raises it.
"""

from __future__ import annotations

from typing import Any

PRICE_BASIS: dict[str, Any] = {
    "provider": "Google Compute Engine",
    "region": "us-central1",
    "family": "N2D predefined (AMD EPYC), on-demand, Linux",
    "vcpu_hour_usd": 0.027502,
    "gib_hour_usd": 0.003686,
    "reference_instance": "n2d-standard-16",
    "reference_instance_vcpus": 16,
    "reference_instance_gib": 64,
    "reference_instance_hour_usd": 0.6759,
    "sources": [
        "https://cloud.google.com/compute/vm-instance-pricing",
        "https://calculator.holori.com/gcp/vm/n2d-standard-4 (updated 2026-10-15)",
        "https://calculator.holori.com/gcp/vm/n2d-standard-16",
    ],
    "retrieved_on": "2026-10-16",
    "status": "estimate; list prices, no discounts; excludes storage, network, staff",
}


def metered_usd(cpu_seconds: float, resident_gib: float, wall_seconds: float) -> dict[str, float]:
    cpu = cpu_seconds * PRICE_BASIS["vcpu_hour_usd"] / 3600.0
    memory = resident_gib * wall_seconds * PRICE_BASIS["gib_hour_usd"] / 3600.0
    return {"cpu_usd": cpu, "memory_usd": memory, "total_usd": cpu + memory}


def provisioned_usd(units_per_second: float) -> float | None:
    if units_per_second <= 0:
        return None
    return PRICE_BASIS["reference_instance_hour_usd"] / (units_per_second * 3600.0)


def build_cost_model(results: dict[str, Any]) -> dict[str, Any]:
    """Derive per-1,000-transaction and per-investigation cost estimates."""
    out: dict[str, Any] = {
        "price_basis": PRICE_BASIS,
        "external_llm_calls": 0,
        "external_llm_cost_usd": 0.0,
        "llm_note": "No LLM provider is configured or called; reports are deterministic.",
        "energy": "not estimated; host power was not metered",
    }
    model = results.get("model")
    if model:
        scoring: dict[str, Any] = {}
        for threads, entry in model["full_test_cohort"].items():
            rows = entry["rows"]
            per_1000_cpu = entry["cpu_seconds_per_1000_transactions"]
            per_1000_wall = entry["median_seconds"] / rows * 1000.0
            resident_gib = entry["peak_rss_mib"] / 1024.0
            metered = metered_usd(per_1000_cpu, resident_gib, per_1000_wall)
            scoring[threads] = {
                "cpu_seconds_per_1000": per_1000_cpu,
                "wall_seconds_per_1000": per_1000_wall,
                "peak_rss_mib": entry["peak_rss_mib"],
                "metered_usd_per_1000": metered["total_usd"],
                "metered_usd_per_million": metered["total_usd"] * 1000.0,
                "provisioned_usd_per_1000": (
                    (provisioned_usd(entry["transactions_per_second"]) or 0.0) * 1000.0
                ),
                "scope": "LightGBM booster on a prepared matrix; excludes feature building",
            }
        out["scoring"] = scoring
        out["replay_api_note"] = (
            "The replay API serves precomputed scores; it does not run the model per request."
        )
    serial = results.get("api_serial", {}).get("investigation")
    resources = results.get("api_serial", {}).get("investigation_resources")
    if serial and resources:
        n = resources["requests_in_window"]
        api_cpu = resources["process_cpu_seconds"] / n
        observed_falkor_cpu = resources.get("falkordb_cpu_seconds", 0.0) / n
        tools = results.get("graph_tools", {}).get("tools", {})
        tool_sum = sum(t.get("falkordb_cpu_seconds_per_invocation", 0.0) for t in tools.values())
        tools_isolated = bool(tools) and all(t.get("falkordb_cpu_isolated") for t in tools.values())
        # Prefer a window where no other client touched FalkorDB: first the
        # API window itself, then the sum of the isolated direct-tool windows.
        if resources.get("falkordb_cpu_isolated"):
            falkor_cpu, falkor_source = observed_falkor_cpu, "API serial window, isolated"
        elif tools_isolated:
            falkor_cpu, falkor_source = tool_sum, "sum of isolated direct-tool windows"
        else:
            falkor_cpu, falkor_source = observed_falkor_cpu, "API window, NOT isolated (upper bound)"
        wall = serial["mean_ms"] / 1000.0
        api_gib = resources["process_rss_peak_mib"] / 1024.0
        graph_mib = results.get("graph", {}).get("graph_memory_mib")
        graph_gib = (graph_mib or 0.0) / 1024.0
        metered = metered_usd(api_cpu + falkor_cpu, api_gib + graph_gib, wall)
        investigation: dict[str, Any] = {
            "api_cpu_seconds": api_cpu,
            "falkordb_cpu_seconds": falkor_cpu,
            "falkordb_cpu_source": falkor_source,
            "falkordb_cpu_observed_in_api_window": observed_falkor_cpu,
            "falkordb_cpu_direct_tool_sum": tool_sum,
            "total_cpu_seconds": api_cpu + falkor_cpu,
            "wall_seconds_serial_mean": wall,
            "api_peak_rss_mib": resources["process_rss_peak_mib"],
            "graph_memory_mib": graph_mib,
            "falkordb_transient_peak_mib": resources.get("falkordb_used_memory_peak_mib", 0.0)
            - resources.get("falkordb_used_memory_start_mib", 0.0),
            "metered_usd": metered["total_usd"],
            "metered_usd_per_1000": metered["total_usd"] * 1000.0,
            "falkordb_cpu_note": (
                "FalkorDB CPU is server-wide (INFO cpu); concurrent foreign clients would "
                "inflate it. See environment.falkordb.foreign_activity."
            ),
        }
        peak = results.get("capacity_summary", {}).get("investigations_peak_rps")
        if peak:
            investigation["provisioned_peak_rps"] = peak
            investigation["provisioned_usd"] = provisioned_usd(peak)
            investigation["provisioned_usd_per_1000"] = (provisioned_usd(peak) or 0.0) * 1000.0
        out["investigation"] = investigation
    return out
