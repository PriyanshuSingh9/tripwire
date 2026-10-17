"""Latency sample summaries and capacity knee detection."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import Any


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (NumPy ``linear`` method) of sorted data."""
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * q / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(sorted_values[low])
    weight = rank - low
    return float(sorted_values[low] * (1 - weight) + sorted_values[high] * weight)


def summarize_ms(samples_ms: Sequence[float]) -> dict[str, Any]:
    """P50/P95/P99/mean/min/max of millisecond samples."""
    ordered = sorted(float(value) for value in samples_ms)
    count = len(ordered)
    return {
        "samples": count,
        "p50_ms": percentile(ordered, 50),
        "p95_ms": percentile(ordered, 95),
        "p99_ms": percentile(ordered, 99),
        "mean_ms": sum(ordered) / count if count else math.nan,
        "min_ms": ordered[0] if ordered else math.nan,
        "max_ms": ordered[-1] if ordered else math.nan,
    }


def find_knee(levels: Sequence[dict[str, Any]], min_gain: float = 0.10) -> dict[str, Any]:
    """Capacity knee of a closed-loop concurrency sweep.

    ``levels`` holds ``concurrency``, ``throughput_rps``, ``error_rate``, and
    ``p95_ms`` per level in ascending concurrency. The knee is the last level
    whose throughput still improved by at least ``min_gain`` over the
    previous level while staying error-free; beyond it extra clients mostly
    add queueing delay. Also reports peak throughput and the first level
    with errors.
    """
    ordered = sorted(levels, key=lambda level: level["concurrency"])
    if not ordered:
        return {"knee_concurrency": None}
    knee = ordered[0]
    for previous, current in itertools.pairwise(ordered):
        if current["error_rate"] > 0:
            break
        if current["throughput_rps"] >= previous["throughput_rps"] * (1 + min_gain):
            knee = current
        else:
            break
    peak = max(ordered, key=lambda level: level["throughput_rps"])
    first_error = next((level for level in ordered if level["error_rate"] > 0), None)
    return {
        "rule": (
            f"last error-free level whose throughput rose >= {min_gain:.0%} over the "
            "previous level"
        ),
        "knee_concurrency": knee["concurrency"],
        "knee_throughput_rps": knee["throughput_rps"],
        "knee_p95_ms": knee["p95_ms"],
        "peak_throughput_rps": peak["throughput_rps"],
        "peak_concurrency": peak["concurrency"],
        "first_error_concurrency": first_error["concurrency"] if first_error else None,
    }
