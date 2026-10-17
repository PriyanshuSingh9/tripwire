# TraceAML Contracts

This file records shared API and graph contracts. Pydantic models and executable Cypher are the implementation authority; update this document when either changes.

## API conventions

- Base path: `/api`
- JSON requests and responses use validated Pydantic models.
- Transaction IDs and account keys are source-backed identifiers.
- Target labels and typology truth never appear in inference responses.
- Errors use standard HTTP status codes and a JSON `detail` value. Graph-dependent operations return `503` when FalkorDB is unavailable.

## Implemented endpoints

### `GET /api/health`

Returns service readiness and dependency status.

```json
{
  "status": "ok",
  "falkordb": "available",
  "version": "0.1.0",
  "llm": "not_configured"
}
```

Allowed status values for `falkordb` are `available`, `unavailable`, and `not_configured`. `llm` is `configured` when a provider and key are set and `not_configured` otherwise; the check makes no provider request. Health output contains no credentials or dataset labels.

### `GET /api/alerts`

Accepts `limit` from 1-200 and a non-negative `offset`. Returns `total`, `limit`, `offset`, and items containing `transaction_id`, `timestamp`, `score`, and deterministic `decision` (`allow` or `review`). Items sort by score descending, then transaction ID ascending. Decisions use the validation-selected LightGBM threshold. The API reads only the target-free `inference_scores.parquet` artifact.

### `GET /api/benchmarks`

Returns the `logreg` and `lgbm` sections from the offline `baseline_metrics.json` artifact under a `models` key. When `TRACEAML_GRAPH_METRICS_PATH` points to an existing graph-ablation run, its model entries (including `lgbm_graph`) are merged into the response. The `performance` object is loaded from `TRACEAML_PERFORMANCE_PATH` and contains the recorded model, API, and FalkorDB latency snapshot plus explicit cost status. It reports measured synthetic benchmark results; it does not expose test labels or individual test outcomes.

### `POST /api/investigations`

Request:

```json
{
  "transaction_id": "source-transaction-id",
  "max_hops": 2,
  "time_window_seconds": 86400,
  "result_limit": 50,
  "use_llm": false,
  "act": false
}
```

`use_llm` and `act` are optional opt-ins (see below). `max_hops` is bounded to 1–3. `result_limit` is bounded to 1–100. The server enforces a query timeout independently of these request values.

Response shape:

```json
{
  "investigation_id": "inv_...",
  "transaction_id": "source-transaction-id",
  "risk_score": 0.0,
  "decision": "review",
  "policy_version": "v1",
  "evidence": {
    "transactions": [],
    "accounts": [],
    "paths": [],
    "query_duration_ms": 0.0,
    "tool_durations_ms": [
      { "tool": "trace_funds", "query_duration_ms": 0.0 },
      { "tool": "inspect_counterparties", "query_duration_ms": 0.0 },
      { "tool": "detect_flow_patterns.fan_out", "query_duration_ms": 0.0 },
      { "tool": "detect_flow_patterns.fan_in", "query_duration_ms": 0.0 },
      { "tool": "detect_flow_patterns.pass_through", "query_duration_ms": 0.0 }
    ]
  },
  "report": {
    "hypothesis": "...",
    "supporting_transaction_ids": [],
    "counterevidence_ids": [],
    "recommendation": "review",
    "validation": "validated"
  },
  "created_at": "2026-10-15T00:00:00Z"
}
```

The score is loaded from the target-free replay artifact. Deterministic policy compares it with the validation-selected threshold. The current report is a deterministic summary; it does not call an LLM. Evidence arrays contain only records returned by executed queries. The API calls `trace_funds`, `inspect_counterparties`, and bounded `detect_flow_patterns` variants concurrently over pooled FalkorDB connections: the first two start together, and the three pattern variants start as soon as either has returned the anchor transfer's account keys. Evidence is merged in a fixed order, so results match serial execution.

