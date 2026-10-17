# TraceAML ML (`ml`)

uv-managed offline evaluation project. Python >= 3.12. All commands run from
the repository root with `uv run --project ml`. It covers source profiling,
point-in-time feature generation, and transaction-level baselines.

## Setup

```sh
uv sync --project ml
```

## CSV profiler (aggregate metadata only)

Profiles a local CSV: row count, column names, dtypes, null counts. It never
prints row values or samples, and it rejects files with duplicate header
names instead of misreading them.

```sh
uv run --project ml traceaml-profile-csv /path/to/file.csv
```

Missing paths exit with code 2.

## HI-Small inspection (data version 8)

Streams the three source files with bounded memory and writes an
aggregate-only profile. `HI-Small_Patterns.txt` is strictly parsed into
typology attempt/row counts when it follows the expected block grammar (370
attempts across 8 typologies in v8); otherwise the profile records an
explicit coverage limitation instead of a partial parse. Pattern rows are
never linked to transaction IDs and never parsed into features. The profile
also records the exact full-row duplicate count (DuckDB out-of-core
`GROUP BY` over all raw fields, method noted in the metadata) and
per-currency amount min/max ranges with paid and received units kept
separate.

```sh
uv run --project ml traceaml-inspect-hi-small
uv run --project ml traceaml-inspect-hi-small --input-dir data/raw/hi-small \
  --output artifacts/runs/hi-small-v8/profile.json
```

## HI-Small preparation (normalized Parquet + frozen split)

Validates every record, normalizes the duplicate source `Account` headers by
position into `sender_account`/`receiver_account`, preserves banks and
accounts as strings with `bank::account` composite keys, keeps paid/received
amounts and currencies as separate measures, and freezes a chronological
train/validation/test split on whole-timestamp groups (nearest cumulative
cutoffs to 70%/85%). Labels land in exactly one offline column,
`target_is_laundering`, which must never enter graph properties, inference
queries, or feature inputs.

```sh
uv run --project ml traceaml-prepare-hi-small
uv run --project ml traceaml-prepare-hi-small --output-dir data/processed/hi-small-v8
```

Outputs:

- `data/processed/hi-small-v8/transactions.parquet`
- `data/processed/hi-small-v8/split-manifest.json` (hashes, schema, cutoffs,
  row counts, label prevalence, split algorithm)

## HI-Small feature table (past-only, transaction-level)

Builds one feature row per source transaction under
`artifacts/runs/hi-small-v8/baselines/`, reusing the exact frozen split
assignments and transaction IDs. Features are transaction-local log amounts,
currency/format categories, and sender/receiver prior incoming/outgoing
counts computed from per-account timestamp groups with a window ending at 1
PRECEDING (same-minute peers excluded together). The feature stage does not
read the target column; labels, pattern data, and cross-currency amount sums
are excluded. The run fails if split counts diverge, IDs mismatch, or hashes
disagree.

```sh
uv run --project ml traceaml-build-features
```

Outputs `features.parquet` and `features-manifest.json` in the run directory.

## Transaction baselines (logistic regression + LightGBM)

Trains both models on the identical feature set and frozen train split,
selects a max-F1 threshold on validation only, and early-stops LightGBM on
validation average precision. The evaluator joins labels from the offline
transaction table for train and validation, then joins test labels only after
thresholds are locked. It evaluates the frozen test split once and reports
average precision, precision/recall/F1 at the validation threshold,
and precision/recall at a predeclared 0.1% alert budget, with prevalence
and alert counts per split. Dollar-weighted recall, latency, and cost are
deferred.

```sh
uv run --project ml traceaml-train-baselines
```

Outputs `baseline_config.json`, `baseline_metrics.json`,
`models/logreg.joblib`, `models/lgbm.txt`, and `test_scores.parquet` in the
run directory. Joined splits are sorted by transaction ID; LightGBM trains
in deterministic CPU mode with fixed seeds and thread count, native
categorical features, and recorded package versions. Two clean-directory
reruns reproduced predictions, model files, and metrics bitwise exactly.

