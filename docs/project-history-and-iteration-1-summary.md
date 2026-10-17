# TraceAML project context and Iteration 1 summary

This document consolidates the decisions made during project planning, the work present in the repository, the measured results, and the remaining delivery gates. It is a project handoff for the team. The architecture and contracts remain authoritative in [architecture](architecture.md), [decisions](decisions.md), and [contracts](contracts.md); this summary links those decisions to their context and implementation.

## Project in one paragraph

TraceAML is a local-first analyst workbench for replaying synthetic IBM AMLworld HI-Small transactions. It ranks transactions with reproducible classical models, uses FalkorDB to retrieve bounded transaction-flow evidence, validates investigation reports against queried records, and presents alerts, investigations, and benchmark measurements. The repository is named Tripwire. This is a research and demonstration system on synthetic data; it is not connected to a bank, does not freeze funds or file regulatory reports, and does not establish real-world AML effectiveness.

## How the project direction was chosen

The initial Iteration 1 proposal called for a measurable AML pipeline, a transaction graph, graph-enhanced detection, an evidence-grounded investigator, and an analyst workbench. It also proposed leaving advanced graph learning and model training research for a later iteration, guided by benchmark results.

The public-work review identified [Tracer](research/tracer-aml-reference.md) as the closest product precedent and highlighted reusable ideas from IBM's Multi-GNN work, graph-feature implementations, and evidence-verification research. The decision was to learn from public workflow and evaluation patterns without treating published scores as comparable results. In particular, Tracer's account-level split and full-period aggregates do not answer what was knowable at a transaction's scoring time. TraceAML therefore uses a chronological, point-in-time protocol and a FalkorDB-centered investigation path. No Tracer source code was copied or vendored.

The final Iteration 1 target was narrowed to a working, measurable system rather than a collection of advanced models:

- A reproducible logistic-regression and LightGBM baseline, followed by a same-cohort LightGBM graph-feature ablation.
- A real FalkorDB graph with bounded, source-backed investigation queries.
- A deterministic API and evidence verifier that keep target labels out of inference.
- A connected analyst workbench for alerts, investigations, benchmarks, and service status.
- Recorded detection, latency, throughput, and cost limitations, with a clean setup path.

The user then provided the KaggleHub download approach and explicitly requested HI-Small. The repository pins Kaggle dataset version 8 and documents the three expected files. Later, the user requested that the Tracer repository be cloned and inspected. The implementation reference was reviewed, but the architecture decision was to borrow workflow ideas and not vendor the repository. The user also asked for checkpoint commits, asked for an overlap review against member responsibilities, and directed the work away from visual polish toward latency and other benchmark measurements. Those instructions shaped the current checkpoints and the benchmark report.

## Decisions now reflected in the implementation

### Stack and repository boundaries

- `apps/web/`: Next.js App Router, TypeScript, Tailwind CSS, shadcn/ui, TanStack Query, Recharts, and Cytoscape.js.
- `services/api/`: FastAPI and strict Pydantic contracts for health, alert, benchmark, and investigation endpoints.
- `ml/`: Python data preparation, features, model training, evaluation, and score publication, managed with uv.
- `graph/`: FalkorDB schema, indexes, ingestion, bounded Cypher investigations, and point-in-time graph feature extraction.
- `compose.yaml`: local FalkorDB service. pnpm manages the web application; uv manages Python projects.

Kafka, Redis, vector search, Kubernetes, GNNs, fine-tuned LLMs, Jev-like decision models, and multi-agent orchestration were deferred. Iteration 1 did not need them to establish the core benchmark and investigation workflow.

### Data, identity, and evaluation

The selected input is synthetic IBM AMLworld HI-Small Kaggle version 8. Dataset files and generated data/model artifacts remain local and Git-ignored. The data has a separate CDLA-Sharing-1.0 license; the repository's code license does not automatically cover it.

The inspected source contains 5,078,345 transactions, 5,177 positive labels, and 9 exact duplicate rows, with timestamps from September 1 through September 18, 2022. Exact duplicate source rows are retained as separate records. Transaction IDs derive from source row order, and account keys combine bank and account values to avoid collisions. Amounts remain paired with their source currencies. Pattern metadata and labels are offline evaluation data only.

The split is chronological and assigns complete same-minute groups together. The frozen train cutoff is September 7, 2022 at 14:55; validation ends September 9 at 03:16; the remainder is test. Features for a transaction use only strictly earlier timestamps. The test cohort is frozen, threshold selection uses validation, and target labels are excluded from graph ingestion, feature generation, replay scores, and inference responses.