Duration semantics (changed in Iteration 2): `query_duration_ms` is the wall-clock time of the whole graph evidence phase, which is what the analyst waits for. Before Iteration 2 it was the sum of the serial tool durations. `tool_durations_ms` lists each executed tool's own measured duration in the fixed order above; pattern entries are absent when the anchor transfer was not returned. Under concurrency the per-tool sum can exceed the wall time.

Errors: a transaction absent from the score artifact returns 404 `transaction not scored`; a scored transaction absent from the graph returns 404 `transaction not found in graph`; an unconfigured or unreachable graph returns 503 `graph unavailable`; a missing score or threshold artifact returns 503; a query exceeding its timeout returns 504 `graph query timed out`; malformed graph evidence returns 500 `graph evidence unavailable`, and a report citing unreturned records returns 500 `evidence validation failed`; invalid request bounds return 422. Error bodies carry only these fixed `detail` strings, never hosts, ports, paths, or exception text. Each graph query uses the server-side timeout `TRACEAML_GRAPH_QUERY_TIMEOUT_MS` (default 5000).

### LLM report agent (optional)

`POST /api/investigations` accepts an optional boolean `use_llm` (default `false`). The default path is unchanged: deterministic, fast, and free. With `use_llm: true` and a configured provider (Gemini), a tool-calling agent may select among `trace_funds` and `detect_flow_patterns` (fan_out, fan_in, pass_through) and draft the report. The anchor transaction, time window, result limit, and hop ceiling come from the request; the model supplies only `max_hops` (clamped to the request) or a pattern name. The anchor transfer and its immediate counterparties are always retrieved first with `inspect_counterparties`. Tool calls, model rounds, and wall time are bounded (`LLM_MAX_TOOL_CALLS`, `LLM_MAX_ROUNDS`, `LLM_TIMEOUT_SECONDS`).

The model returns a draft with a hypothesis, supporting and counter-evidence transaction IDs, `cited_claims`, and `cited_paths`. Deterministic code accepts it only if every cited ID exists in the retrieved evidence, every claim's timestamp, amount, currency, and account keys equal the evidenced record, every cited path equals a path a query returned, and identifiers and amount-sized numbers written in the hypothesis come from the evidence. The model never sets `risk_score`, `decision`, or `recommendation`; the recommendation is fixed to `review` by code.

Responses add these optional report fields: `source` (`deterministic` or `llm`), `cited_claims`, `cited_paths`, and `llm`. `llm` records `provider`, `model`, `status` (`completed`, `fallback`, `unavailable`), `fallback_reason`, `rounds`, `tool_calls`, token counts, `duration_ms`, and `estimated_cost_usd` (present only when both per-million-token prices are configured; no price is assumed). `fallback_reason` is one of `not_configured`, `provider_error`, `timeout`, `invalid_output`, `verification_failed`. Any of these returns HTTP 200 with the deterministic report; none produces a raw provider message. FalkorDB failures are not masked and return the same 503/504 as the deterministic path. Prompts, tool results, and keys are never returned or logged. When `use_llm` is true, retrieved synthetic evidence is sent to the provider; the default path sends nothing.

### Typed outcome, tool trace and MCP boundary

Every investigation response adds three fields. `outcome` is one of `evidence_found` (a query returned a path or pattern), `no_matching_path` (the graph was queried successfully and nothing matched within the declared bounds; this is never evidence of safety and never maps to `allow`), `unknown_anchor`, or `inconclusive` (a result hit its cap, or a query did not complete). `unknown_anchor` and unresolved graph failures keep their existing HTTP errors (404, 503, 504), so a response with those outcomes is never returned; the values exist so the MCP tools, the trace, and the UI share one vocabulary. Without paths, any capped tool result makes the outcome `inconclusive` instead of `no_matching_path`.