## Daily test-cohort report

Joins locked-threshold test scores to transaction timestamps and reports
per-day transactions, positives, and prevalence, plus per-day average
precision and threshold recall per model. Day-level model metrics require at
least 10 positives per day; thinner days report counts only. The test window
splits into a low-prevalence bulk (early days) and a high-prevalence burst
tail (later days); read threshold metrics with that shift in view.

```sh
uv run --project ml traceaml-daily-report
```

Outputs `daily_report.json` in the run directory.

## Iteration 2 experiment runner

`traceaml-experiment` builds the seven point-in-time feature families in
`ml.feature_families` (base, txlocal, velocity, amount, counterparty, cycle,
kyc; 76 features in about 3 minutes). It also runs validation-only grids from
`experiments/iteration2_grid.json` and evaluates pre-declared candidates
(`experiments/iteration2_candidates.json`) on the frozen test cohort,
appending every test look to `test_ledger.jsonl`. Real-data leakage audits
(`truncation-check`, `perturbation-check`) and target-free score publication
(`publish`) are also included. Subcommands: `features`, `validate`, `test`,
`rescore`, `truncation-check`, `perturbation-check`, `publish`. Pass absolute
or `../`-relative input and output paths. Method, every run, and results:
`docs/benchmarks/iteration-2-detection.md`.

Currency semantics: the `txlocal` column `log_received_over_paid` is computed
only when the paid and received amounts share one currency and both are
strictly positive; it is null otherwise, so amounts from different currencies
are never compared. The `currency_mismatch` indicator records the
mismatched-currency case separately. Every `features-manifest.json` restates
this as `currency_rule` and carries per-family `notes`.

Test lock: `test` accepts a candidate only when its name is declared in the
candidate manifest and its grid configuration still hashes to the recorded
`config_hash`. It refuses to overwrite an existing test artifact or append a
second ledger row for the same candidate and experiment id; a deliberate
repeat needs an explicit `--experiment-id`, which writes a separate artifact
directory and preserves earlier results. Each test config and ledger row
records the model, test-score, features-manifest and split-manifest hashes
together. Validation runs stay repeatable.

## Leakage and semantics tests

Fast tests pin the user-approved seams: target absent from feature
artifacts and model feature lists, same-minute peers excluded, past activity
included, future activity excluded (synthetic DuckDB scenario), and
validation-threshold vs fixed-capacity selection semantics kept distinct.

```sh
uv run --project ml --group dev pytest ml/tests -q
```

The checked-in protocol and local run artifacts currently show these results
on the frozen v8 chronological test split (761,639 transactions; 0.205%
positive prevalence):

| Model | Average precision | Precision at validation threshold | Recall at validation threshold | F1 at validation threshold | Recall at 0.1% alert budget |
| --- | ---: | ---: | ---: | ---: | ---: |
| Logistic regression | 0.0146 | 1.79% | 29.60% | 0.0337 | 0.00% |
| LightGBM | 0.1587 | 23.79% | 24.79% | 0.2428 | 15.89% |

These are run outputs on synthetic data, not evidence of real-world AML
effectiveness. Validation-threshold rows answer how the tuned operating
point performs (variable alert counts: 25,875 logreg vs 1,627 LightGBM
alerts on test); the budget column answers what a fixed 762-alert review
capacity catches — the two framings must not be interchanged. Test
prevalence is higher than train and validation, so threshold metrics should
be read with that temporal shift in view. Graph features, graph-enhanced
model results, latency, and cost are not included.

All output paths are ignored by Git. Command output contains aggregates
only, never transaction rows.

## Graph-feature ablation

`make data-train-graph` builds the point-in-time graph sidecar with the
vectorized offline builder (`traceaml-build-graph-features-batch`, ADR-015).
It then trains the `lgbm_graph` arm with
`traceaml-train-baselines --graph-features data/processed/hi-small-v8/graph-features.parquet`.
The FalkorDB builder (`make graph-features-falkordb`) produces identical
values and remains available as a cross-check.