The experiment ladder implemented is logistic regression, LightGBM with transaction and past-only behavioral features, and LightGBM with the same features plus five point-in-time graph features. All compare against the same held-out cohort. Simple rules appeared in the earliest proposal but were not part of the final implemented benchmark ladder.

### Graph and investigation

FalkorDB is the authority for graph topology and structural investigation evidence. Transfers use first-class transaction nodes:

```text
(:Account)-[:SENT]->(:Transaction)-[:RECEIVED]->(:Account)
```

The loader uses stable row-derived IDs, bank-aware account keys, indexes, idempotent batches, and an inference-safe property allowlist. The bounded tools are `trace_funds`, `inspect_counterparties`, and `detect_flow_patterns`. Query output includes source transaction identifiers and the available timestamps, amounts/currencies, account endpoints, paths, and duration. The UI receives topology from API results generated by executed Cypher; it does not invent graph facts.

The offline graph-feature builder uses a separate compact feature graph. It processes complete minute groups before updating state and emits recent distinct counterparties for sender/receiver in/out activity plus a prior reverse-flow flag. It does not read labels, split assignment, or pattern metadata.

### API, policy, report, and UI

The API reads a target-free replay score artifact and supports `/api/health`, `/api/alerts`, `/api/benchmarks`, and `/api/investigations`. Deterministic policy maps scores to `allow` or `review`; a simulated `block` is an architectural option, not a live financial action. Investigation orchestration calls the approved FalkorDB tools. The verifier ensures report transaction IDs and paths are present in retrieved evidence and checks sourced values before they are presented as evidence.

The current report is deterministic. An optional tool-calling LLM was part of the proposed eventual product design but is not connected. No external LLM provider was called for the reported benchmark. Case persistence, analyst confirmation/dismissal history, and provenance-backed confirmed case memory are not yet implemented.

The Next.js workbench has Alerts, Investigations, Benchmarks, and System routes; typed API calls; same-origin `/api` proxying; and API-backed evidence graph rendering. It labels the content as a synthetic replay. At the user's direction, the latest work prioritized functional integration and measurement over visual review.

## What is implemented

### Data and models

- HI-Small v8 aggregate profile, source hashes, normalized Parquet, and frozen chronological split manifest.
- Bank-aware account keys, retained source duplicates, and source-row-derived transaction IDs.
- Past-only behavioral features and automated checks for target exclusion and same-minute/future leakage.
- Logistic regression and LightGBM baseline models, validation-selected threshold, target-free held-out score artifact, and machine-readable metrics.
- Deterministic clean-run reproduction: baseline metrics, test scores, and model artifacts matched byte for byte in the recorded rerun.
- Complete point-in-time graph feature extraction for all 5,078,345 transactions and a same-cohort graph-feature ablation.

### Graph, API, and product integration

- FalkorDB schema/index setup, idempotent batched transaction graph ingestion, and three bounded investigation tools.
- Transaction/edge reconciliation on a 500,000-row capacity sample and a separate target-free demo graph centered on a held-out case.
- FastAPI endpoints for health, ranked alerts, benchmark results, and evidence-backed investigations.
- Deterministic policy, target-free replay score loading, bounded query orchestration, and report evidence validation.
- Next.js workbench connected to the API for alert, investigation, benchmark, and system information.
- A recorded performance snapshot exposed through the benchmark API and workbench.

### Verification recorded by the team

The benchmark report records 17 passing ML tests, 46 passing graph tests, and 21 passing API tests, including leakage, target exclusion, graph timing, evidence grounding, and performance contract checks. Web lint and production build passed. The service API Ruff checks passed. A live local API investigation against the demo graph returned a validated response for a held-out replay transaction. These are the recorded checks for the current checkpoint; consult the report and milestone files for scope and limitations.

## Benchmark results and interpretation

The full methodology, hardware, workload, exact measurements, and limitations are in [the Iteration 1 performance report](benchmarks/iteration-1-performance.md), with a machine-readable snapshot in [JSON](benchmarks/iteration-1-performance.json).

### Detection

| Experiment | Average precision | Precision / recall / F1 at validation threshold | Recall at 0.1% test alert budget |
| --- | ---: | ---: | ---: |
| Logistic regression | 0.0146 | See run metrics | 0% |
| LightGBM baseline | 0.1587 | 23.79% / 24.79% / 0.2428 | 15.89% (248 / 1,561 positives) |
| LightGBM + graph features | 0.1521 | 15.04% / 24.73% / 0.1871 | 14.73% (230 / 1,561 positives) |

The graph feature set did not improve this test benchmark. Its average precision and recall at the fixed alert budget were both lower than the tabular LightGBM baseline. FalkorDB remains a core product component because it supplies relationship-based investigation evidence; the current result does not support a claim that these graph features improve classifier quality.