`tool_trace` lists each graph tool call: `tool_run_id`, `tool` (`trace_funds`, `inspect_counterparties`, `detect_flow_patterns`), `transport` (`direct` or `mcp`), `origin` (`policy` for the deterministic context and fixed calls, `model` for calls the agent selected), `arguments` (bounded scalars; the flow pattern is `flow_pattern`), `outcome` (the outcome values plus `invalid_input`), `status`, `duration_ms`, `result_count`, `path_count`, and `truncated`. It records observable tool facts only, never model reasoning.

The LLM agent (`use_llm: true`) reaches the graph tools through an MCP server (`api.mcp_graph`) and an in-process MCP client session when `TRACEAML_AGENT_TOOL_TRANSPORT=mcp` (the default). The server exposes exactly `trace_funds`, `inspect_counterparties`, and `detect_flow_patterns`, delegates to the existing parameterized queries in `graph.queries`, and rejects out-of-range arguments before any database call. `TRACEAML_AGENT_TOOL_TRANSPORT=direct` calls the same functions without the protocol. The deterministic path always uses direct calls. `python -m api.mcp_graph` serves the same tools over stdio for external MCP clients.

### Controlled case action

`POST /api/investigations` accepts an optional boolean `act` (default `false`). When set, the response adds `action` after a deterministic gate. The proposal comes from the agent (`proposed_action` and `action_reason` on an LLM report) or, for a deterministic report, from policy: `open_case` for a `review` decision with `evidence_found`, `enrich_case` when an open case already exists. The gate allows a proposal only when: `outcome` is `evidence_found`; opening requires the policy decision `review`; evidence references are present and each exists in the evidence returned by the tool runs of this investigation; and the case state allows it (`case_already_active`, `no_open_case`, and `duplicate_enrichment` are denials). `action` carries `proposed_action`, `proposed_by` (`agent` or `policy`), `reason`, `status` (`executed`, `denied`, `no_action`), `denial_code`, `case_id`, `evidence_refs`, and `tool_run_ids`. The action writes only to the local case store. It cannot change `risk_score` or `decision`, writes nothing to FalkorDB, and does not block, freeze, or move money.

### Investigation record fields

`TransactionEvidence` contains `transaction_id`, `timestamp`, `source_account_key`, `destination_account_key`, sent/received amount and currency when present, and payment type when present. `GraphPath` contains ordered account and transaction references. `InvestigationReport` contains a hypothesis, supporting transaction IDs, counterevidence IDs, a human-review recommendation, and validation status. The report cannot supply risk probabilities or references outside the retrieved evidence set.

## Graph contract

Graph name: `traceaml`.

| Label | Required properties | Notes |
| --- | --- | --- |
| `Account` | `account_key` | Stable key; include bank/institution when necessary to disambiguate IDs |
| `Transaction` | `transaction_id`, `timestamp` | Also store source-supported amount, currency, and payment type properties |

Relationships:

- `(:Account)-[:SENT]->(:Transaction)` identifies the sender.
- `(:Transaction)-[:RECEIVED]->(:Account)` identifies the receiver.
- Each transaction has exactly one sender and one receiver in the normalized graph.

The inference graph excludes laundering labels and typology ground truth. Case memory additions require explicit analyst action and must include provenance.

Create property indexes before applying unique node constraints. FalkorDB unique constraints are created through the supported client constraint API after those indexes exist; do not use unsupported Neo4j-only constraint syntax in Cypher files.

## Case review contract

Cases persist in SQLite at `TRACEAML_CASE_DB_PATH` (default `data/cases/cases.db`, relative to the repository root; ignored by Git). The review history is append-only: database triggers reject updates and deletes of history rows and deletes of cases.

`GET /api/cases?status=&limit=50&offset=0` returns `items`, `total`, `limit` (1-100), and `offset`, most recently updated first. `status` filters by `suspected`, `under_review`, `confirmed`, or `dismissed`.

