# Iteration 2 benchmark results

Measured 2026-10-16 on the local development machine with the `bench/` suite (`make bench`). These results use synthetic IBM AMLworld HI-Small v8 data and the frozen chronological test cohort. They describe this implementation and this host, not real-world AML performance or production capacity. All dollar figures are estimates.

Official run: `artifacts/runs/hi-small-v8/bench/20261016T083449Z/` (Git-ignored), snapshot `docs/benchmarks/iteration-2-performance.json`. The model workload ran at 14:20 local on merge commit `7dfd28c`. A reboot interrupted the run, so graph, API, and capacity workloads were resumed into the same run directory at 18:04 with `--resume` on `1227000`. The two commits differ only in benchmark-harness code (counter-read retries, resume, report flags). Application and graph code were identical for every workload. Inputs were re-hashed on resume and matched.

## Environment and scope

- Host: Intel Core i5-13400F, 10 cores (6P + 4E) / 16 threads, 15.84 GiB RAM, Gigabyte B760M, Windows 11 (10.0.26200), Python 3.14.2. The CPU name is read from the OS. The Iteration 1 report names an AMD Ryzen 9 8945HS. The graph team's Iteration 2 report and this run both read the Intel CPU on this machine, so the Iteration 1 numbers below are quoted as published and may come from different hardware.
- FalkorDB 4.22.1 (module 42201) on Redis 8.10.2 in Docker Desktop (WSL2 VM, 7.67 GiB, 16 CPUs). `THREAD_COUNT=16`, `OMP_THREAD_COUNT=16`, `MAX_QUEUED_QUERIES=25`, `TIMEOUT=1000`, `CACHE_SIZE=25`.
- Packages: LightGBM 4.7.0, NumPy 2.5.4, FastAPI 0.143.2, uvicorn 0.54.0, httpx 0.28.1, falkordb-py 1.8.0.
- Investigation graph `agentbench-demo`: 998,021 transactions, 354,660 accounts, 998,021 `SENT` and 998,021 `RECEIVED` edges. These counts match Iteration 1 exactly. It was built with `traceaml-load-graph --mode create --window-around hi8-tx-03863669 --window-seconds 86400`, in 75.6 s. Selection rule: every transaction with a timestamp in the closed interval [anchor - 86,400 s, anchor + 86,400 s] (2022-09-08 12:20 to 2022-09-10 12:20). Only loader-allowlisted columns are used; labels and split are never read. The suite independently rebuilds the same slice with DuckDB, and the row count matched (slice sha256 `bb5700d4e399abecb41c584a77aca305541d3eb7d2a1121883db24ccb7678a71`). Graph memory: 563 MiB (`GRAPH.MEMORY USAGE`).
- Investigation case: `hi8-tx-03863669` (score rank 1 of 761,639 held-out transactions), 2 hops, 86,400-second window, 50-result limit, 5,000 ms query timeout.
- LLM: none. No provider is configured or called, and the report path is deterministic.

### Contamination and gating

Other agents shared this host and FalkorDB server during the run. Before each phase and each capacity level, the suite waits for an idle host: no foreign graph calls, no blocked FalkorDB clients, FalkorDB CPU below 0.15 cores, and fewer than 1.5 other host threads busy. The wait is capped by a 2,400 s run-wide budget. Every measured window records the background load it saw.

- **Clean:** the direct graph tools, the serial API workloads, and the sampled investigations (no foreign FalkorDB calls; background below threshold).
- **Contaminated:** the model workload (a concurrent detection training job used up to 8.5 of 16 threads) and most capacity levels (1.9 to 11.6 background threads). Treat these figures as lower bounds on capacity and upper bounds on latency. Each level's background load is in the tables. Only three capacity levels were clean.

## Scoring and API latency

Model timings call `booster.predict` on an already prepared float64 matrix with one thread unless noted. They exclude feature retrieval, categorical encoding, graph access, and serialization. Single-row: 20 warmups and 200 samples. Batches: 3 warmups each. Cohort: 3 repeats. Held-out predictions matched the saved score artifact exactly (max absolute difference 0.0) for both thread counts.

