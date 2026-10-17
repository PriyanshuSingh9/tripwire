"""Markdown summary and API performance snapshot rendered from results JSON."""

from __future__ import annotations

from typing import Any

ITERATION_1 = {
    "single_row_p50_ms": 0.0291995,
    "batch_100_p50_ms": 1.046,
    "batch_1000_p50_ms": 11.441,
    "batch_10000_p50_ms": 72.446,
    "cohort_seconds": 5.003724045,
    "cohort_tps": 152214.4293,
    "alerts_p50_ms": 1.086583,
    "benchmarks_p50_ms": 1.0816085,
    "investigation_p50_ms": 3486.528068,
    "trace_funds_p50_ms": 5.464668,
    "inspect_counterparties_p50_ms": 1.191125,
    "fan_out_p50_ms": 1728.081294,
    "fan_in_p50_ms": 890.564397,
    "pass_through_p50_ms": 923.417629,
}


def _ms(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if value >= 100:
        return f"{value:,.0f} ms"
    if value >= 1:
        return f"{value:,.{digits}f} ms"
    return f"{value:.3f} ms"


def _ratio(before: float, after: float | None) -> str:
    if after is None or after <= 0:
        return "n/a"
    return f"{before / after:,.1f}x"


def render_markdown(results: dict[str, Any]) -> str:
    env = results["environment"]
    hw = env["hardware"]
    lines = [
        f"# TraceAML benchmark run {results['run_id']}",
        "",
        ("Synthetic IBM AMLworld HI-Small v8 data on local hardware. These numbers describe "
        "this implementation and host, not real-world AML performance or production capacity."),
        "",
        "## Environment",
        "",
        (f"- Host: {hw['cpu']}, {hw['physical_cores']} cores / {hw['logical_threads']} threads, "
        f"{hw['memory_total_gib']} GiB RAM, {hw['os']}, Python {hw['python']}."),
        f"- Git: `{env['git']['commit']}` on `{env['git']['branch']}`"
        + (" (dirty working tree)" if env["git"]["dirty"] else "")
        + ".",
    ]
    falkor = env.get("falkordb")
    if falkor:
        cfg = falkor.get("config", {})
        lines.append(
            f"- FalkorDB {falkor['server'].get('falkordb_module_version')} on Redis "
            f"{falkor['server'].get('redis_version')}: THREAD_COUNT={cfg.get('THREAD_COUNT')}, "
            f"OMP_THREAD_COUNT={cfg.get('OMP_THREAD_COUNT')}, "
            f"MAX_QUEUED_QUERIES={cfg.get('MAX_QUEUED_QUERIES')}, TIMEOUT={cfg.get('TIMEOUT')}, "
            f"CACHE_SIZE={cfg.get('CACHE_SIZE')}."
        )
        activity = falkor.get("foreign_activity", {})
        if activity:
            lines.append(
                "- Foreign FalkorDB query rate (other clients): "
                f"{activity.get('before_queries_per_second', 0):.2f}/s before, "
                f"{activity.get('after_queries_per_second', 0):.2f}/s after the run."
            )
    gates = results.get("quiet_gates") or []
    if gates:
        noisy = [g["phase"] for g in gates if not g["quiet"]]
        waited = sum(g["waited_seconds"] for g in gates)
        lines.append(
            f"- Quiet gates: {len(gates)} phases waited {waited:,.0f} s in total for other FalkorDB "
            "clients to go idle; "
            + (f"NOT idle before: {', '.join(noisy)}." if noisy else "all phases started idle.")
        )
    graph = results.get("graph")
    if graph:
        counts = graph["counts"]
        lines.append(
            f"- Graph `{graph['graph_name']}`: {counts['transactions']:,} transactions, "
            f"{counts['accounts']:,} accounts, {counts['sent_edges']:,} SENT, "
            f"{counts['received_edges']:,} RECEIVED; "
            f"slice {graph['slice']['window_start']} to {graph['slice']['window_end']} "
            f"(anchor {graph['slice']['anchor_transaction_id']} +/- 24 h, closed interval)."
        )
    lines += ["", "## Model inference (LightGBM)", ""]
    model = results.get("model")
    if model:
        lines += [
            "| Workload | Samples | P50 | P95 | P99 | Iteration 1 P50 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        single = model["single_prepared_row"]
        lines.append(
            f"| One prepared row, 1 thread | {single['samples']} | {_ms(single['p50_ms'])} | "
            f"{_ms(single['p95_ms'])} | {_ms(single['p99_ms'])} | "
            f"{_ms(ITERATION_1['single_row_p50_ms'])} |"
        )
        for size, entry in model["batches"].items():
            before = ITERATION_1.get(f"batch_{size}_p50_ms")
            lines.append(
                f"| Batch of {int(size):,}, 1 thread | {entry['samples']} | {_ms(entry['p50_ms'])} | "
                f"{_ms(entry['p95_ms'])} | {_ms(entry['p99_ms'])} | {_ms(before)} |"
            )
        lines += [
            "",
            "| Full held-out cohort | Rows | Median | Throughput | CPU s / 1k tx | Parity |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for threads, entry in model["full_test_cohort"].items():
            lines.append(
                f"| {threads} thread(s) | {entry['rows']:,} | {entry['median_seconds']:.3f} s | "
                f"{entry['transactions_per_second']:,.0f} tx/s | "
                f"{entry['cpu_seconds_per_1000_transactions']:.5f} | "
                f"max abs err {entry['max_abs_error_vs_saved_scores']:g} |"
            )
        lines.append(
            f"| Iteration 1, 1 thread | 761,639 | {ITERATION_1['cohort_seconds']:.3f} s | "
            f"{ITERATION_1['cohort_tps']:,.0f} tx/s | n/a | 0 |"
        )
        busy = max(
            entry.get("foreign_host_cores_max", 0.0) for entry in model["full_test_cohort"].values()
        )
        if busy >= 1.5:
            lines += [
                "",
                (f"CONTAMINATED: other host processes used up to {busy:.1f} hardware threads during "
                "the model workload; treat these timings as an upper bound."),
            ]
    serial = results.get("api_serial")
    if serial:
        lines += [
            "",
            "## API, serial (1 uvicorn worker, loopback HTTP)",
            "",
            "| Request | Samples | P50 | P95 | P99 | Serial req/s | Iteration 1 P50 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        before_map = {
            "alerts_limit_50": ITERATION_1["alerts_p50_ms"],
            "benchmarks": ITERATION_1["benchmarks_p50_ms"],
            "investigation": ITERATION_1["investigation_p50_ms"],
        }
        labels = {
            "health": "`GET /api/health`",
            "alerts_limit_50": "`GET /api/alerts?limit=50`",
            "benchmarks": "`GET /api/benchmarks`",
            "cases": "`GET /api/cases`",
            "investigation": "`POST /api/investigations`",
        }
        for key, label in labels.items():
            entry = serial.get(key)
            if not entry:
                continue
            lines.append(
                f"| {label} | {entry['samples']} | {_ms(entry['p50_ms'])} | {_ms(entry['p95_ms'])} | "
                f"{_ms(entry['p99_ms'])} | {entry['serial_requests_per_second']:,.2f} | "
                f"{_ms(before_map.get(key))} |"
            )
        inv = serial.get("investigation")
        if inv:
            lines += [
                "",
                (f"Investigation evidence: {inv['evidence_transactions']} transactions, "
                f"{inv['evidence_accounts']} accounts, {inv['evidence_paths']} paths; "
                f"validated={inv['all_reports_validated']}, stable={inv['evidence_counts_stable']}. "
                f"Server evidence phase P50 {_ms(inv['graph_evidence_phase']['p50_ms'])}."),
            ]
        sample = serial.get("investigation_sample")
        if sample:
            lines += [
                "",
                "| Sampled high-score case | Score rank | P50 | Transactions / accounts / paths |",
                "| --- | ---: | ---: | --- |",
            ]
            for case in sample["cases"]:
                lines.append(
                    f"| `{case['transaction_id']}` | {case['score_rank']} | {_ms(case['p50_ms'])} | "
                    f"{case['evidence_transactions']} / {case['evidence_accounts']} / "
                    f"{case['evidence_paths']} |"
                )
            lines.append(
                f"| All sampled cases | | {_ms(sample['all']['p50_ms'])} "
                f"(P95 {_ms(sample['all']['p95_ms'])}) | |"
            )
    graph_tools = results.get("graph_tools")
    if graph_tools:
        lines += [
            "",
            "## Direct graph tools (serial, one connection)",
            "",
            ("| Tool | Samples | P50 | P95 | P99 | Iteration 1 P50 | Speed-up | Matches | "
            "DB calls | FalkorDB CPU s/call | Isolated |"),
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
        ]
        for name, entry in graph_tools["tools"].items():
            before = ITERATION_1.get(f"{name}_p50_ms")
            matches = (
                f"{entry['matches_min']}-{entry['matches_max']}" if "matches_min" in entry else ""
            )
            lines.append(
                f"| `{name}` | {entry['samples']} | {_ms(entry['p50_ms'])} | {_ms(entry['p95_ms'])} | "
                f"{_ms(entry['p99_ms'])} | {_ms(before)} | {_ratio(before, entry['p50_ms'])} | "
                f"{matches} | {entry.get('database_calls_per_invocation', 0):g} | "
                f"{entry.get('falkordb_cpu_seconds_per_invocation', float('nan')):.3f} | "
                f"{'yes' if entry.get('falkordb_cpu_isolated') else 'no'} |"
            )
    capacity = results.get("capacity")
    if capacity:
        lines += ["", "## API concurrent capacity (closed loop)", ""]
        for key, sweep in capacity.items():
            lines += [
                f"### {sweep['endpoint']}, {sweep['workers']} uvicorn worker(s)",
                "",
                ("| Clients | Requests | Throughput | P50 | P95 | P99 | Error rate | "
                "API CPU s/req | FalkorDB CPU s/req | Other host threads | Clean |"),
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
            for level in sweep["levels"]:
                res = level["resources"]
                api_cpu = res.get("api_cpu_seconds_per_ok_request")
                db_cpu = res.get("falkordb_cpu_seconds_per_ok_request")
                api_text = f"{api_cpu:.4f}" if api_cpu is not None else "n/a"
                db_text = f"{db_cpu:.4f}" if db_cpu is not None else "n/a"
                lines.append(
                    f"| {level['concurrency']} | {level['requests']} | "
                    f"{level['throughput_rps']:,.2f}/s | {_ms(level['p50_ms'])} | "
                    f"{_ms(level['p95_ms'])} | {_ms(level['p99_ms'])} | "
                    f"{level['error_rate']:.1%} | {api_text} | {db_text} | "
                    f"{res.get('foreign_host_cores', float('nan')):.1f} | "
                    f"{'yes' if level.get('clean', True) else 'no'} |"
                )
            knee = sweep["knee"]
            lines += [
                "",
                (f"Knee: {knee['knee_concurrency']} clients at {knee['knee_throughput_rps']:,.2f} "
                f"req/s (P95 {_ms(knee['knee_p95_ms'])}); peak {knee['peak_throughput_rps']:,.2f} "
                f"req/s at {knee['peak_concurrency']} clients; first errors at "
                f"{knee['first_error_concurrency'] or 'none'}. Rule: {knee['rule']}."),
                "",
            ]
    cost = results.get("cost")
    if cost:
        basis = cost["price_basis"]
        lines += [
            "## Cost model (estimate)",
            "",
            (f"Prices: {basis['provider']} {basis['family']}, {basis['region']}: "
            f"${basis['vcpu_hour_usd']}/vCPU-hour, ${basis['gib_hour_usd']}/GiB-hour; reference "
            f"{basis['reference_instance']} ${basis['reference_instance_hour_usd']}/hour. Retrieved "
            f"{basis['retrieved_on']}; {basis['status']}. LLM cost: "
            f"${cost['external_llm_cost_usd']:.2f} ({cost['llm_note']})"),
            "",
            "| Unit | CPU s | Peak memory | Metered USD | Provisioned USD |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for threads, entry in cost.get("scoring", {}).items():
            lines.append(
                f"| 1,000 scored transactions ({threads} thread(s)) | "
                f"{entry['cpu_seconds_per_1000']:.5f} | {entry['peak_rss_mib']:,.0f} MiB | "
                f"${entry['metered_usd_per_1000']:.3g} | ${entry['provisioned_usd_per_1000']:.3g} |"
            )
        inv = cost.get("investigation")
        if inv:
            graph_mib = inv["graph_memory_mib"]
            lines.append(
                f"| 1 investigation ({inv['falkordb_cpu_source']}) | {inv['total_cpu_seconds']:.3f} "
                f"(API {inv['api_cpu_seconds']:.3f} + FalkorDB {inv['falkordb_cpu_seconds']:.3f}) | "
                f"API {inv['api_peak_rss_mib']:,.0f} MiB + graph "
                f"{graph_mib if graph_mib is not None else 'n/a'} MiB | "
                f"${inv['metered_usd']:.3g} | "
                f"{'$' + format(inv['provisioned_usd'], '.3g') if inv.get('provisioned_usd') else 'n/a'} |"
            )
    lines.append("")
    return "\n".join(lines)


def _latency(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "samples": entry["samples"],
        "p50_ms": entry["p50_ms"],
        "p95_ms": entry["p95_ms"],
        "p99_ms": entry["p99_ms"],
        "mean_ms": entry["mean_ms"],
    }


def build_snapshot(results: dict[str, Any]) -> dict[str, Any]:
    """Performance snapshot in the Iteration 1 shape, extended compatibly.

    Matches ``api.schemas.PerformanceSummary``: every Iteration 1 key keeps
    its meaning; added keys are optional in the schema.
    """
    env = results["environment"]
    hw = env["hardware"]
    graph = results["graph"]
    counts = graph["counts"]
    model = results["model"]
    cohort_1 = model["full_test_cohort"]["1"]
    all_key = max(model["full_test_cohort"], key=int)
    cohort_all = model["full_test_cohort"][all_key]
    serial = results["api_serial"]
    tools = results["graph_tools"]["tools"]
    inv = serial["investigation"]
    cost = results["cost"]

    def serial_entry(key: str) -> dict[str, Any]:
        entry = serial[key]
        return {**_latency(entry), "serial_requests_per_second": entry["serial_requests_per_second"]}

    def flow(name: str) -> dict[str, Any]:
        entry = tools[name]
        return {
            **_latency(entry),
            "matches_min": entry["matches_min"],
            "matches_max": entry["matches_max"],
        }

    def cohort(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "rows": entry["rows"],
            "repeats": entry["repeats"],
            "median_seconds": entry["median_seconds"],
            "transactions_per_second": entry["transactions_per_second"],
            "threads": entry["threads"],
            "max_abs_error_vs_saved_scores": entry["max_abs_error_vs_saved_scores"],
        }

    capacity_out: dict[str, Any] = {}
    for key, sweep in results.get("capacity", {}).items():
        capacity_out[key] = {
            "endpoint": sweep["endpoint"],
            "workers": sweep["workers"],
            "duration_seconds": sweep["duration_seconds"],
            "levels": [
                {
                    "concurrency": level["concurrency"],
                    "requests": level["requests"],
                    "throughput_rps": level["throughput_rps"],
                    "p50_ms": level["p50_ms"],
                    "p95_ms": level["p95_ms"],
                    "p99_ms": level["p99_ms"],
                    "error_rate": level["error_rate"],
                }
                for level in sweep["levels"]
            ],
            "knee_concurrency": sweep["knee"]["knee_concurrency"],
            "peak_throughput_rps": sweep["knee"]["peak_throughput_rps"],
            "first_error_concurrency": sweep["knee"]["first_error_concurrency"],
        }

    scoring_cost = cost.get("scoring", {}).get("1", {})
    inv_cost = cost.get("investigation", {})
    falkor_cfg = env.get("falkordb", {}).get("config", {})
    return {
        "measured_on": results["measured_on"],
        "dataset_label": "Synthetic IBM AMLworld HI-Small v8",
        "hardware": (
            f"{hw['cpu']}, {hw['physical_cores']} cores / {hw['logical_threads']} threads, "
            f"{hw['memory_total_gib']} GiB system RAM"
        ),
        "graph_scope": (
            f"Target-free two-day slice: {counts['transactions']} transactions and "
            f"{counts['accounts']} accounts"
        ),
        "execution_mode": (
            "Serial latency plus closed-loop concurrency sweeps against a real uvicorn process "
            "over loopback"
            + _contamination_note(results)
        ),
        "run_id": results["run_id"],
        "git_commit": env["git"]["commit"],
        "model_inference": {
            "single_prepared_row": _latency(model["single_prepared_row"]),
            "full_test_cohort": cohort(cohort_1),
            "full_test_cohort_all_threads": cohort(cohort_all),
            "batches": {size: _latency(entry) for size, entry in model["batches"].items()},
        },
        "api": {
            "health": serial_entry("health"),
            "alerts_limit_50": serial_entry("alerts_limit_50"),
            "benchmarks": serial_entry("benchmarks"),
            "cases": serial_entry("cases"),
            "investigation": {
                **serial_entry("investigation"),
                "graph_query_sum": _latency(inv["graph_evidence_phase"]),
                "evidence_transactions": inv["evidence_transactions"],
                "evidence_accounts": inv["evidence_accounts"],
                "evidence_paths": inv["evidence_paths"],
                "all_reports_validated": inv["all_reports_validated"],
            },
        },
        "graph_queries": {
            "trace_funds": _latency(tools["trace_funds"]),
            "inspect_counterparties": _latency(tools["inspect_counterparties"]),
            "fan_out": flow("fan_out"),
            "fan_in": flow("fan_in"),
            "pass_through": flow("pass_through"),
        },
        "concurrency": {
            "falkordb_thread_count": _int_or_none(falkor_cfg.get("THREAD_COUNT")),
            "falkordb_max_queued_queries": _int_or_none(falkor_cfg.get("MAX_QUEUED_QUERIES")),
            "sweeps": capacity_out,
        },
        "cost": {
            "external_llm_calls": 0,
            "external_llm_cost": "none incurred; no LLM provider is called",
            "local_compute_cost": (
                "estimated from measured CPU-seconds and resident memory at GCP N2D us-central1 "
                "on-demand list prices retrieved 2026-10-16"
            ),
            "cost_per_1000_decisions": (
                f"about ${scoring_cost.get('metered_usd_per_1000', 0):.2g} per 1,000 LightGBM "
                "scores (metered, 1 thread); investigation about "
                f"${inv_cost.get('metered_usd', 0):.2g} each (metered, serial)"
            ),
            "dollar_weighted_recall": "not measured; no valuation basis selected",
            "price_basis": (
                f"GCP N2D us-central1 on-demand: ${cost['price_basis']['vcpu_hour_usd']}/vCPU-h, "
                f"${cost['price_basis']['gib_hour_usd']}/GiB-h; estimate"
            ),
            "scoring_usd_per_1000_transactions": scoring_cost.get("metered_usd_per_1000"),
            "scoring_cpu_seconds_per_1000_transactions": scoring_cost.get("cpu_seconds_per_1000"),
            "investigation_usd": inv_cost.get("metered_usd"),
            "investigation_cpu_seconds": inv_cost.get("total_cpu_seconds"),
            "investigation_provisioned_usd": inv_cost.get("provisioned_usd"),
        },
    }


def _contamination_note(results: dict[str, Any]) -> str:
    noisy = sorted(
        {gate["phase"].split(" ")[0] for gate in results.get("quiet_gates", []) if not gate["quiet"]}
    )
    if not noisy:
        return ""
    return f"; host not idle at the start of: {', '.join(noisy)} (timings are upper bounds)"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
