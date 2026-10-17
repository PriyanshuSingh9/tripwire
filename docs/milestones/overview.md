# TraceAML Delivery Gates

This document tracks Iteration 1 outcomes without retaining the previous dated hackathon plans. The full product scope and acceptance criteria are in [docs/prd.md](../prd.md).

## Current state

The web app, uv-managed API and ML projects, and local FalkorDB service are set up. HI-Small Kaggle version 8 is present locally and ignored by Git. Its aggregate source profile records 5,078,345 transactions, 5,177 positive labels, 9 exact duplicate rows, a 2022-09-01 through 2022-09-18 timestamp range, and per-currency amount ranges. Normalized transaction Parquet and a chronological split manifest are generated. The frozen boundaries are train through 2022-09-07 14:55, validation through 2022-09-09 03:16, then test. Past-only transaction and account-history features plus logistic regression and LightGBM baselines reproduce bitwise across clean runs. On the 761,639-row test split (0.205% positive prevalence), LightGBM average precision is 0.1587; at its validation-selected threshold precision is 23.79%, recall is 24.79%, and F1 is 0.2428. At a fixed 0.1% alert budget it detects 248 positives (15.89% recall). The daily report shows 0.119% positive prevalence across September 9–10, then 59.12% across September 11–18, when only 1,108 test transactions remain. See the [performance report](../benchmarks/iteration-1-performance.md); the data is synthetic and does not establish real-world AML effectiveness.

FalkorDB ingestion and the three bounded query tools are implemented. A 500,000-row chronological prefix in `traceaml-capacity` reconciles 500,000 transactions and each edge direction; FalkorDB used about 706 MiB and loading took 31.3 seconds. A separate 1,000-row smoke graph was reloaded idempotently and exercised through all three tools. The full graph was not loaded: a simple linear estimate is about 7 GiB, exceeding available memory with safe headroom on this host. Artifacts and graph data remain local.

The FastAPI service now serves the target-free replay alert queue, offline benchmark metrics, and bounded graph investigations. It uses a deterministic threshold policy and evidence verifier. The score artifact contains 761,639 held-out transactions and only transaction ID, timestamp, and score. Endpoint tests cover label exclusion, ranked alerts, validation, evidence grounding, and optional graph-ablation metrics. A target-free two-day demo slice around the highest-scored held-out case contains 998,021 transactions and 354,660 accounts; a live API request for that case returned 25 evidence transactions and 75 verified paths with no target field. This slice supports a working demonstration but is not a full-cohort graph. The alert, investigation, and benchmark views are wired to API responses, and Next.js proxies same-origin `/api` requests to the configured API origin.

The point-in-time graph feature builder processed all 5,078,345 transactions into five target-free features in 1,563 seconds. The same-cohort comparison did not improve detection: graph LightGBM average precision was 0.1521 versus 0.1587, and 0.1% alert-budget recall was 14.73% versus 15.89%. On the two-day investigation graph, 12 serial API runs measured 3.49 seconds P50 / 3.58 seconds P95. The fan-out, fan-in, and pass-through queries dominate this time. Raw LightGBM scored the held-out cohort at 152,214 transactions per second with one thread. Full measurements and limits are in [the performance report](../benchmarks/iteration-1-performance.md). Cost per decision and concurrent capacity remain unmeasured.

## Shared delivery gates

### Repository foundation

- [x] Next.js analyst workbench scaffold is installable with pnpm.
- [x] FastAPI and ML Python packages are reproducible with uv.
- [x] Docker Compose starts local FalkorDB.
- [x] Contracts, schema, environment examples, and ignore rules agree.

### Data and evaluation

- [x] Verify HI-Small version 8, license, schema, and source hashes in the local profile and split manifest.
- [x] Resolve account IDs using bank plus account strings; preserve leading zeroes and the duplicate source account columns by position.
- [x] Generate past-only behavioral features and validate row/split/hash reconciliation over the frozen chronological split.
- [x] Complete the full point-in-time feature extraction and compare LightGBM with and without graph features on the frozen test cohort.

### Graph and API

- [x] Ingest a bounded chronological prefix idempotently into first-class `Transaction` nodes and reconcile transaction/edge counts.
- [x] Implement and live-smoke-test bounded `trace_funds`, `inspect_counterparties`, and `detect_flow_patterns`.
- [x] Load and benchmark the complete 5,078,345-transaction graph (Iteration 2: 520.9 s load, 2,514 MB graph memory, 23.2 ms P50 case investigation; see [graph performance](../benchmarks/iteration-2-graph-performance.md)).
- [x] Serve validated scoring and investigation contracts through FastAPI.
- [x] Serve a ranked target-free replay queue, benchmark artifacts, deterministic decisions, and verified graph-investigation responses.
- [x] Load a target-free demo slice containing a held-out replay case and verify a live API investigation against FalkorDB.
- [x] Preserve deterministic policy and human-confirmed case-memory provenance (append-only SQLite case history; graph-side case links remain deferred).

### Analyst workbench and evidence

- [x] Present alerts, investigation paths, verified report, and benchmark measurements from API response contracts.
- [x] Confirm graph evidence is API output from actual FalkorDB Cypher.
- [x] Verify report IDs, paths, amounts, and timestamps against retrieved records.
- [x] Label synthetic replay clearly and avoid claims of real-world effectiveness.

### Release evidence

- [x] Document clean setup and data placement without committing data or credentials.
- [x] Record metric definitions, split, feature set, hardware, serial workload protocol, latency, throughput, and costs that remain unpriced.
- [x] Provide graph schema and queries and reproducible benchmark instructions (`make bench`, `make graph-bench`; see the Iteration 2 reports).
- [ ] Provide demo materials (video/walkthrough).
- [ ] Verify the Gemini report agent live (implemented and unit-tested, ADR-020) and measure its latency and cost with a real key.

## Ownership

See [team ownership](README.md) and the individual member charters. Shared schema changes are reviewed by the user as integration lead and by the relevant subsystem owner.
