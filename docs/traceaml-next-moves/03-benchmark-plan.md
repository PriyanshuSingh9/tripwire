# 03 — Benchmark and Evaluation Plan

This document defines what TraceAML can responsibly claim and how to produce final measurements. It separates detection performance, graph performance, agent quality and operating cost. Those are related but not interchangeable.

## 1. Why benchmark discipline matters

The current implementation report is promising but lists unresolved issues that can affect the headline result:

- `log_received_over_paid` is computed even when the input and output currencies differ.
- The experiment runner does not strictly enforce the predeclared candidate manifest or prevent repeated test evaluation and overwritten outputs.
- The full test average precision is affected by a late test tail that is nearly separable; C1 AP is 0.7525 overall but 0.5996 over September 9–10.
- The older Iteration 1 LightGBM AP was reported as 0.1853 in an earlier summary and 0.1587 in the latest report's saved-score reevaluation.
- Some load/capacity windows were contaminated by other host activity.
- No live Gemini call is included in the recorded provider latency/cost measurements.

The final report must preserve these facts until controlled experiments resolve or characterize them.

## 2. Data and split protocol

### Dataset identity

- Source: IBM AMLworld HI-Small, Kaggle version 8 according to the implementation report.
- Files: `HI-Small_Trans.csv`, `HI-Small_accounts.csv`, `HI-Small_Patterns.txt`.
- Raw data is synthetic and excluded from Git.
- Source hashes and schema are written to local profile and split manifests.
- Transaction IDs derive from source row positions and are tied to the source-file hash.
- Nine exact duplicate transaction rows were retained as distinct source records.

### Frozen chronological split

| Split | Rows | Positive labels | Boundary |
|---|---:|---:|---|
| Train | 3,554,957 | 2,856 | Through 2022-09-07 14:55 |
| Validation | 761,749 | 760 | Through 2022-09-09 03:16 |
| Test | 761,639 | 1,561 | Later transactions |

All rows at the same minute are assigned as a group. Feature history uses intervals of the form `[t - w, t)` and activity strictly before the current minute. The target label is joined separately by the offline evaluator and is excluded from features, graph properties and online score APIs.

Do not change this split to improve model scores. If a split change becomes necessary for a methodological reason, version it as a new experiment and clearly mark the earlier test cohort as already used.

## 3. Fix feature correctness first

### Currency consistency

Fix `log_received_over_paid` before promoting the candidate. Raw amount ratios are only semantically comparable under the feature's declared currency rule. For the deadline, use the conservative rule: compute the ratio for compatible currency pairs and mark it unavailable for mismatched currencies. Keep a separate currency-mismatch feature if the model is allowed to use that signal. Do not add FX conversion without a dated, documented conversion dataset.

Tests must cover:
- equal currency and positive valid amounts;
- different currencies;
- zero paid amount;
- missing/invalid amount;
- whether missingness is represented consistently during training and inference.

### Static account registry / KYC family

The account registry in HI-Small is a snapshot without event timestamps. The current report records this as an availability assumption and preserves C2 as a no-registry comparison. Maintain that comparison. State explicitly that registry attributes are assumed available to the scoring system; do not describe them as point-in-time facts if the data cannot prove that.

### Feature manifest

Every candidate report should identify exactly which families were included, the feature count after model filtering, the missing-value behavior, categorical treatment, and the relevant config/data hashes.

## 4. Lock down the experiment runner

The test evaluator must fail closed.

1. Iterative model selection and hyperparameter searches use train/validation only.
2. The accepted final candidate names/config hashes are committed in the candidate manifest.
3. A final test run accepts only a manifest entry.
4. Existing test outputs cannot be overwritten by default.
5. A deliberate repeat requires a new experiment/run ID and preserves earlier output. It must state why the previous run was invalid or why a genuinely new protocol is being evaluated.
6. `test ledger`, model hash, score artifact hash, split-manifest hash and metric JSON must agree.
7. Test results are not used to choose features, thresholds or models for the same claimed evaluation.

If the current run has already used the test tail to make design decisions, disclose that the test set is no longer fully untouched. Do not hide this by creating an unlabelled rerun.

## 5. Detection models and ablations

Required comparison:

| Candidate | Feature set | Question it answers |
|---|---|---|
| Logistic regression | Base/tabular inputs | What does a simple statistical classifier achieve? |
| C3 | Original Iteration 1 features with Iteration 2 regularization | Did training/configuration improvement help before adding all new feature families? |
| C2 | All selected feature families except static registry/KYC | Does the model work without the registry-availability assumption? |
| C1 | All predeclared feature families, after fixing currency semantics | Does the fuller feature set add value? |
| Graph ablation | Base or selected non-graph features vs with/without graph-derived families | What is the incremental contribution of relationship/flow information? |

For a cleaner graph claim, keep other settings fixed and compare models differing only by the graph feature family where possible. A comparison with many other simultaneous changes cannot isolate the value of graph information.

Do not compare raw results across public repositories unless you reproduce their exact split and feature protocol. Public results are design references, not a shared leaderboard.

## 6. Detection metrics

### Required

- **Average precision / PR-AUC:** primary ranking metric for the highly imbalanced positive class. State the implementation used, since “PR-AUC” can be used for different calculations.
- **Precision, recall and F1 at validation-selected threshold:** choose threshold on validation only; freeze it and apply unchanged to test.
- **Recall at a fixed alert budget:** a separate ranking/capacity metric.
- **Confusion counts:** TP, FP, FN and total selected alerts.
- **Prevalence:** report positive fraction for every evaluated cohort.
- **Daily/time-bulk metrics:** make temporal irregularities visible.
- **Per-currency amount-weighted recall:** only within a currency unless a documented conversion source and valuation timestamp are used.