| Workload | Repeats | Iteration 1 P50 | Iteration 2 P50 | Iteration 2 P95 | Iteration 2 P99 | Throughput |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| One prepared transaction, LightGBM only (contaminated) | 200 | 0.029 ms | 0.084 ms | 0.109 ms | 0.122 ms | — |
| Batch of 100, LightGBM only (contaminated) | 100 | 1.046 ms | 2.44 ms | 4.02 ms | 5.34 ms | — |
| Batch of 1,000, LightGBM only (contaminated) | 50 | 11.441 ms | 21.05 ms | 28.43 ms | 31.41 ms | — |
| Batch of 10,000, LightGBM only (contaminated) | 20 | 72.446 ms | 134 ms | 156 ms | 168 ms | — |
| Entire held-out cohort, 761,639 rows, 1 thread (contaminated) | 3 | 5.004 s median | 9.893 s median | — | — | 76,988 tx/s (Iteration 1: 152,214) |
| Entire held-out cohort, 761,639 rows, 16 threads (contaminated) | 3 | not measured | 1.052 s median | — | — | 724,030 tx/s |
| `GET /api/health` | 200 after 10 warmups | not measured | 6.01 ms | 22.70 ms | 29.43 ms | 124 requests/s, serial |
| `GET /api/alerts?limit=50` | 200 after 10 warmups | 1.087 ms | 1.34 ms | 1.83 ms | 2.62 ms | 718 requests/s, serial |
| `GET /api/benchmarks` | 200 after 10 warmups | 1.082 ms | 1.04 ms | 1.74 ms | 2.31 ms | 885 requests/s, serial |
| `GET /api/cases` | 200 after 10 warmups | not measured | 3.66 ms | 6.59 ms | 9.31 ms | 243 requests/s, serial |

The model rows are not a valid regression signal. They ran with 8.5 background threads busy on a 6P + 4E hybrid CPU, against an Iteration 1 baseline that may come from different hardware. Re-measure on an idle host before drawing any model-speed conclusion. Score parity is valid. `/api/health` opens a fresh FalkorDB connection per probe, which is why it is slower than the cached routes. The replay API serves precomputed scores and does not run the model per request.

## FalkorDB query and investigation latency

Direct tool measurements go through the API's own adapter (`api.investigate.FalkorGraphTools`) with the investigation's arguments: 3 warmups and 20 serial samples per tool on one connection. The metric is each tool's `query_duration_ms`, which covers all of its database calls. API investigations used 3 warmups and 20 HTTP requests to one uvicorn worker over loopback. Every report passed evidence validation and returned the same 25 transactions, 13 accounts, and 72 paths.

| Operation | Iteration 1 P50 | Iteration 1 P95 | Iteration 2 P50 | Iteration 2 P95 | Iteration 2 P99 | Speed-up (P50) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `trace_funds` | 5.46 ms | 6.65 ms | 5.68 ms | 6.21 ms | 6.83 ms | 1.0x |
| `inspect_counterparties` | 1.19 ms | 1.64 ms | 1.59 ms | 2.33 ms | 2.38 ms | 0.75x |
| `detect_flow_patterns` fan-out | 1,728 ms | 1,841 ms | 1.28 ms | 1.94 ms | 1.96 ms | 1,350x |
| `detect_flow_patterns` fan-in | 891 ms | 943 ms | 1.06 ms | 1.92 ms | 2.15 ms | 840x |
| `detect_flow_patterns` pass-through | 923 ms | 977 ms | 0.96 ms | 1.99 ms | 2.03 ms | 960x |
| Full `POST /api/investigations` request | 3,487 ms | 3,582 ms | 11.04 ms | 13.69 ms | 14.17 ms | 316x |
| Graph evidence phase inside that request | 3,481 ms | 3,576 ms | 8.97 ms | — | — | 388x |

The pattern queries no longer dominate. The graph team's query rewrite removed the timestamp-index scan, the pass-through cross product, and the second expansion (see [the graph report](iteration-2-graph-performance.md)). The API also runs the tools concurrently, so the evidence phase is now close to the slowest single tool plus overhead. Serial investigation throughput rose from 0.286 to 87.5 requests/s. Each tool makes 2 database calls, 11 per investigation in total.

Evidence changed by design. The latest-epoch fix (ADR-017) moves the account window end to the account's actual latest transfer, so fan-out now returns 7 counterparty matches where Iteration 1 returned 10. The path count changed from 75 to 72. The transaction (25) and account (13) counts are unchanged.

Ten other high-scored held-out cases in the slice were also sampled. They are the top-scored transactions in the target-free score artifact whose timestamps fall in the slice window, anchor excluded, ties broken by ID; score ranks 6 to 24. Each had 1 warmup and 3 samples. Across all 30 requests, P50 was 7.83 ms and P95 14.68 ms, with 6 to 49 evidence transactions per case. All reports validated.

