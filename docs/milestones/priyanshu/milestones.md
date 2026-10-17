# Priyanshu: Fraud Intelligence and Evaluation Charter

## Role

Own the data protocol, leakage controls, model evaluation, and review of every fraud-detection or performance claim. Also serve as integration lead: define cross-team contracts, delegate bounded work, review results, and resolve conflicts between the API, graph, model, and UI work.

## Iteration 1 outcomes

### 1. Establish an auditable dataset record

- Pin the dataset release and source-file hashes for each run. Keep raw and processed data out of Git.
- Profile the transaction, account, and pattern files without printing transaction examples or labels.
- Record schema, row counts, null/empty counts, timestamp coverage and resolution, target prevalence, payment-format and currency distributions, and account identity cardinalities.
- Normalize the duplicate source `Account` headers by column position. Preserve bank and account IDs as strings and build account keys from both values.
- Keep `Amount Paid` with `Payment Currency` and `Amount Received` with `Receiving Currency`. Never compare or sum values across currencies without a documented conversion source and date.
- Treat `HI-Small_Patterns.txt` as evaluation-only typology metadata. Missing pattern entries are not negative labels.

### 2. Freeze point-in-time evaluation data

- Use a chronological transaction split with approximately 70% training, 15% validation, and 15% test rows.
- Assign whole source timestamps to one split. The source has minute resolution; transactions sharing a timestamp are simultaneous for feature availability.
- Persist exact inclusive split boundaries, row counts, positive counts, algorithm, and source hashes in a split manifest. Do not change the test cohort after comparing models.
- Store the source target only as `target_is_laundering` in the offline Parquet table. Exclude it from graph ingestion, feature inputs, inference queries, API responses, and case memory.
- Build behavioral features only from transactions with timestamps strictly earlier than the scored transaction. Do not use same-minute peers or future transactions.
- Use the chronological transaction protocol as the primary benchmark. Account-stratified splits may be added as a separately named reproduction, never mixed into the primary result.

### 3. Deliver comparable detection baselines

- Start with a transaction-level logistic regression and LightGBM baseline on identical transaction-local and past-only behavioral features.
- Use Ashwani's graph feature contract: recent 24-hour distinct in/out counterparties for each endpoint and a prior reverse-flow flag. The feature builder must read no labels or split fields and must exclude same-minute activity. Compare LightGBM with and without these columns on the same frozen rows and labels.
- Exclude identifiers and targets from features unless their use is explicitly justified, point-in-time safe, and approved in the experiment configuration.
- Select model settings and thresholds using training and validation data only. Freeze the chosen threshold before opening test results.
- Save model configuration, feature names, input hashes, split-manifest hash, validation-selected threshold, and test metrics as machine-readable run artifacts.

### 4. Report useful, bounded metrics

- Report average precision (PR-AUC), precision, recall, and F1. State the split prevalence next to ranking metrics.
- Report recall and precision at a named alert budget, such as a fixed share of the cohort or alerts per 1,000 transactions. Keep the budget identical across model comparisons.
- Report dollar-weighted recall only with an explicit valuation basis. With mixed source currencies, report per-currency weighted recall or defer a combined figure until a dated FX source is selected.
- Measure scoring, graph-query, and end-to-end latency separately. Record warm-up, hardware, concurrency, and throughput. Separate measured service cost from estimates.
- Do not present synthetic benchmark performance as proof of real-world AML effectiveness. Do not present repeated transaction amounts as uniquely recovered funds.

### 5. Review integration and public claims

- Co-design graph-feature contracts with Ashwani and inference DTOs with Parth.
- Give Kanishka only reproducible result artifacts and approved metric wording. UI fixtures must be labeled and must not appear as retrieved graph evidence or measured results.
- Review model, graph, agent, latency, cost, and synthetic-data claims before they enter the UI, README, demo, or submission.
- Keep implementation choices and metric definitions synchronized across `docs/contracts.md`, `docs/decisions.md`, `docs/architecture.md`, and the shared delivery gates.

## Ownership boundaries