`POST /api/cases` with `{"transaction_id": "...", "opened_by": "analyst-id", "notes": null}` opens a `suspected` case and returns it with status 201. The transaction must exist in the target-free score artifact (404 `transaction not scored` otherwise); its risk score is copied from the artifact. A transaction may have at most one `suspected`, `under_review`, or `confirmed` case (409 otherwise); a dismissed case does not block a new one. There is no seed data.

`GET /api/cases/{case_id}` returns one case (404 `case not found`).

`POST /api/cases/{case_id}/review` accepts `{"action": "confirm" | "dismiss", "reviewer_id": "...", "pattern_type": null, "notes": null}`. Only explicit analyst action changes status: `confirm` moves the case to `confirmed`, and `dismiss` moves it to `dismissed`. Both are terminal; reviewing a confirmed or dismissed case returns 409 and never overwrites history. A dismissal preserves the transaction history and audit record and does not erase graph data. Nothing is written to the graph yet; provenance-backed case or ring links in the graph remain future work.

Case response fields: `case_id`, `transaction_id`, `status`, `risk_score`, `opened_by`, `created_at`, `updated_at`, `history`, and `evidence`. Each `evidence` entry (append-only, from an agent or policy action) has `evidence_id`, `review_id`, `action` (`open_case` or `enrich_case`), `proposed_by`, `investigation_id`, `reason`, `evidence_refs`, `tool_run_ids`, `tool_trace`, the `risk_score`, `decision`, and `policy_version` snapshot, and `timestamp`. It stores identifier references and the tool trace, never transaction records or labels. Cases opened by an action have `opened_by` of `traceaml-agent` or `traceaml-policy` and stay `suspected`; only explicit analyst review confirms or dismisses them. Each history entry has `review_id`, `action` (`open`, `enrich`, `confirm`, or `dismiss`), `reviewer_id`, `pattern_type`, `notes`, and `timestamp`, oldest first. `under_review` is accepted as a filter value but no current transition produces it.

## Approved graph tools

| Tool | Input | Output |
| --- | --- | --- |
| `trace_funds` | Transaction ID, direction, bounded depth, time window, row limit | Ordered transaction/account paths with IDs, timestamps, amount/currency, and query duration |
| `inspect_counterparties` | Account or transaction ID, bounded window and row limit | Related accounts and source-backed repeated transaction records |
| `detect_flow_patterns` | Transaction or account ID, bounded pattern set, time window, row limit | Defined pattern matches with supporting transaction IDs and query duration |

All tool queries are parameterized and bounded. An empty result is not evidence that no suspicious activity exists outside the query scope.

The current query implementation caps traversal at 1-3 hops, time windows at 30 days, result counts at 100, and client query timeouts at 30 seconds. `trace_funds` measures its anchor lookup and traversal together, and applies the row limit to each (direction, depth) branch, so it returns at most `result_limit` paths per branch. The window of account-anchored inspection and pattern queries ends at the account's latest sent or received transaction (`Account.last_epoch`; the iteration-1 statement returned an arbitrary epoch, ADR-017). Unknown anchors raise `graph.queries.TransactionNotFound` or `AccountNotFound`. Both are `ValueError` subclasses and keep the messages `unknown transaction_id: ...` and `unknown account_key or no account activity: ...`. `fan_in` and `fan_out` require a minimum count of distinct counterparties; `pass_through` returns two-hop chains within the configured dwell time. Amounts retain their source currencies.

## Data and model evaluation contract

