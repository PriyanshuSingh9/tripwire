# TraceAML Product Requirements

## Product summary

TraceAML is a graph-powered AML detection and investigation workbench for analysts. It replays the synthetic IBM AMLworld HI-Small transactions, scores them with reproducible classical models, retrieves bounded money-flow evidence from FalkorDB, and presents a verified investigation report for human review.

The repository is named Tripwire. TraceAML is the product name used in the interface and project documentation.

## Users and product experience

The primary user is a fraud or AML analyst reviewing a flagged transaction. The core flow is:

1. Select a ranked alert and inspect its model score and decision context.
2. Run a bounded investigation against FalkorDB.
3. Inspect the accounts, transaction records, paths, and counterevidence returned by executed graph queries.
4. Review a structured report whose claims refer to retrieved evidence.
5. Compare the evaluation results and operational measurements on the benchmark page.

The interface has four primary areas: Alerts, Investigations, Benchmarks, and System. Dataset replay must be labelled as a replay of synthetic data, not a live banking integration.

## Iteration 1 scope

### Required

- Inspect the supplied HI-Small files and record their hashes, columns, types, time range, nulls, duplicate transactions, currencies, and label counts.
- Normalize transaction and account records into Parquet while preserving source IDs and source fields needed for audit.
- Create a chronological train, validation, and frozen test split. Build features with information available at the scored transaction time.
- Establish a logistic-regression baseline, a LightGBM tabular model, and LightGBM with past-only graph features on the same split.
- Load selected or full transaction data into FalkorDB using first-class `Transaction` nodes and bounded, parameterized Cypher.
- Provide `trace_funds`, `inspect_counterparties`, and `detect_flow_patterns` investigation tools.
- Expose a typed FastAPI service and an analyst workbench.
- Let one tool-calling LLM select approved investigation tools and draft a structured report. Deterministic validation must verify cited records and claims.
- Report detection quality, fixed alert-budget recall, graph and service latency, throughput, and measured or clearly estimated cost.

### Deferred

GNNs, IBM Multi-GNN, TabGT, LLM fine-tuning, Jev-like decision models, Kafka, Redis, vector databases, Kubernetes, live banking integrations, automated blocking, and regulatory filing are outside the Iteration 1 build unless benchmark evidence and remaining time justify a scoped follow-up.

## Data and evaluation requirements

The expected starting dataset is IBM AMLworld HI-Small: `HI-Small_Trans.csv`, `HI-Small_accounts.csv`, and `HI-Small_Patterns.txt`. The dataset has not been supplied in the repository yet. Do not download or fabricate it as part of setup.

Treat the main transaction `Is Laundering` field as the binary target when confirmed in the supplied version. Pattern metadata is supplementary and missing pattern rows are not negative labels. Keep target labels and typology ground truth out of inference-time graph properties, Cypher, API responses, and investigator tools.

Before fixing the split, inspect positive counts by time interval. Tune features and thresholds on training and validation data only, then evaluate once on the frozen test set. Record split boundaries, feature availability, configuration, and source hashes with each result. Do not compare published scores from different evaluation protocols as if they shared a leaderboard.

Use precision, recall, F1, average precision/PR-AUC, and recall at a stated alert budget. Define dollar-weighted recall explicitly and do not describe repeated transaction amounts as unique recovered funds. Report P50/P95 after warm-up and include hardware, concurrency, graph scope, and cost-estimation method.

The dataset is synthetic. Results demonstrate behavior on the selected benchmark, not real-world AML effectiveness. Preserve CDLA-Sharing-1.0 attribution and licensing information separately from the code license.

## Technical stack

| Area | Choice | Responsibility |
| --- | --- | --- |
| Web | Next.js App Router, React, TypeScript, Tailwind CSS, shadcn/ui | Analyst workbench |
| Graph view | Cytoscape.js | Render query-derived investigation subgraphs |
| API state and charts | TanStack Query, Recharts | Synchronize API state and present recorded benchmark results |
| API | Python, FastAPI, Pydantic | Typed endpoints, scoring orchestration, evidence validation |
| Data | Polars, DuckDB, PyArrow, Parquet | Profiling, transformation, splits, feature tables |
| Models | scikit-learn, LightGBM | Logistic baseline and tabular/graph-feature classifiers |
| Graph database | FalkorDB and its Python client | Authoritative transaction topology and bounded investigations |
| Local services | Docker Compose | FalkorDB development service |
| Tooling | pnpm for web; uv for Python | Dependency and environment management |

Keep Parquet as the analytical source of truth. FalkorDB is authoritative for topology and graph evidence. Do not make model benchmarking depend on millions of individual graph round trips; extract graph features in batches or a reproducible feature job.

## Graph and inference principles

- Model money movement as `(:Account)-[:SENT]->(:Transaction)-[:RECEIVED]->(:Account)`.
- Construct account keys from bank/institution plus account ID when source IDs are not globally unique.
- Preserve sent and received amounts and currencies independently if the data distinguishes them; never compare incompatible currencies as though they were the same unit.
- Use executed, bounded Cypher as the sole source of graph topology shown to users.
- Cap hop depth, time windows, rows, and query duration. Every returned item carries source transaction IDs and timestamps.
- Use only past and current information for a transaction's score. Later evidence must be labelled as subsequent evidence.
- Models emit scores; deterministic policy maps validated inputs to `allow`, `review`, or a clearly simulated `block`.
- Provider failure, invalid output, or unavailable graph evidence must fall back safely to review or deterministic rules. Never fabricate graph results.
- Only an explicit analyst confirmation may promote suspected entities into confirmed case memory. Dismissals preserve transaction history and audit records.

## Team roles and ownership

| Member | Role | Primary ownership | Collaboration boundary |
| --- | --- | --- | --- |
| Kanishka | Frontend and product UI | `apps/web/`, analyst information architecture, graph interaction, benchmark presentation | Consumes shared API contracts; does not invent evidence or scoring |
| Ashwani | Graph and data engineering | `graph/`, graph ingestion/query code, FalkorDB schema, indexes, structural feature extraction | Owns query execution and topology; coordinates feature definitions with the evaluation lead |
| Parth | Backend and investigation integration | `services/api/`, typed endpoints, replay orchestration, deterministic policy, audit persistence, tool/report validation | Integrates graph and model modules through shared contracts |
| User | Fraud intelligence and evaluation lead | `ml/`, split protocol, baselines, graph-feature design, model evaluation, latency/cost methodology, fraud and performance claims | Co-designs graph features with Ashwani; reviews benchmark and product claims |

The user is the project orchestration lead for this setup: coordinate shared interfaces, review worker output, and resolve scope and integration conflicts. Ownership identifies the primary maintainer; it does not prevent cross-review.

## Acceptance criteria

- A clean local setup starts the web app, API, and FalkorDB service from documented commands.
- Data preparation records exact input hashes and produces a leakage-safe split manifest once the dataset is provided.
- All three model configurations use the same frozen test cohort and report reproducible metrics.
- FalkorDB investigations return bounded, source-backed paths and counterparties.
- Investigation reports cite only retrieved records; the verifier rejects unsupported transaction IDs, paths, amounts, or timestamps.
- The UI exposes the alert queue, graph investigation, evidence/report, and benchmark results without presenting synthetic data as real-world proof.
- README documents setup, data placement and license, graph schema, API entry points, and benchmark reproduction.

## Current setup state

The repository scaffold is being established before dataset arrival. Raw data, processed data, credentials, and generated model artifacts remain outside version control.