## Concurrent capacity

Closed-loop clients (`httpx.AsyncClient`, one connection each) ran in a separate load-generator process against a real `uvicorn api.app:app` over loopback. Each level ran for 10 s (alerts) or 30 s (investigations), after one warmup request per client. A contaminated level was re-measured once after the quiet gate. The table shows the final attempt; all attempts are in `results.json`. Investigations used the fixed anchor case. The pool allows 16 FalkorDB connections per worker; FalkorDB itself runs `THREAD_COUNT=16` with `MAX_QUEUED_QUERIES=25`. Knee rule: the last error-free level whose throughput rose at least 10% over the previous level.

`POST /api/investigations`:

| Clients | 1 worker: throughput | P50 | P95 | P99 | Background threads | 4 workers: throughput | P50 | P95 | P99 | Background threads |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 32.3/s | 29.3 ms | 43.7 ms | 105 ms | 10.2 | 77.6/s | 11.9 ms | 16.9 ms | 47.8 ms | 2.1 |
| 2 | 53.7/s | 34.3 ms | 69.2 ms | 119 ms | 11.6 | 139.0/s | 13.5 ms | 18.1 ms | 58.0 ms | 2.4 |
| 4 | 54.5/s | 69.1 ms | 106 ms | 207 ms | 9.3 | 204.4/s | 17.7 ms | 28.2 ms | 76.3 ms | 3.2 |
| 8 | 56.0/s | 135 ms | 246 ms | 301 ms | 9.1 | 209.3/s | 31.4 ms | 62.5 ms | 121 ms | 4.9 |
| 16 | 56.5/s | 280 ms | 413 ms | 497 ms | 9.3 | 244.3/s | 61.6 ms | 126 ms | 180 ms | 4.6 |
| 32 | 60.6/s | 522 ms | 795 ms | 916 ms | 8.0 | 144.6/s | 111 ms | 735 ms | 1,226 ms | 4.4 |

`GET /api/alerts?limit=50`:

| Clients | 1 worker: throughput | P50 | P95 | P99 | Background threads | 4 workers: throughput | P50 | P95 | P99 | Background threads |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 440/s (clean) | 2.10 ms | 3.30 ms | 4.71 ms | 1.5 | 178/s | 5.22 ms | 10.1 ms | 12.5 ms | 8.6 |
| 2 | 757/s (clean) | 2.43 ms | 3.54 ms | 4.95 ms | 1.5 | 257/s | 6.47 ms | 15.3 ms | 26.2 ms | 9.1 |
| 4 | 580/s | 5.93 ms | 11.9 ms | 22.8 ms | 4.2 | 436/s | 8.38 ms | 14.9 ms | 19.3 ms | 8.1 |
| 8 | 400/s | 10.7 ms | 28.8 ms | 109 ms | 3.2 | 298/s | 19.8 ms | 68.0 ms | 141 ms | 8.8 |
| 16 | 222/s | 33.3 ms | 262 ms | 463 ms | 8.3 | 342/s | 19.3 ms | 175 ms | 296 ms | 1.9 |
| 32 | 166/s | 117 ms | 589 ms | 896 ms | 9.1 | 291/s (clean) | 68.7 ms | 331 ms | 526 ms | 1.3 |

No level returned an error: 0 non-2xx responses and 0 client timeouts across 83,842 measured requests.

Knee points (contaminated except where noted):

- Investigations, 1 worker: knee at 2 clients (53.7/s, P95 69 ms); throughput is flat at 54-61/s beyond it. API CPU is 0.016-0.022 s per request and FalkorDB CPU only 0.007-0.010 s, so one worker saturates about one core in Python (GIL-bound request handling) while FalkorDB stays lightly loaded.
- Investigations, 4 workers: knee at 4 clients (204/s, P95 28 ms); peak 244/s at 16 clients. At 32 clients throughput falls to 145/s and P95 rises to 735 ms.
- Alerts, 1 worker: knee at 2 clients (757/s, P95 3.5 ms; both levels clean). Beyond 2 clients, throughput falls while background load rises, so the decline cannot be separated from contamination in this run.
- Alerts, 4 workers: knee at 4 clients (436/s); the only clean level (32 clients) gives 291/s.

The pre-Iteration-2 pass in the Anomalies section showed what happens when graph queries are slow: with 16-32 clients, 503 responses (connection pool exhausted) and 500 responses (probably FalkorDB `MAX_QUEUED_QUERIES=25` rejections; the cause was not confirmed) appeared. With the optimized queries, no level reached those limits.