Every benchmark run records dataset version and input hashes, split boundaries, feature set, model version, threshold, alert budget, test cohort, hardware, concurrency, and metric definitions. All candidate models use the same frozen test cohort. Test labels are accessible only to the offline evaluation process. The initial model, graph, and API measurements are in [the Iteration 1 performance report](benchmarks/iteration-1-performance.md). The current suite (`make bench`, `bench/`) and its results are in [the Iteration 2 performance report](benchmarks/iteration-2-performance.md); the API serves `docs/benchmarks/iteration-2-performance.json` by default. The `PerformanceSummary` schema keeps every Iteration 1 field and adds optional `p99_ms`, model `batches` and `full_test_cohort_all_threads`, `concurrency.sweeps`, and numeric cost estimates. Serial loopback throughput must not be presented as concurrent capacity, and capacity levels flagged as contaminated by background load must not be quoted as clean capacity.

Primary metrics are precision, recall, F1, average precision/PR-AUC, and recall at a named alert budget. Latency reports include P50/P95 and warm-up method. Dollar-weighted recall must state how value is counted and must not imply unique recovered funds.

### HI-Small v8 offline table

`data/processed/hi-small-v8/transactions.parquet` is the normalized offline transaction table. The source's two `Account` columns are mapped by position to `sender_account` and `receiver_account`. The canonical account keys are `{bank}::{account}` with both values preserved as strings. `transaction_id` is derived from the source row number and is stable only for the recorded transaction-file SHA-256.

Exact duplicate source rows are retained as distinct transaction records with distinct row-derived IDs. HI-Small v8 contains 9 such rows; the profile records the count. Do not deduplicate the primary benchmark cohort silently.

The table contains `transaction_id`, `timestamp`, sender/receiver bank and account fields, sender/receiver account keys, `amount_paid`, `payment_currency`, `amount_received`, `receiving_currency`, `payment_format`, `target_is_laundering`, `source_row_number`, and `split`. `target_is_laundering` is the only transaction label and is offline-only. `split` is one of `train`, `validation`, or `test` and is fixed by the adjacent `split-manifest.json`.

The generated `features.parquet` contains no target column. Feature generation projects only source transaction inputs, transaction IDs, and frozen split assignments. The offline baseline evaluator joins labels from the normalized transaction table by `(transaction_id, split)`, loads train and validation labels for fitting and threshold selection, then loads test labels only after thresholds are locked. This labeled evaluation path must not be wired into the graph, API, or inference feature path.

The source timestamp has minute resolution. All rows at one timestamp belong to one chronological split. A feature for transaction `t` may use only transactions strictly earlier than `t`; same-minute transactions are excluded from prior-history features. Keep each amount paired with its own currency. The patterns sidecar is not part of this feature table.

### Point-in-time graph feature sidecar

`traceaml-build-graph-features-batch` (default, vectorized from Parquet) and `traceaml-build-graph-features` (FalkorDB cross-check) write the same target-free Parquet sidecar keyed by `transaction_id`, with row-for-row identical values. Its exact columns are the transaction ID plus `gfeat_sender_out_24h`, `gfeat_sender_in_24h`, `gfeat_receiver_out_24h`, `gfeat_receiver_in_24h`, and `gfeat_prior_reverse_flow`. Both builders read only `transaction_id`, `timestamp`, `sender_account_key`, and `receiver_account_key`; they never read target, split, or pattern columns. DuckDB sorts those fields by `(timestamp, transaction_id)`, and the FalkorDB builder queries graph state before applying each complete minute. Recent counterparties use the inclusive 24-hour lower bound and strict earlier-minute upper bound. Reverse flow means a prior directed edge from receiver to sender. The FalkorDB builder uses a separate `FAccount`/`FLOW` graph with an account-key range index; the batch builder computes the same rules with columnar interval counting and rejects null projected values. Both record source hash, row reconciliation, feature definitions, ordering, and timings in a manifest.

The baseline trainer accepts this sidecar only through `--graph-features`. It requires the exact target-free schema, unique non-null IDs, integer features, and a binary reverse-flow flag. The offline evaluator joins by transaction ID and fails if any split loses rows; labels and split assignments continue to come only from the offline evaluation tables. The `lgbm_graph` arm shares the baseline's frozen split, threshold-selection rules, categorical encoding, and alert budget.
