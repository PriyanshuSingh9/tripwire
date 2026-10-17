# Benchmark snapshot reconciliation

This note reconciles the reported detection-metric snapshots so the final report can
use one defensible baseline. It is documentation only. It was produced without
running any benchmark, pipeline, training job, or test evaluation, and it adds no
new measurements. Every figure below is quoted from files already on disk
(`docs/benchmarks/*`, the local Git-ignored run artifacts, and `git log`).

The relevant gate is Task 2.3 of the build plan and the exit-gate line in the
benchmark plan: the earlier Iteration 1 average precision must be explained or
marked unreconciled, and the C1 full-test versus September 9-10 bulk gap must be
explained rather than resolved by picking the larger number.

## 1. Snapshots under review

| Label | Feature set | Reported value | Source on disk | Status |
| --- | --- | ---: | --- | --- |
| Iteration 1 LightGBM, earlier | 9 tabular / past-only features | AP 0.1853 | Git history only (commit `96e4f12`, tree); no artifact survives | Unreconciled, superseded |
| Iteration 1 LightGBM, frozen | 9 tabular / past-only features | AP 0.1587 | `artifacts/runs/hi-small-v8/baselines/`, `docs/benchmarks/iteration-1-performance.md` | Reconciled, baseline in use |
| C1 (all seven families) full test | 76 features, seven families | AP 0.7525 | `artifacts/runs/hi-small-v8/iteration-2-detection/candidates/s1_all_reg_neg100/`, test-ledger row 2 | Reconciled by cohort composition; superseded by the currency fix |
| C1 September 9-10 bulk | same model, bulk cohort only | AP 0.5996 | same artifacts, test-ledger row 2 | Reconciled by cohort composition; superseded by the currency fix |

No frozen score artifact exists for the 0.1853 run, and no `repro-check/` directory
survives under `artifacts/`. The 0.1587 baseline is the only Iteration 1 detection
snapshot with a model, a config, a split manifest, and a score artifact on disk.

## 2. Iteration 1 average precision: 0.1853 versus 0.1587

The 0.1853 figure was an earlier run of the same Iteration 1 pipeline, produced
before labels were fully isolated from feature construction. It is not
reproducible and has no surviving artifact.

Evidence from Git history:

- Commit `96e4f12` (`feat(ml): stabilize HI-Small evaluation pipeline`,
  2026-10-15) recorded the 0.1853 run in `README.md` and
  `docs/milestones/overview.md`, and in the same tree
  `docs/milestones/overview.md` stated: "A rerun after isolating labels changed
  the LightGBM result from the earlier run; the source of that variation is not
  isolated, so results need a repeatability check." The commit message states the
  stable value is 0.1587.
- Commit `408fd2f` (2026-10-15) replaced the 0.1853 text with 0.1587 and the
  sentence "These values supersede the earlier 0.1853 run." after the two clean
  runs reproduced scores and model artifacts bitwise.

Conclusion: 0.1853 is an unreconciled, superseded pre-label-isolation run. It must
not be used as a baseline. The frozen Iteration 1 baseline is 0.1587.

Iteration 1 frozen baseline record:

- Metric: average precision (test) `0.1587351185208954`; validation AP
  `0.15380280238329894`; validation max-F1 threshold `0.1555367329027308`.
- Source of metric: `artifacts/runs/hi-small-v8/baselines/baseline_metrics.json`.
- Model: `artifacts/runs/hi-small-v8/baselines/models/lgbm.txt`, sha256
  `3d49f14e8acd2bf45da6a816f6b6672ca0c3d06e106c13a9353b7d7b5c94b178`.
- Score artifact: `artifacts/runs/hi-small-v8/baselines/test_scores.parquet`,
  sha256 `ae15c08cf040eaa3066a24a36bac79dc0cf3beb296694034b6e8e289276d022a`
  (the same hash appears as `scores_sha256` on test-ledger row 1).
- Config: `artifacts/runs/hi-small-v8/baselines/baseline_config.json`.
- Split manifest: sha256
  `4bf0ff46191234f3eaa803c49e15ea9071735156085f095c3a25386f06fe2e28`.
- Features manifest: sha256
  `fa47a2c8a9ef23f9741e42367e0ca95be49f9df8e2b2443e5320c4f5db999658`.
- Evaluator version: `ml.baselines` (`traceaml-train-baselines`) as recorded in
  the same run directory; the Iteration 2 metric set re-analysis is test-ledger
  row 1 (`kind`: re-analysis of existing frozen test scores, no training).
- Cohort: frozen chronological test split, 761,639 rows, 1,561 positives
  (prevalence 0.20495%).
- Cleanliness: the run's own reproducibility note in
  `docs/benchmarks/iteration-1-performance.md` records a second clean-directory
  run matching predictions and model files; contamination is not tracked for
  this snapshot beyond that note.

### Unreconciled byte-level detail