## Cost model (estimate)

Measured CPU-seconds and resident memory were converted at Google Compute Engine N2D (AMD EPYC) us-central1 on-demand Linux list prices: $0.027502 per vCPU-hour and $0.003686 per GiB-hour. The reference instance is n2d-standard-16 (16 vCPU, 64 GiB) at $0.6759 per hour. Sources: [Google Compute Engine VM pricing](https://cloud.google.com/compute/vm-instance-pricing) for the component rates, and [the Holori GCP catalogue](https://calculator.holori.com/gcp/vm/n2d-standard-4) for the instance prices (page updated 2026-10-15, retrieved 2026-10-16). The component rates reproduce the listed n2d-standard-2, -4, and -16 prices to four decimal places; re-check both sources before external use. No discounts are applied, and storage, network, and staff costs are excluded. A vCPU is priced as one host hardware thread.

- **Metered:** CPU-seconds x vCPU rate + resident GiB x wall seconds x GiB rate. This is the marginal cost of the work.
- **Provisioned:** the n2d-standard-16 hourly price divided by measured peak throughput, i.e. the cost per unit for a fully busy, always-on instance.

| Unit | Measured CPU | Resident memory | Metered USD | Provisioned USD |
| --- | ---: | ---: | ---: | ---: |
| 1,000 LightGBM scores, 1 thread (contaminated) | 0.0128 CPU-s | 316 MiB | $0.00000010 | $0.0000024 |
| 1,000 LightGBM scores, 16 threads (contaminated) | 0.0174 CPU-s | 317 MiB | $0.00000013 | $0.00000026 |
| 1 investigation, serial (clean window) | 0.0152 CPU-s (API 0.011 + FalkorDB 0.004) | API 535 MiB + graph 563 MiB | $0.00000013 | $0.00000077 at 244/s |
| 1,000 investigations | 15.2 CPU-s | as above | $0.00013 | $0.00077 |

On this basis, a million scored transactions cost about $0.0001 metered, and a thousand investigations about $0.0001-0.0008 depending on utilization. Compute is negligible next to storage, network, staff, and any future LLM use. These figures cover compute only, at synthetic-data scale, on a two-day graph slice. External LLM cost was $0.00: 0 calls. Energy was not metered and is not estimated.

## Anomalies and limits

- **Hardware label.** Iteration 1 names an AMD Ryzen 9 8945HS; this host reads as an Intel Core i5-13400F. Iteration 1 numbers are quoted as published. The graph team re-measured the frozen Iteration 1 statements on this machine; see their report for like-for-like graph comparisons.
- **Shared host.** A concurrent ML training job used 7-10 threads during the model workload and most capacity levels. Those figures are flagged. Their direction (latency higher, capacity lower) is known; their size is not.
- **Superseded pre-merge pass.** Run `20261016T074348Z` measured the pre-merge code under similar contention: investigation P50 1,886 ms, fan-out 1,065 ms, and 503/500 errors at 16-32 clients. Those results predate the query optimizations and a FalkorDB restart, and must not be quoted as Iteration 2 results.
- **Graph scope.** Two-day slice only; the full 5,078,345-transaction graph was not loaded. Timings do not establish full-graph latency.
- **One case.** Capacity used one anchor case; the 10-case sample covers serial latency only.
- **Not measured.** Dollar-weighted recall: no valuation basis has been selected.

## Reproduce

```sh
docker compose up -d falkordb
make graph-load-demo GRAPH_NAME=agentbench-demo PARQUET=<path to transactions.parquet>
make bench SUITE_RUN_ROOT=<artifacts/runs/hi-small-v8> PARQUET=<path to transactions.parquet> \
  SUITE_ARGS="--publish-snapshot docs/benchmarks/iteration-2-performance.json"
```

`make bench` builds or reuses the demo graph, so the explicit load is optional. It writes `results.json` (environment, input hashes, protocol, raw samples, resource windows, gates), `summary.md`, and `performance-snapshot.json` under `<run root>/bench/<UTC timestamp>/`. Useful options (pass them in `SUITE_ARGS`): `--workloads model,graph,api,capacity`, `--api-workers 1,4`, `--levels 1,2,4,8,16,32`, `--quiet-wait-seconds`, `--quiet-budget-seconds`, `--level-attempts`, and `--resume <run dir>` to add missing workloads to an interrupted run. `make bench-quick` runs a short smoke pass.
