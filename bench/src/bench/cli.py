"""``traceaml-bench``: run the full benchmark suite with one command.

Writes ``<run-root>/bench/<UTC timestamp>/results.json`` (all raw summaries,
environment, input hashes, protocol), ``summary.md`` (generated tables), and
``performance-snapshot.json`` (the API ``PerformanceSummary`` shape). With
``--publish-snapshot PATH`` the snapshot is also copied to PATH.

Workloads (``--workloads``, comma separated, default all):
``model``, ``graph`` (demo slice + direct tools), ``api`` (serial and
investigation sample), ``capacity`` (closed-loop sweeps). Labels are never
read: the slice uses the loader allowlist, the model reads only the
target-free feature table, and the API serves the target-free score file.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALL_WORKLOADS = ("model", "graph", "api", "capacity")
DEFAULT_ANCHOR = "hi8-tx-03863669"
# Other host processes may use at most this many hardware threads during a
# measured window before the window is flagged as contaminated.
HOST_FOREIGN_CORES = 1.5


def log(message: str) -> None:
    print(f"[bench {datetime.now().strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def _ints(value: str) -> list[int]:
    return [int(part) for part in value.split(",") if part.strip()]


def build_parser(repo: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-root", type=Path, default=repo / "artifacts/runs/hi-small-v8")
    parser.add_argument(
        "--transactions",
        type=Path,
        default=repo / "data/processed/hi-small-v8/transactions.parquet",
    )
    parser.add_argument(
        "--slice-out", type=Path, default=repo / "data/interim/bench/demo-slice.parquet"
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--graph-name", default="agentbench-demo")
    parser.add_argument("--reload-graph", action="store_true")
    parser.add_argument("--anchor", default=DEFAULT_ANCHOR)
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--max-hops", type=int, default=2)
    parser.add_argument("--window", type=int, default=86_400)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--query-timeout-ms", type=int, default=5_000)
    parser.add_argument("--workloads", default=",".join(ALL_WORKLOADS))
    parser.add_argument("--api-workers", default="1,4")
    parser.add_argument("--levels", default="1,2,4,8,16,32")
    parser.add_argument("--alerts-duration", type=float, default=10.0)
    parser.add_argument("--investigation-duration", type=float, default=30.0)
    parser.add_argument("--light-repeats", type=int, default=200)
    parser.add_argument("--investigation-repeats", type=int, default=20)
    parser.add_argument("--graph-repeats", type=int, default=20)
    parser.add_argument("--cohort-repeats", type=int, default=3)
    parser.add_argument(
        "--quick", action="store_true", help="Smoke run with reduced repeats and durations."
    )
    parser.add_argument("--publish-snapshot", type=Path, default=None)
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="Existing run directory: add the selected workloads to its results.json.",
    )
    parser.add_argument(
        "--quiet-budget-seconds",
        type=float,
        default=2400.0,
        help="Total quiet-gate waiting allowed per run.",
    )
    parser.add_argument(
        "--level-attempts",
        type=int,
        default=2,
        help="Max measurements of a capacity level when foreign load contaminates it.",
    )
    parser.add_argument(
        "--quiet-wait-seconds",
        type=float,
        default=900.0,
        help="Max wait per phase for other FalkorDB clients to go idle (0 disables).",
    )
    return parser


def foreign_rate(meter: Any, seconds: float = 5.0) -> float:
    start = meter.graph_query_calls()
    time.sleep(seconds)
    return (meter.graph_query_calls() - start) / seconds


def select_sample(
    scores: Path, start: str, end: str, anchor: str, size: int
) -> tuple[list[dict[str, Any]], int]:
    """Top-``size`` held-out scores inside the slice window, excluding the anchor.

    Uses only the target-free score artifact. Ties break by transaction_id.
    Also returns the anchor's global rank (1 = highest score).
    """
    import duckdb

    path = str(scores).replace("'", "''")
    con = duckdb.connect()
    try:
        ranked = (
            f"SELECT transaction_id, timestamp, score, row_number() OVER "
            f"(ORDER BY score DESC, transaction_id) AS score_rank FROM read_parquet('{path}')"
        )
        rows = con.execute(
            f"SELECT transaction_id, score, score_rank FROM ({ranked}) "
            "WHERE CAST(timestamp AS TIMESTAMP) BETWEEN CAST(? AS TIMESTAMP) AND CAST(? AS TIMESTAMP) "
            "AND transaction_id <> ? ORDER BY score_rank LIMIT ?",
            [start, end, anchor, size],
        ).fetchall()
        anchor_rank = con.execute(
            f"SELECT score_rank FROM ({ranked}) WHERE transaction_id = ?", [anchor]
        ).fetchone()
    finally:
        con.close()
    sample = [
        {"transaction_id": row[0], "score": float(row[1]), "score_rank": int(row[2])}
        for row in rows
    ]
    return sample, int(anchor_rank[0]) if anchor_rank else -1


def main(argv: list[str] | None = None) -> int:
    from api.settings import REPOSITORY_ROOT, Settings

    from bench import api_load, demo_slice, env, graph_tools, model, report
    from bench.cost import build_cost_model
    from bench.stats import find_knee, summarize_ms

    repo = REPOSITORY_ROOT
    args = build_parser(repo).parse_args(argv)
    if args.quick:
        args.light_repeats = 30
        args.investigation_repeats = 5
        args.graph_repeats = 5
        args.cohort_repeats = 1
        args.alerts_duration = 3.0
        args.investigation_duration = 8.0
        args.sample_size = 3
    workloads = {part.strip() for part in args.workloads.split(",") if part.strip()}
    unknown = workloads - set(ALL_WORKLOADS)
    if unknown:
        print(json.dumps({"error": f"unknown workloads: {sorted(unknown)}"}))
        return 2

    baselines = args.run_root / "baselines"
    inputs = {
        "transactions": args.transactions,
        "features": baselines / "features.parquet",
        "model": baselines / "models" / "lgbm.txt",
        "scores": baselines / "inference_scores.parquet",
        "baseline_config": baselines / "baseline_config.json",
        "baseline_metrics": baselines / "baseline_metrics.json",
        "graph_metrics": args.run_root / "graph-ablation" / "baseline_metrics.json",
        # The API serves its configured default snapshot during the run.
        "performance_snapshot_served": repo / Settings.model_fields["performance_path"].default,
    }
    missing = [name for name, path in inputs.items() if not path.is_file()]
    if missing:
        print(json.dumps({"error": f"missing inputs: {missing}"}))
        return 2

    prior: dict[str, Any] | None = None
    if args.resume is not None:
        prior = json.loads((args.resume / "results.json").read_text(encoding="utf-8"))
        run_id = str(prior["run_id"])
        out_dir = args.resume
    else:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_dir = args.run_root / "bench" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"writing to {out_dir}")

    results: dict[str, Any] = {
        "run_id": run_id,
        "measured_on": datetime.now(UTC).date().isoformat(),
        "dataset_label": "Synthetic IBM AMLworld HI-Small v8",
        "environment": env.environment_block(repo),
        "inputs": {
            name: {"path": str(path), "sha256": env.sha256_file(path)}
            for name, path in inputs.items()
        },
        "protocol": {
            "workloads": sorted(workloads),
            "model": {
                "single_row": {"warmups": 20, "repeats": 200},
                "batches": {"100": 100, "1000": 50, "10000": 20},
                "batch_warmups": 3,
                "cohort_repeats": args.cohort_repeats,
            },
            "api_serial": {
                "light_warmups": 10,
                "light_repeats": args.light_repeats,
                "investigation_warmups": 3,
                "investigation_repeats": args.investigation_repeats,
                "sample_warmups": 1,
                "sample_repeats": 3,
            },
            "graph_tools": {"warmups": 3, "repeats": args.graph_repeats},
            "capacity": {
                "levels": _ints(args.levels),
                "api_workers": _ints(args.api_workers),
                "alerts_duration_seconds": args.alerts_duration,
                "investigation_duration_seconds": args.investigation_duration,
                "warmup_requests_per_client": 1,
                "client": "httpx.AsyncClient, closed loop, one connection per client",
                "server": "uvicorn api.app:app, loopback",
            },
            "investigation": {
                "anchor": args.anchor,
                "max_hops": args.max_hops,
                "time_window_seconds": args.window,
                "result_limit": args.limit,
                "graph_query_timeout_ms": args.query_timeout_ms,
            },
            "percentiles": "linear interpolation (NumPy default)",
            "quick": args.quick,
        },
    }
    if prior is not None:
        # Resume: keep completed workloads from the interrupted run, refuse
        # if any input changed, and record what this invocation added.
        changed = [
            name
            for name, entry in results["inputs"].items()
            if prior["inputs"].get(name, {}).get("sha256") != entry["sha256"]
        ]
        if changed:
            print(json.dumps({"error": f"inputs changed since the resumed run: {changed}"}))
            return 1
        resume_entry = {
            "started_utc": datetime.now(UTC).isoformat(),
            "workloads": sorted(workloads),
            "git": results["environment"]["git"],
        }
        prior["protocol"]["workloads"] = sorted(set(prior["protocol"]["workloads"]) | workloads)
        prior.setdefault("resumes", []).append(resume_entry)
        prior.setdefault("quiet_gates", [])
        results = prior

    meter = env.FalkorMeter(args.host, args.port)
    needs_graph = bool(workloads & {"graph", "api", "capacity"})
    if needs_graph:
        results["environment"]["falkordb"] = {
            "server": meter.server(),
            "config": meter.config(),
            "graphs_present": meter.list_graphs(),
            "foreign_activity": {"before_queries_per_second": foreign_rate(meter)},
        }

    gates: list[dict[str, Any]] = results.setdefault("quiet_gates", [])

    budget = {"remaining": args.quiet_budget_seconds}

    def gate(phase: str) -> None:
        if args.quiet_wait_seconds <= 0:
            return
        # A long-running foreign job must not stall the suite for hours: once
        # the run-wide budget is spent, each gate only probes once.
        allowed = max(0.0, min(args.quiet_wait_seconds, budget["remaining"]))
        outcome = meter.wait_for_quiet(allowed)
        budget["remaining"] -= outcome["waited_seconds"]
        gates.append({"phase": phase, **outcome})
        if not outcome["quiet"]:
            log(f"WARNING: host or FalkorDB not idle before {phase}; results flagged")
        elif outcome["waited_seconds"] > 10:
            log(f"waited {outcome['waited_seconds']:.0f} s for FalkorDB to go idle before {phase}")

    def save() -> None:
        (out_dir / "results.json").write_text(
            json.dumps(results, indent=2, default=str), encoding="utf-8"
        )

    try:
        if "model" in workloads:
            gate("model")
            results["model"] = model.run_model_workloads(
                inputs["model"],
                inputs["features"],
                inputs["scores"],
                single_repeats=200,
                single_warmups=20,
                batch_plan={100: 100, 1000: 50, 10000: 20},
                cohort_repeats=args.cohort_repeats,
                log=log,
            )
            save()

        if needs_graph:
            log("graph: building target-free demo slice")
            info = demo_slice.build_slice(args.transactions, args.slice_out, args.anchor)
            loaded = demo_slice.ensure_demo_graph(
                info,
                source=args.transactions,
                host=args.host,
                port=args.port,
                graph_name=args.graph_name,
                force_reload=args.reload_graph,
                log=log,
            )
            results["graph"] = {
                "graph_name": args.graph_name,
                "selection_rule": (
                    "all transactions with timestamp in the closed interval "
                    "[anchor_ts - 86400 s, anchor_ts + 86400 s]; loader allowlist columns only"
                ),
                "slice": {
                    "anchor_transaction_id": info.anchor_transaction_id,
                    "anchor_timestamp": info.anchor_timestamp,
                    "window_start": info.window_start,
                    "window_end": info.window_end,
                    "rows": info.rows,
                    "sha256": info.sha256,
                },
                **loaded,
                "graph_memory_mib": meter.graph_memory_mb(args.graph_name),
                "falkordb_used_memory_mib": meter.used_memory() / 2**20,
            }
            sample, anchor_rank = select_sample(
                inputs["scores"],
                info.window_start,
                info.window_end,
                args.anchor,
                args.sample_size,
            )
            results["graph"]["anchor_score_rank"] = anchor_rank
            save()

        calls_per_investigation: float | None = None
        if "graph" in workloads:
            gate("graph_tools")
            results["graph_tools"] = graph_tools.run_graph_tool_workloads(
                host=args.host,
                port=args.port,
                graph_name=args.graph_name,
                transaction_id=args.anchor,
                max_hops=args.max_hops,
                window=args.window,
                limit=args.limit,
                timeout_ms=args.query_timeout_ms,
                warmups=3,
                repeats=args.graph_repeats,
                log=log,
                meter=meter,
            )
            calls_per_investigation = sum(
                entry["database_calls_per_invocation"]
                for entry in results["graph_tools"]["tools"].values()
            )
            results["graph_tools"]["database_calls_per_investigation"] = calls_per_investigation
            save()

        def attribute(resources: dict[str, Any], own_calls: float | None, own_parallel: int) -> bool:
            """Flag windows whose counters include other clients' or processes' work.

            Returns True when the window is clean: no foreign graph calls, no
            more blocked FalkorDB clients than this suite can cause, and
            other host processes below ``HOST_FOREIGN_CORES`` threads.
            """
            host_clean = resources.get("foreign_host_cores", 0.0) < HOST_FOREIGN_CORES
            resources["host_isolated"] = host_clean
            if "falkordb_graph_query_calls" not in resources or own_calls is None:
                return host_clean
            foreign = resources["falkordb_graph_query_calls"] - own_calls
            resources["foreign_graph_calls_estimate"] = foreign
            resources["falkordb_cpu_isolated"] = (
                abs(foreign) < 1 and resources["falkordb_max_blocked_clients"] <= own_parallel
            )
            return bool(resources["falkordb_cpu_isolated"]) and host_clean

        server_env = {
            "TRACEAML_FALKORDB_HOST": args.host,
            "TRACEAML_FALKORDB_PORT": str(args.port),
            "TRACEAML_GRAPH_NAME": args.graph_name,
            "TRACEAML_GRAPH_QUERY_TIMEOUT_MS": str(args.query_timeout_ms),
            "TRACEAML_SCORE_ARTIFACT": str(inputs["scores"]),
            "TRACEAML_BASELINE_CONFIG_PATH": str(inputs["baseline_config"]),
            "TRACEAML_BASELINE_METRICS_PATH": str(inputs["baseline_metrics"]),
            "TRACEAML_GRAPH_METRICS_PATH": str(inputs["graph_metrics"]),
            "TRACEAML_PERFORMANCE_PATH": str(inputs["performance_snapshot_served"]),
            "TRACEAML_CASE_DB_PATH": str(out_dir / "cases.db"),
        }
        body = api_load.investigation_body(args.anchor, args.max_hops, args.window, args.limit)

        if "api" in workloads:
            gate("api_serial")
            log("api: serial workloads (1 worker)")
            serial: dict[str, Any] = {}
            with api_load.ApiServer(workers=1, env=server_env, log_dir=out_dir) as server:
                import httpx

                assert server.process is not None
                with httpx.Client(
                    base_url=server.base_url, timeout=api_load.HTTP_TIMEOUT_SECONDS
                ) as client:
                    for key, path in (
                        ("health", "/api/health"),
                        ("alerts_limit_50", "/api/alerts?limit=50"),
                        ("benchmarks", "/api/benchmarks"),
                        ("cases", "/api/cases"),
                    ):
                        log(f"api serial: {path}")
                        serial[key], _ = api_load.serial_endpoint(
                            client,
                            "GET",
                            path,
                            body=None,
                            warmups=10,
                            repeats=args.light_repeats,
                        )
                    log("api serial: POST /api/investigations")
                    for _ in range(3):
                        client.post("/api/investigations", json=body).raise_for_status()
                    with env.ResourceWindow(server.process, meter, interval=0.1) as window:
                        stats, responses = api_load.serial_endpoint(
                            client,
                            "POST",
                            "/api/investigations",
                            body=body,
                            warmups=0,
                            repeats=args.investigation_repeats,
                        )
                    stats["warmups"] = 3
                    serial["investigation"] = {**stats, **api_load.evidence_summary(responses)}
                    resources = window.result()
                    resources["requests_in_window"] = args.investigation_repeats
                    attribute(
                        resources,
                        calls_per_investigation * args.investigation_repeats
                        if calls_per_investigation is not None
                        else None,
                        own_parallel=5,
                    )
                    serial["investigation_resources"] = resources

                    log(f"api serial: {len(sample)} sampled high-score cases")
                    cases = []
                    all_samples: list[float] = []
                    for case in sample:
                        case_body = api_load.investigation_body(
                            case["transaction_id"], args.max_hops, args.window, args.limit
                        )
                        try:
                            case_stats, case_responses = api_load.serial_endpoint(
                                client,
                                "POST",
                                "/api/investigations",
                                body=case_body,
                                warmups=1,
                                repeats=3,
                            )
                        except httpx.HTTPStatusError as exc:
                            cases.append(
                                {**case, "error_status": exc.response.status_code}
                            )
                            continue
                        evidence = api_load.evidence_summary(case_responses)
                        cases.append(
                            {
                                **case,
                                "p50_ms": case_stats["p50_ms"],
                                "p95_ms": case_stats["p95_ms"],
                                "evidence_transactions": evidence["evidence_transactions"],
                                "evidence_accounts": evidence["evidence_accounts"],
                                "evidence_paths": evidence["evidence_paths"],
                                "validated": evidence["all_reports_validated"],
                                "tool_p50_ms": {
                                    tool: entry["p50_ms"]
                                    for tool, entry in evidence["tool_durations_in_api"].items()
                                },
                            }
                        )
                        all_samples.extend(case_stats["raw_ms"])
                    serial["investigation_sample"] = {
                        "selection_rule": (
                            "top-scored held-out transactions (target-free score artifact) "
                            "inside the slice window, excluding the anchor; ties by "
                            "transaction_id"
                        ),
                        "cases": cases,
                        "all": summarize_ms(all_samples) if all_samples else None,
                    }
            results["api_serial"] = serial
            save()

        if "capacity" in workloads:
            capacity: dict[str, Any] = {}
            for workers in _ints(args.api_workers):
                with api_load.ApiServer(workers=workers, env=server_env, log_dir=out_dir) as server:
                    for name, method, path, bodies, duration in (
                        ("alerts", "GET", "/api/alerts?limit=50", None, args.alerts_duration),
                        (
                            "investigations",
                            "POST",
                            "/api/investigations",
                            [body],
                            args.investigation_duration,
                        ),
                    ):
                        def classify(level: dict[str, Any], name: str = name) -> bool:
                            res = level["resources"]
                            own = (
                                0.0
                                if name == "alerts"
                                else (
                                    calls_per_investigation * res["requests_in_window"]
                                    if calls_per_investigation is not None
                                    else None
                                )
                            )
                            return attribute(
                                res,
                                own,
                                own_parallel=0 if name == "alerts" else 5 * level["concurrency"],
                            )

                        levels = api_load.capacity_sweep(
                            server,
                            meter,
                            gate=gate,
                            classify=classify,
                            max_attempts=args.level_attempts,
                            method=method,
                            path=path,
                            bodies=bodies,
                            levels=_ints(args.levels),
                            duration=duration,
                            warmup_per_client=1,
                            log=log,
                        )
                        capacity[f"{name}_w{workers}"] = {
                            "endpoint": f"{method} {path}",
                            "workers": workers,
                            "duration_seconds": duration,
                            "levels": levels,
                            "knee": find_knee(levels),
                        }
                        save()
            results["capacity"] = capacity
            investigation_peaks = [
                sweep["knee"]["peak_throughput_rps"]
                for key, sweep in capacity.items()
                if key.startswith("investigations")
            ]
            results["capacity_summary"] = {
                "investigations_peak_rps": max(investigation_peaks) if investigation_peaks else None
            }

        if needs_graph:
            results["environment"]["falkordb"]["foreign_activity"]["after_queries_per_second"] = (
                foreign_rate(meter)
            )
    finally:
        meter.close()
        save()

    results["cost"] = build_cost_model(results)
    save()
    (out_dir / "summary.md").write_text(report.render_markdown(results), encoding="utf-8")
    if all(key in results for key in ("model", "graph_tools", "api_serial")):
        from api.schemas import PerformanceSummary

        snapshot = report.build_snapshot(results)
        # Fail here rather than in the API: the snapshot must match the contract.
        PerformanceSummary.model_validate(snapshot)
        snapshot_path = out_dir / "performance-snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        if args.publish_snapshot is not None:
            args.publish_snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snapshot_path, args.publish_snapshot)
            log(f"snapshot published to {args.publish_snapshot}")
    log(f"done: {out_dir}")
    print(json.dumps({"run_dir": str(out_dir), "run_id": run_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