### Data caveat

Train, validation, and test positive prevalence is approximately 0.0803%, 0.0998%, and 0.2050%. The test tail is unusual: September 9–10 contains 760,531 test rows with 906 positives, while September 11–18 contains 1,108 rows with 655 positives. The tail is small by transaction count but accounts for about 42% of test positives. Preserve the frozen split and report this shift alongside aggregate metrics. It is a property of this synthetic sample, not an estimate of banking prevalence.

### Runtime measurements

- Raw LightGBM inference on one already prepared row: 0.029 ms P50 / 0.041 ms P95. Feature retrieval and encoding are excluded.
- Full 761,639-row held-out scoring: 5.004 seconds median, or 152,214 transactions/second, one CPU thread. Predictions matched the frozen score artifact exactly.
- Serial loopback `GET /api/alerts?limit=50`: 1.087 ms P50 / 1.720 ms P95. `GET /api/benchmarks`: 1.082 ms / 1.954 ms.
- Direct graph queries: `trace_funds` 5.46/6.65 ms P50/P95; `inspect_counterparties` 1.19/1.64 ms. The three selected flow-pattern scans range from about 0.9 to 1.8 seconds and dominate the end-to-end investigation.
- Full serial investigation request: 3.487 seconds P50 / 3.582 seconds P95; about 0.286 requests/second for the measured case and query bounds.
- The investigation timings use a two-day demo graph of 998,021 transactions, not the full dataset graph. They are serial local measurements, not concurrent capacity results.
- A 500,000-transaction capacity sample used about 706 MiB and loaded in 31.3 seconds. A roughly 7 GiB full-graph estimate is a linear projection, not a measured full load. The full transaction evidence graph was not loaded on the current host.
- No external LLM costs were incurred. Cost per 1,000 scores, cost per investigation, concurrent capacity, and dollar-weighted recall remain unmeasured or unpriced. Dollar-weighted recall is deferred because amounts may use different currencies and no valuation basis was selected.

## Team ownership and handoff

The current role charters are indexed in [the milestones directory](milestones/README.md). Primary ownership remains:

| Member | Owns | Current handoff context |
| --- | --- | --- |
| Kanishka | `apps/web/`, analyst information architecture, Cytoscape interaction, benchmark presentation | Core routes are API-connected. Visual review and analyst review remain. |
| Ashwani | `graph/`, FalkorDB schema, ingestion, Cypher, structural feature extraction | Bounded tools and full feature sidecar are implemented. Review the integration changes and decide next query optimization. |
| Parth | `services/api/`, Pydantic contracts, replay orchestration, deterministic policy, investigation integration and persistence | API routes and evidence validation are implemented. Case review persistence and optional LLM integration remain open. |
| Priyanshu | `ml/`, data protocol, baselines, graph feature design with Ashwani, metrics and performance/fraud claims; integration lead | Frozen protocol, models, ablation, and initial measurements are recorded. Own interpretation and next experiment selection. |

The integration checkpoint crossed the primary path boundaries: shared work added or connected code under `graph/`, `services/api/`, and `apps/web/` as well as evaluation work. This was recorded in Priyanshu's charter. It does not transfer ownership; subsystem owners should review and take over extensions in their areas. Shared API/data shapes remain in `docs/contracts.md`.

## Repository checkpoints

The current `main` and `origin/main` both point to `695d13b` (`feat(workbench): expose measured runtime results`). The user reported that pushing to `origin/main` succeeded. The recent implementation sequence is:

| Commit | Checkpoint |
| --- | --- |
| `779999f` | Scaffold pnpm/uv workspace and align project docs |
| `96e4f12` | Stabilize HI-Small data and evaluation pipeline |
| `408fd2f` | Add bounded FalkorDB ingestion and queries |
| `183c2f4` | Add target-free API replay investigations |
| `564c040` | Add point-in-time FalkorDB feature ablation |
| `337a1cb` | Connect analyst workbench to the API |
| `0b437d7` | Include transfer endpoints in trace evidence |
| `d25a44f` | Sync member milestones with integration status |
| `4921c81` | Record Iteration 1 latency results |
| `695d13b` | Expose measured runtime results in the workbench |

The repository documents local setup and reproducible data/model commands in the [README](../README.md). Raw data and generated model artifacts remain excluded from commits.

## Remaining work and Iteration 2 choices

Iteration 1 has an integrated replay and investigation path with baseline results, a graph ablation, and a first serial performance report. It is not complete as a public submission package. The shared delivery gates still list these open items:

- Load and benchmark the full 5,078,345-transaction evidence graph on hardware with adequate memory, or keep the limited graph scope explicit in any demo claim.
- Implement and verify analyst case confirmation/dismissal persistence with provenance, if this remains in the accepted product scope.
- Provide final graph/schema and query material, reproducible benchmark instructions, and demo materials/video.
- Complete visual and analyst review if needed for the final demonstration; this was intentionally not the latest focus.
- Measure concurrent capacity and choose whether actual infrastructure cost can be calculated. Keep external LLM costs out of claims until a provider is connected and measured.

The next model or infrastructure work should follow evidence. The current graph feature ablation did not improve detection, while pattern queries dominate investigation latency. A sensible next step is to inspect and optimize bounded pattern-query plans and cache/feature availability before expanding the model ladder. Any GNN, graph transformer, constrained decision model, or fine-tuning experiment should be a separately named protocol on the same frozen cohort, with a reason tied to a measured gap.

## Iteration 2 summary (latency and benchmark)

Iteration 2 focused on latency, scale, and benchmark quality; visual work was deferred. All results are on synthetic AMLworld HI-Small v8 data on a local Intel Core i5-13400F host. The Iteration 1 report names a different CPU, so Iteration 1 figures are quoted as published and same-machine reruns are given where available.

| Area | Iteration 1 | Iteration 2 | Report |
| --- | ---: | ---: | --- |
| `POST /api/investigations`, serial P50 | 3,487 ms | 11.0 ms (P95 13.7 ms) | [performance](benchmarks/iteration-2-performance.md) |
| Investigation throughput | 0.286 req/s serial | 87.5 req/s serial; 244 req/s at 16 clients, 4 workers, 0 errors | [performance](benchmarks/iteration-2-performance.md) |
| Flow-pattern queries, P50 | 891-1,728 ms | 1.0-1.3 ms | [graph](benchmarks/iteration-2-graph-performance.md) |
| Full 5,078,345-transaction graph | not loaded (7 GiB projected) | loaded in 520.9 s, 2,514 MB graph memory, 23.2 ms case investigation | [graph](benchmarks/iteration-2-graph-performance.md) |
| Graph feature sidecar build | 1,563 s | 14.3 s, row-for-row identical | ADR-015 |
| Test average precision | 0.1587 | 0.7525 (Sept 9-10 bulk 0.5996 vs 0.0877) | [detection](benchmarks/iteration-2-detection.md) |
| Recall at 0.1% alert budget | 15.89% | 47.73% | [detection](benchmarks/iteration-2-detection.md) |
| Cost per investigation | unpriced | about $1.3e-7 metered CPU (estimate) | [performance](benchmarks/iteration-2-performance.md) |

Root causes and changes:

- **Investigation latency:** account queries started from a whole-window `timestamp_epoch` range scan instead of the indexed account; pass-through built a full in-by-out cross product. Queries now anchor on indexed keys, and the API runs tools concurrently on pooled connections (ADR-016, ADR-017).
- **Correctness:** `UNION ... ORDER BY ... LIMIT` bound only the last branch, so pattern windows ended at an arbitrary epoch and trace limits applied to one branch only. Iteration 1 fan-out evidence for the benchmark case was affected (2 vs 7 counterparties).
- **API:** a parallel rewrite merged in PR #1 (hardcoded alerts, mismatched graph properties, invalid `pyproject.toml`) was replaced by the restored single entrypoint `api.app:app`; its case module was kept and rebuilt with append-only provenance.
- **Detection:** Iteration 1 settings overfit (train AP up to 0.99), so the earlier graph-feature ablation result was single-run variance. Regularized LightGBM with 76 strictly past-only features in seven families reaches the figures above. Candidates were pre-declared before test evaluation and every test look is ledgered (ADR-018). Truncation and perturbation audits on real data found no leakage. The Sept 11-18 tail is near-separable and inflates full-cohort budget recall; the Sept 9-10 bulk figures are the conservative comparison.
- **Infrastructure:** the Compose volume now mounts FalkorDB's real data path, so graphs survive container recreation; the FalkorDB browser moved to host port 3001.

Open items: demo materials, the optional LLM report drafter, graph-side case links, hub-account latency (up to 207 ms P95 at full scale), a clean-host re-measurement of the model workload, and switching the API replay to the Iteration 2 score artifact.

## Key documents

- [Product requirements and ownership](prd.md)
- [Architecture](architecture.md)
- [Shared API and graph contracts](contracts.md)
- [Architecture decisions](decisions.md)
- [Delivery gates and current status](milestones/overview.md)
- [Member role charters](milestones/README.md)
- [Tracer implementation reference](research/tracer-aml-reference.md)
- [Iteration 1 performance report](benchmarks/iteration-1-performance.md)