### Threshold vs alert-budget semantics

These are two different operating points and must not be conflated:

1. **Validation-threshold evaluation:** choose the F1 threshold on validation; apply that numeric threshold to the entire test cohort unchanged. This evaluates a fixed policy threshold.
2. **Recall at 0.1% alert budget:** rank test scores and select the top 762 records for this 761,639-row cohort (subject to a deterministic tie-break rule). This is a capacity/ranking analysis. It uses the test score distribution to decide the selected count, so it is not the result of a preconfigured production threshold.

Name the latter “recall at fixed review capacity” or “recall at 0.1% alert budget.” State the row count and selected alerts. Never imply it was chosen on validation unless that is what the code truly does.

### Currency-weighted results

Do not sum amounts across currencies. Report per-currency metrics or omit aggregate dollar-weighted recall. Repeated transfers can count the same funds multiple times; name the metric precisely and do not imply unique proceeds recovered.

## 7. Analyze the temporal tail responsibly

The report notes the test cohort has 1,561 positives (0.205% prevalence), with 655 positives in the last 1,108 rows from September 11–18. This late tail strongly affects full-cohort metrics.

Required diagnostic report:
- daily transaction count and positive count;
- daily prevalence;
- AP where both classes and counts make it meaningful;
- precision/recall using the frozen validation threshold;
- positive count and metric for September 9–10 bulk, reported alongside the full test;
- feature distributions and pattern composition by period, if available;
- whether models all improve sharply on the same tail (a potential synthetic simulator signature).

Do not use the tail to pick a candidate and then continue describing the same test as an untouched final evaluation. If analysis causes feature changes, freeze the new candidate and state that the observed test is now a development/diagnostic cohort for that round.

## 8. Graph and product latency metrics

Measure separately:

### Graph database
- full graph load duration;
- peak process/server memory and graph memory;
- account, transaction and relationship reconciliation counts;
- query P50/P95 for typical, fixed demo, high-degree and max-depth anchors;
- result truncation, timeout and error rates;
- whether repeated graph load is idempotent in merge mode.

### API
- `GET /api/alerts` response time;
- case investigation end-to-end P50/P95;
- serial and concurrent throughput at fixed client counts;
- error rate and tail latency;
- deterministic report vs live LLM report, measured separately.

### Model
- one-row scoring latency;
- batch throughput;
- all-cohort batch runtime;
- CPU/thread count and hardware/software versions.

### Environment hygiene
- warm up before measurement;
- record sample count, warmups, concurrency, input hashes and hardware;
- run without unrelated heavy jobs whenever possible;
- mark contaminated windows; do not quietly combine clean and contaminated samples;
- report demo-graph measurements separately from full-graph measurements.

The current report's figures (for context, not guaranteed current service status) include full graph loading in about 520.9 seconds, graph memory about 2,514 MB, investigation API about 11.04 ms P50 / 13.69 ms P95 on the demo graph, and about 23.2 ms P50 for a five-tool full-graph case. These are not interchangeable workloads. High-degree anchors and three-hop traces have worse tail latency.

## 9. Agent-quality and evidence metrics

At minimum record:

- tool-call success rate;
- unknown-anchor rate;
- timeout/truncation rate;
- fraction of displayed citations present in retrieved evidence;
- verifier rejection/fallback rate;
- supported-path accuracy on a small curated case set;
- case action correctness (did deterministic policy permit the requested action?);
- investigation runtime with the LLM disabled and enabled;
- live LLM token use and provider cost only when the provider reports usage and price configuration is present.

Do not score natural-language eloquence as detection quality. The agent and classifier have distinct roles, and those metrics must remain separate.

## 10. Cost accounting

Separate actual from estimated costs:

- **Measured local resource use:** CPU time, peak memory, wall time, throughput.
- **Compute price estimate:** apply dated cloud list price to resource consumption or a stated provisioned-server assumption.
- **LLM provider cost:** actual token counts and configured rates, if available.
- **Excluded costs:** disclose storage, network, observability, staff and other items not captured.

A local run does not itself have an invoice proving a cloud cost. Use “estimated compute cost” where appropriate. Report cost per 1,000 scored transactions and cost per full investigation separately.

## 11. How to present benchmark results

The UI and demo should show:
- frozen cohort and prevalence;
- baseline vs selected candidate;
- full test and bulk-period results;
- threshold-based metrics separately from fixed-alert-budget recall;
- graph feature ablation;
- P50/P95 with workload and graph scope;
- deterministic versus LLM path and provider calls;
- synthetic-data limitation and tail caveat.

Do not present C1 `0.7525` as the sole headline after fixing the currency bug. Rerun and report the valid candidate. If the results change materially, explain what changed and why. If C2 remains close to C1 without the static registry family, prefer the simpler/less assumption-heavy model unless there is a verified reason to retain KYC features.

## 12. Benchmark exit gate

- [ ] Currency-inconsistent ratio is corrected and tested.
- [ ] Final candidate is predeclared and test runner refuses arbitrary/repeated final tests.
- [ ] Previous AP discrepancy is explained or old result marked unreconciled.
- [ ] Final score artifact, model config and split hash are aligned.
- [ ] Full cohort and bulk-period metrics are both documented.
- [ ] Alert-budget semantics and tie-breaking are stated.
- [ ] Graph/API/model performance measurements are cleanly separated.
- [ ] Contaminated samples are flagged and not marketed as clean.
- [ ] Provider cost/latency is not claimed without a live measured run.
- [ ] All claims in README/video match the final benchmark report.