- Own the offline evaluation protocol, feature availability rules, benchmark runs, and conclusions.
- Do not own FalkorDB ingestion or Cypher implementation. The graph team owns topology and structural feature extraction; this role verifies their labels, timing, and reproducibility.
- Do not let a model or LLM decide policy actions. Thresholding and action mapping remain deterministic and are owned with the API team.
- Do not let evaluation labels or typology truth enter inference-time graph state or confirmed case memory.

## Current evidence and remaining work

- **Complete:** HI-Small Kaggle version 8 is present locally and ignored by Git. Its three source-file hashes, aggregate profile, currency ranges, and 9 exact duplicate rows are recorded in `artifacts/runs/hi-small-v8/profile.json`.
- **Complete:** normalized transaction Parquet and a chronological split manifest are generated under `data/processed/hi-small-v8/`. The frozen cutoffs are 2022-09-07 14:55 for train and 2022-09-09 03:16 for validation; all 5,177 positive labels are distributed across the three splits.
- **Complete:** transaction-local and strict past-only features plus logistic regression and LightGBM baselines run on the frozen chronological cohort. The feature table contains no target column; the offline evaluator joins labels separately and only loads test labels after thresholds are fixed. Metrics, thresholds, model configuration, and test scores are in `artifacts/runs/hi-small-v8/baselines/` (local, ignored artifacts). Two clean-directory runs reproduced test scores, metrics, and model files bitwise. On the 761,639-row test split (0.205% positive prevalence), LightGBM average precision is 0.1587; at the validation-selected max-F1 threshold, precision is 23.79%, recall is 24.79%, and F1 is 0.2428. At a predeclared 0.1% alert budget, recall is 15.89% (248 true positives). Logistic regression average precision is 0.0146; it detects no positives in the top 0.1% alert budget.
- **Caveat:** test prevalence (0.205%) is higher than training (0.080%) and validation (0.100%), indicating temporal distribution shift. These measurements describe this synthetic dataset and protocol only.
- **Graph checkpoint:** bounded FalkorDB ingestion and investigation queries are implemented and live-smoke-tested. The local 500,000-row chronological prefix takes about 706 MiB in the graph container and loads in 31.3 seconds. The full graph is not loaded on this host; a simple extrapolation is roughly 7 GiB before headroom.
- **Integration overlap:** the shared implementation checkpoint also added code under Ashwani's `graph/`, Parth's `services/api/`, and Kanishka's `apps/web/` ownership paths. Primary ownership remains unchanged; each member should review and take over extensions within their charter.
- **Complete:** the separate FalkorDB point-in-time feature builder wrote all 5,078,345 rows and the optional `lgbm_graph` trainer evaluated the frozen test cohort. The graph arm reached 0.1521 average precision versus 0.1587 for LightGBM alone. At a 0.1% alert budget it detected 230 positives (14.73% recall) versus 248 (15.89%). This graph feature set did not improve detection quality.
- **Complete:** raw LightGBM single-row inference, full-cohort scoring throughput, alert and benchmark endpoint latency, direct graph query latency, and end-to-end investigation latency were measured. The workload, hardware, warmups, repeats, costs, and limitations are in [the performance report](../../benchmarks/iteration-1-performance.md).
- **Not measured or priced:** dollar-weighted recall, concurrent service capacity, electricity or infrastructure cost, and cost per 1,000 decisions. No LLM provider was called; the current report generator is deterministic.

## Acceptance evidence

Iteration 1 evaluation work is complete when:

1. A clean setup can regenerate the source profile, normalized Parquet, and split manifest from the pinned input files.
2. Automated checks prove split boundaries do not overlap and same-timestamp rows stay together; no future or target-derived data enters features.
3. Logistic regression, LightGBM, and LightGBM plus point-in-time graph features share one frozen test cohort and save configuration and result artifacts. The graph ablation is complete and recorded in the benchmark report.
4. Thresholds are selected on validation only, alert-budget definitions are fixed, and currency-weighted metrics follow the documented valuation rule.
5. Every benchmark claim in the workbench and public materials traces to a saved run artifact and clearly labels the data as synthetic.
