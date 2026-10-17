"""API workloads against a real uvicorn process over loopback HTTP.

``ApiServer`` launches ``uvicorn api.app:app`` as a child process with the
benchmark graph and artifact paths in ``TRACEAML_*`` environment variables,
waits for ``/api/health``, and kills the whole process tree on exit.

Serial workloads issue one request at a time from a synchronous client.
Capacity workloads run ``c`` closed-loop asyncio clients for a fixed
duration: each client sends its next request as soon as the previous one
completes. Requests started before the deadline are measured to completion.
Throughput is completed requests divided by the elapsed time from the
first start to the last completion. Errors are non-2xx responses and
transport exceptions (timeouts included).
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import socket
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Self

import httpx
import psutil

from bench.env import FalkorMeter, ResourceWindow
from bench.stats import summarize_ms

HTTP_TIMEOUT_SECONDS = 120.0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ApiServer:
    """A uvicorn child process serving ``api.app:app``."""

    def __init__(self, *, workers: int, env: dict[str, str], log_dir: Path) -> None:
        self.workers = workers
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._env = {**os.environ, **env}
        self._log_path = log_dir / f"uvicorn-w{workers}.log"
        self.process: psutil.Process | None = None
        self._popen: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> Self:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self._log_path.open("wb")
        self._popen = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--workers",
                str(self.workers),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            env=self._env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        self.process = psutil.Process(self._popen.pid)
        deadline = time.monotonic() + 180
        with httpx.Client(timeout=5.0) as client:
            while time.monotonic() < deadline:
                if self._popen.poll() is not None:
                    raise RuntimeError(f"uvicorn exited early; see {self._log_path}")
                try:
                    if client.get(f"{self.base_url}/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("uvicorn did not become healthy within 180 s")
            # Every worker preloads the score artifact at startup; wait until
            # all worker processes exist and their memory has settled.
            settle_deadline = time.monotonic() + 120
            last = -1
            while time.monotonic() < settle_deadline:
                children = self.process.children(recursive=True)
                rss = sum(child.memory_info().rss for child in children)
                if len(children) >= self.workers and abs(rss - last) < 2 * 2**20:
                    break
                last = rss
                time.sleep(1.0)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.process is not None:
            procs = [self.process, *self.process.children(recursive=True)]
            for proc in reversed(procs):
                try:
                    proc.kill()
                except psutil.Error:
                    pass
            psutil.wait_procs(procs, timeout=10)
        self._log.close()


def investigation_body(transaction_id: str, max_hops: int, window: int, limit: int) -> dict:
    return {
        "transaction_id": transaction_id,
        "max_hops": max_hops,
        "time_window_seconds": window,
        "result_limit": limit,
    }


def serial_endpoint(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None,
    warmups: int,
    repeats: int,
) -> tuple[dict[str, Any], list[httpx.Response]]:
    def send() -> httpx.Response:
        if method == "GET":
            return client.get(url)
        return client.post(url, json=body)

    for _ in range(warmups):
        send().raise_for_status()
    samples: list[float] = []
    responses: list[httpx.Response] = []
    started_all = time.perf_counter()
    for _ in range(repeats):
        started = time.perf_counter()
        response = send()
        samples.append((time.perf_counter() - started) * 1000.0)
        response.raise_for_status()
        responses.append(response)
    elapsed = time.perf_counter() - started_all
    stats = summarize_ms(samples)
    stats["warmups"] = warmups
    stats["serial_requests_per_second"] = repeats / elapsed
    stats["raw_ms"] = samples
    return stats, responses


def evidence_summary(responses: list[httpx.Response]) -> dict[str, Any]:
    payloads = [response.json() for response in responses]
    counts = {
        (
            len(p["evidence"]["transactions"]),
            len(p["evidence"]["accounts"]),
            len(p["evidence"]["paths"]),
        )
        for p in payloads
    }
    server_ms = [float(p["evidence"]["query_duration_ms"]) for p in payloads]
    tool_ms: dict[str, list[float]] = {}
    for payload in payloads:
        for entry in payload["evidence"].get("tool_durations_ms", []):
            tool_ms.setdefault(entry["tool"], []).append(float(entry["query_duration_ms"]))
    tx, accounts, paths = min(counts)
    return {
        "evidence_transactions": tx,
        "evidence_accounts": accounts,
        "evidence_paths": paths,
        "evidence_counts_stable": len(counts) == 1,
        "all_reports_validated": all(
            p.get("report") is not None and p["report"]["validation"] == "validated"
            for p in payloads
        ),
        "decision": payloads[0]["decision"],
        "graph_evidence_phase": summarize_ms(server_ms),
        "graph_tool_sum": summarize_ms(
            [sum(values) for values in zip(*tool_ms.values(), strict=False)]
        )
        if tool_ms
        else None,
        "tool_durations_in_api": {tool: summarize_ms(values) for tool, values in tool_ms.items()},
    }


async def _closed_loop(
    base_url: str,
    method: str,
    path: str,
    bodies: list[dict[str, Any]] | None,
    concurrency: int,
    duration: float,
    warmup_per_client: int,
) -> dict[str, Any]:
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(
        base_url=base_url, limits=limits, timeout=HTTP_TIMEOUT_SECONDS
    ) as client:

        async def send(index: int) -> httpx.Response:
            if method == "GET":
                return await client.get(path)
            body = bodies[index % len(bodies)] if bodies else None
            return await client.post(path, json=body)

        async def warm(client_index: int) -> None:
            for step in range(warmup_per_client):
                try:
                    await send(client_index + step)
                except httpx.HTTPError:
                    pass

        await asyncio.gather(*(warm(i) for i in range(concurrency)))

        latencies: list[float] = []
        ok_latencies: list[float] = []
        statuses: Counter[str] = Counter()
        loop = asyncio.get_running_loop()
        first_start = loop.time()
        deadline = first_start + duration
        last_end = first_start

        async def worker(client_index: int) -> None:
            nonlocal last_end
            sent = 0
            while loop.time() < deadline:
                started = loop.time()
                try:
                    response = await send(client_index + sent)
                    key = str(response.status_code)
                    ok = 200 <= response.status_code < 300
                except httpx.TimeoutException:
                    key, ok = "client_timeout", False
                except httpx.HTTPError as exc:
                    key, ok = type(exc).__name__, False
                ended = loop.time()
                elapsed_ms = (ended - started) * 1000.0
                latencies.append(elapsed_ms)
                if ok:
                    ok_latencies.append(elapsed_ms)
                statuses[key] += 1
                last_end = max(last_end, ended)
                sent += 1

        await asyncio.gather(*(worker(i) for i in range(concurrency)))
        elapsed = last_end - first_start
    total = len(latencies)
    errors = total - len(ok_latencies)
    return {
        "concurrency": concurrency,
        "duration_target_seconds": duration,
        "elapsed_seconds": elapsed,
        "requests": total,
        "errors": errors,
        "error_rate": errors / total if total else 0.0,
        "status_counts": dict(statuses),
        "throughput_rps": len(ok_latencies) / elapsed if elapsed > 0 else 0.0,
        **{key: value for key, value in summarize_ms(latencies).items() if key != "samples"},
        "ok_latency": summarize_ms(ok_latencies) if ok_latencies else None,
    }


def capacity_sweep(
    server: ApiServer,
    falkor: FalkorMeter | None,
    *,
    method: str,
    path: str,
    bodies: list[dict[str, Any]] | None,
    levels: list[int],
    duration: float,
    warmup_per_client: int,
    log: Any,
    gate: Any = None,
    classify: Any = None,
    max_attempts: int = 2,
) -> list[dict[str, Any]]:
    """Sweep concurrency levels; rerun a level once when it was contaminated.

    The load generator runs in its own process so the resource sampler in
    this process cannot steal interpreter time from it. ``classify(level)``
    annotates a finished level and returns True when no foreign load was
    observed; a contaminated level is re-measured after the quiet gate, up
    to ``max_attempts`` times, and every attempt is kept in ``attempts``.
    """
    assert server.process is not None
    results = []
    with ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn")) as loadgen:
        for concurrency in levels:
            attempts: list[dict[str, Any]] = []
            for attempt in range(1, max_attempts + 1):
                if gate is not None:
                    gate(f"capacity {method} {path} w{server.workers} c{concurrency}")
                log(
                    f"capacity: {method} {path} workers={server.workers} c={concurrency}"
                    f" attempt {attempt}"
                )
                with ResourceWindow(server.process, falkor, interval=0.5) as window:
                    level = loadgen.submit(
                        run_closed_loop,
                        server.base_url,
                        method,
                        path,
                        bodies,
                        concurrency,
                        duration,
                        warmup_per_client,
                    ).result()
                resources = window.result()
                # The window also covers the warmup requests; count them so CPU
                # per request is not overstated.
                ok = level["requests"] - level["errors"] + concurrency * warmup_per_client
                resources["requests_in_window"] = ok
                resources["api_cpu_seconds_per_ok_request"] = (
                    resources["process_cpu_seconds"] / ok if ok else None
                )
                if "falkordb_cpu_seconds" in resources:
                    resources["falkordb_cpu_seconds_per_ok_request"] = (
                        resources["falkordb_cpu_seconds"] / ok if ok else None
                    )
                level["resources"] = resources
                level["attempt"] = attempt
                clean = classify(level) if classify is not None else True
                level["clean"] = bool(clean)
                attempts.append(level)
                if clean:
                    break
            final = attempts[-1]
            final["attempts"] = [
                {key: value for key, value in prior.items() if key != "attempts"}
                for prior in attempts[:-1]
            ]
            results.append(final)
    return results


def run_closed_loop(
    base_url: str,
    method: str,
    path: str,
    bodies: list[dict[str, Any]] | None,
    concurrency: int,
    duration: float,
    warmup_per_client: int,
) -> dict[str, Any]:
    """Process entry point for one closed-loop level (picklable for spawn)."""
    return asyncio.run(
        _closed_loop(base_url, method, path, bodies, concurrency, duration, warmup_per_client)
    )