`docs/benchmarks/iteration-1-performance.md` records the test-score artifact
hash for the frozen baseline as
`3442807e78b2cdd94b3f2fa14e54dc811b1f6f02260267dd9b8070543efb98d2`. The
`test_scores.parquet` on disk today hashes to
`ae15c08cf040eaa3066a24a36bac79dc0cf3beb296694034b6e8e289276d022a`, and no
artifact on disk carries `3442807e`. The metric is unaffected: the on-disk
artifact re-evaluates to the documented 0.1587 (test-ledger row 1), and the
parquet file's own hash is recorded there. The `3442807e` value cannot be tied
to any surviving file and is marked unreconciled; it is not used as a match key
in the final report.

## 3. C1 full-test 0.7525 versus September 9-10 bulk 0.5996

These are the same model and the same frozen test cohort, sliced two ways, so
there is no snapshot conflict. The gap is a cohort-composition effect, already
documented on disk.

C1 record:

- Metric: full-test AP `0.752466087982663`; September 9-10 bulk AP
  `0.5995878221585174`; validation AP `0.6088167038037393`; validation max-F1
  threshold `0.474806945330288`; best iteration 377.
- Source of metric:
  `artifacts/runs/hi-small-v8/iteration-2-detection/candidates/s1_all_reg_neg100/metrics.json`,
  and test-ledger row 2 in
  `artifacts/runs/hi-small-v8/iteration-2-detection/test_ledger.jsonl`.
- Model: `.../candidates/s1_all_reg_neg100/model.txt`, sha256
  `27491519206c23ff448d0bace0141385e24dac508fb49c8627947b3f84b152b0`.
- Score artifact: `.../candidates/s1_all_reg_neg100/test_scores.parquet`,
  sha256 `e70ee3e5d862d7db10643d2e60ee99b76dcdc106c3f5a349abed114c53dc3fec`.
- Config hash: `abbe71e7b0431eb2` (matches the declared candidate
  `ml/experiments/iteration2_candidates.json`).
- Split manifest: sha256
  `4bf0ff46191234f3eaa803c49e15ea9071735156085f095c3a25386f06fe2e28`
  (identical to the Iteration 1 baseline, confirming the same frozen split).
- Features manifest: sha256
  `87c4c1e72792a4c5d9f9731a0dbc3a2655603c84b295b15791b10a7b40f6616e`.
- Cohort: frozen test split, 761,639 rows, 1,561 positives; September 9-10 bulk
  is 760,531 rows with 906 positives.
- Cleanliness: the Iteration 2 detection report notes concurrent jobs on the
  shared host affected fit times but not scores; test evaluation was run once per
  candidate and is ledgered.

Why the numbers differ (quoted from existing on-disk files):

- `docs/benchmarks/iteration-2-detection.md` reports that the test tail
  (September 11-18, 1,108 rows, 655 positives, about 59% prevalence) is
  near-separable for every model, per-day AP 0.89-1.00 even for C3. That tail is
  1.5% of test rows but about 42% of test positives, so it inflates full-cohort
  AP and fixed-capacity recall.
- The same report states the September 9-10 bulk AP of 0.5996 "is the honest
  comparison" and that it "agrees with validation" (C1 validation AP 0.6088).
  The representative C1 figure is therefore the bulk value, not the full-cohort
  value.
- `docs/benchmarks/iteration-1-performance.md` and
  `docs/project-history-and-iteration-1-summary.md` independently describe the
  same distribution shift (0.1191% prevalence across September 9-10, 59.12%
  across September 11-18).

Conclusion: 0.7525 and 0.5996 are reconciled as full-cohort versus bulk-cohort
measurements of one model. The final report should present the bulk figure as the
representative number and label the full-cohort figure as tail-inflated. Neither
value may be presented as the sole headline.

## 4. Superseding condition on the C1 / C2 / C3 snapshots

The C1, C2, and C3 test evaluations were produced before the P0-01 currency
correction, so their `log_received_over_paid` feature compared raw amounts across
mismatched currencies. Their configs still name that feature
(`.../candidates/s1_all_reg_neg100/config.json`, feature list line
`log_received_over_paid`). The benchmark plan (section 11) and the exit gate
require that C1 0.7525 not be presented as the final headline until the corrected
candidate is re-evaluated on the frozen test cohort.

After this change, `log_received_over_paid` is null unless paid and received
amounts share one currency and both are positive, and the separate
`currency_mismatch` indicator carries the mismatched-currency signal. Any
re-evaluation requires analyst approval (the test split must not be re-evaluated
without it) and must be recorded as a new experiment with a new experiment id, so
these earlier artifacts are preserved rather than overwritten.

## 5. What this note does not claim

- It does not choose the most favorable number. The 0.1587 baseline, the bulk
  0.5996 C1 figure, and the tail caveat are all retained.
- It does not re-run any evaluation; it only maps reported numbers to the files
  that produced them.
- It does not reconcile the `3442807e` score-artifact hash, which has no matching
  file on disk; that value is marked unreconciled.
- All figures are synthetic-data results on the frozen HI-Small v8 split and are
  not evidence of real-world AML effectiveness.
