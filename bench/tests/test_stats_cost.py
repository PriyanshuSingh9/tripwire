"""Unit tests for the benchmark statistics, knee rule, and cost model (no services)."""

from __future__ import annotations

import pytest

from bench.cost import PRICE_BASIS, build_cost_model, metered_usd, provisioned_usd
from bench.stats import find_knee, percentile, summarize_ms


def test_percentile_matches_numpy_linear() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 50) == pytest.approx(2.5)
    assert percentile(values, 95) == pytest.approx(3.85)
    assert percentile([7.0], 99) == 7.0


def test_summarize_reports_p99_and_extremes() -> None:
    stats = summarize_ms(list(range(1, 101)))
    assert stats["samples"] == 100
    assert stats["p50_ms"] == pytest.approx(50.5)
    assert stats["p99_ms"] == pytest.approx(99.01)
    assert stats["min_ms"] == 1 and stats["max_ms"] == 100


def level(c: int, rps: float, errors: float = 0.0) -> dict[str, float]:
    return {"concurrency": c, "throughput_rps": rps, "error_rate": errors, "p95_ms": 10.0 * c}


def test_knee_is_last_level_with_meaningful_gain() -> None:
    knee = find_knee([level(1, 1.0), level(2, 1.9), level(4, 3.0), level(8, 3.1), level(16, 3.2)])
    assert knee["knee_concurrency"] == 4
    assert knee["peak_concurrency"] == 16
    assert knee["first_error_concurrency"] is None


def test_knee_stops_at_first_error_level() -> None:
    knee = find_knee([level(1, 1.0), level(2, 2.0), level(4, 4.0, errors=0.2)])
    assert knee["knee_concurrency"] == 2
    assert knee["first_error_concurrency"] == 4


def test_price_components_reproduce_reference_instance() -> None:
    hourly = (
        PRICE_BASIS["reference_instance_vcpus"] * PRICE_BASIS["vcpu_hour_usd"]
        + PRICE_BASIS["reference_instance_gib"] * PRICE_BASIS["gib_hour_usd"]
    )
    assert hourly == pytest.approx(PRICE_BASIS["reference_instance_hour_usd"], abs=5e-5)


def test_metered_and_provisioned_costs() -> None:
    cost = metered_usd(cpu_seconds=3600.0, resident_gib=1.0, wall_seconds=3600.0)
    assert cost["cpu_usd"] == pytest.approx(PRICE_BASIS["vcpu_hour_usd"])
    assert cost["memory_usd"] == pytest.approx(PRICE_BASIS["gib_hour_usd"])
    assert provisioned_usd(0.0) is None
    assert provisioned_usd(1.0) == pytest.approx(PRICE_BASIS["reference_instance_hour_usd"] / 3600)


def test_cost_model_prefers_isolated_direct_tool_cpu() -> None:
    results = {
        "api_serial": {
            "investigation": {"mean_ms": 2000.0},
            "investigation_resources": {
                "requests_in_window": 10,
                "process_cpu_seconds": 1.0,
                "process_rss_peak_mib": 512.0,
                "falkordb_cpu_seconds": 500.0,
                "falkordb_cpu_isolated": False,
            },
        },
        "graph_tools": {
            "tools": {
                "a": {"falkordb_cpu_seconds_per_invocation": 1.5, "falkordb_cpu_isolated": True},
                "b": {"falkordb_cpu_seconds_per_invocation": 0.5, "falkordb_cpu_isolated": True},
            }
        },
        "graph": {"graph_memory_mib": 512.0},
    }
    cost = build_cost_model(results)
    investigation = cost["investigation"]
    assert investigation["falkordb_cpu_seconds"] == pytest.approx(2.0)
    assert investigation["api_cpu_seconds"] == pytest.approx(0.1)
    assert investigation["falkordb_cpu_source"].startswith("sum of isolated")
    assert cost["external_llm_cost_usd"] == 0.0
