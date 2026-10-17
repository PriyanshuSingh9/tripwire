# Iteration 2 detection experiments

Measured 2026-10-15 to 2026-10-16 on the local development machine (AMD Ryzen 9 8945HS, 8 cores / 16 threads, 15.8 GB RAM, shared with other agents running concurrent jobs). All results use **synthetic IBM AMLworld HI-Small v8 data** and the frozen chronological split from Iteration 1. They describe this implementation on this synthetic dataset. They are not evidence of real-world AML performance.

## Summary

| Model (test cohort, 761,639 rows, 0.205% positive) | Test AP | Test P / R / F1 at validation threshold | Recall at 0.1% budget (762 alerts) | Sept 9-10 bulk AP |
| --- | ---: | ---: | ---: | ---: |
| Iteration 1 LightGBM baseline (frozen scores, re-analyzed) | 0.1587 | 23.79% / 24.79% / 0.2428 | 15.89% (248) | 0.0877 |
| C3: Iteration 1 features, Iteration 2 regularization | 0.2409 | 38.92% / 24.86% / 0.3034 | 21.40% (334) | 0.1503 |
| C2: all families except static account registry | 0.7326 | 83.08% / 63.55% / 0.7201 | 47.53% (742) | 0.5744 |
| **C1: all seven families (selected model)** | **0.7525** | **81.28% / 66.75% / 0.7330** | **47.73% (745)** | **0.5996** |

- The selected model (C1) beats the Iteration 1 baseline on every pre-declared metric. It was chosen on validation (AP 0.6088) before any new test evaluation.
- Most of the gain comes from past-only behavioural and graph-structural features, not from tuning. Regularization alone (C3) moves test AP from 0.159 to 0.241.
- Graph features did not actually fail in Iteration 1. On validation, the same five sidecar columns raise AP in every seed tested. The Iteration 1 test drop came from one overfit, unstable model (see [Why the Iteration 1 graph arm looked worse](#why-the-iteration-1-graph-arm-looked-worse)).
- The test tail (Sept 11-18, 1,108 rows, 59% positive) is easy for every model: per-day AP is 0.89-1.00 even for C3. The Sept 9-10 bulk is the honest comparison. There, C1 reaches AP 0.600 versus 0.088 for Iteration 1, consistent with its validation AP.

## Data and protocol

- **Input:** `data/processed/hi-small-v8/transactions.parquet` (5,078,345 rows) and `split-manifest.json`, unchanged from Iteration 1. The frozen cutoffs are train `<= 2022-09-07 14:55` (3,554,957 rows, 2,856 positives), validation `<= 2022-09-09 03:16` (761,749 rows, 760 positives), and test after that (761,639 rows, 1,561 positives). Account registry: `data/raw/hi-small/HI-Small_accounts.csv` (518,581 accounts).
- **Point-in-time rule:** features for a transaction at minute `t` use only rows with timestamp strictly earlier than `t`. Same-minute rows are simultaneous and invisible to each other. Windows are half-open `[t - w, t)`. The scored row's own attributes (amounts, currencies, format, banks, hour) are allowed.
- **Target isolation:** the feature builder sees a projection with no label, split, or pattern columns. It fails closed if any forbidden column is visible. Labels are joined by `transaction_id` only inside the trainer. `HI-Small_Patterns.txt` is never read. No target encoding is used.
- **Selection:** families, hyperparameters, negative-sampling rate, and thresholds were chosen on train and validation only. LightGBM early-stops on validation average precision, and the max-F1 threshold is chosen on validation. This makes validation metrics mildly optimistic. The test cohort is frozen.
- **Test discipline:** three candidates were pre-declared in `ml/experiments/iteration2_candidates.json` and committed (`797b1c7`) before any new test evaluation. Each was evaluated on test exactly once. Every test evaluation is in the [test ledger](#test-ledger).
- **Metrics:** the same as Iteration 1. That is AP per split with prevalence, precision/recall/F1 at the validation max-F1 threshold, and recall/precision at a fixed 0.1% test alert budget (top `ceil(0.001 * n)` scores). Two additions:
  - **Sept 9-10 bulk:** the same metrics restricted to test rows dated 2022-09-09 or 2022-09-10 (760,531 rows, 906 positives). Ranking and the budget (761 alerts) are recomputed within the bulk only. The threshold is still the validation threshold.
  - **Per-currency amount-weighted recall at the 0.1% budget:** for each payment currency `c`, the sum of `Amount Paid` over alerted positives paid in `c`, divided by the sum of `Amount Paid` over all positives paid in `c`. Amounts stay in their own payment currency. There is no FX conversion, and values are never summed across currencies. The alert set is the global top 0.1% (or the bulk top 0.1% for bulk rows), not a per-currency budget.
- **Reproducibility:** LightGBM 4.7.0 with `deterministic=true`, `force_row_wise=true`, `num_threads=6`, and fixed seeds. Splits are sorted by `transaction_id`, and negative sampling uses a seeded NumPy generator. Each validation record stores the config, config hash, feature names, prepared-Parquet hash, split-manifest hash, features-manifest hash, and git commit. Retraining C1 for its test run reproduced its validation run exactly (validation AP 0.60882, best iteration 377).

## Feature families

All families are DuckDB SQL over per-account minute groups, as-of joins, and windowed frames (`ml/src/ml/feature_families.py`). The full build writes 76 features for 5,078,345 rows in **177 s** (6 DuckDB threads, 4 GB memory limit). For comparison, the Iteration 1 FalkorDB sidecar took 1,563 s for 5 columns.

| Family | Columns | Build s | Content | State needed at online serving time |
| --- | ---: | ---: | --- | --- |
| `base` | 9 | 13.8 | Iteration 1 features: log amounts, currencies, format, all-time prior sent/received counts. Reproduces the Iteration 1 feature table exactly (0 differing rows). | Per-account sent/received counters |
| `txlocal` | 8 | 0.7 | Currency mismatch, cross-bank, self-transfer, round-100/1000 amount, has-cents, log(received/paid), hour of day | Stateless |
| `velocity` | 22 | 31.6 | Sender/receiver out/in counts over 1h/6h/24h/7d; minutes since last out/in; minutes since first seen | Per-account minute-bucket counts for 7 days, first/last-seen times |
| `amount` | 14 | 26.9 | Per (account, currency): log-amount z-score versus own history, 24h/7d out/in amount sums, pass-through ratio (this payment / same-currency inflow in 24h/7d) | Per (account, currency) running count/sum/sum of squares and 24h/7d amount sums |
| `counterparty` | 20 | 59.1 | Prior pair count and minutes since last pair transfer, reverse-flow count and recency, distinct out/in counterparties (all-time, 24h, 7d) for both endpoints | Per-pair count/last time; per-account distinct-counterparty sets, all-time and expiring 24h/7d |
| `cycle` | 2 | 4.1 | Count of third parties X with prior R->X and X->S edges (S->R closes a 3-cycle), all-time and 7-day | Per-account in-neighbour first-seen map plus pair index |
| `kyc` | 5 | 1.6 | Entity type of sender/receiver (from the entity-name prefix), accounts per entity, same-entity flag. Entity IDs, names, and numbers are not emitted. | Static registry lookup |

Windowed distinct counts use an exact interval sweep. Each pair event at minute `e` keeps the pair active for query minutes `[e + 1, e + w]`, overlapping intervals of a pair merge into +1/-1 deltas, and a cumulative sum gives the count. The 24h columns match the separately built vectorized graph sidecar (`graph-features-batch.parquet`) on all 5,078,345 rows for all five Iteration 1 graph columns (0 mismatches). That is an independent cross-check of two implementations.

The `cycle` family is correct (brute-force test) but inert on this data. Closures are non-zero for only 321 train rows (0.009%), so LightGBM's feature pre-filter drops them at `min_data_in_leaf=200`. Its ablation runs are identical to their parents.

## Leakage controls and self-audit

The jump from validation AP about 0.15 (Iteration 1) to about 0.61 needed scrutiny. These checks were run:

1. **Synthetic tests** (`ml/tests/test_feature_families.py`, 25 tests):
   - Per family, rewriting, dropping, and adding rows after a cutoff leaves earlier features unchanged.
   - Adding, removing, or rewriting same-minute peers leaves a row's 76 features unchanged.
   - Label or split columns in the input fail closed, and sub-minute timestamps are rejected.
   - Perturbing earlier rows does change history features, so the invariance tests are not vacuous.
   - A brute-force Python reference matches velocity counts, windowed distinct counts, pair/reverse counts, 3-cycle closures, same-currency inflow sums, and recency on sampled rows.
2. **Real-data truncation audit** (`traceaml-experiment truncation-check`, `features/truncation-check.json`): rebuilding all families from rows up to `2022-09-09 03:16` only (every test row removed) gives identical values on all 76 columns for all 4,316,706 train and validation rows.
3. **Real-data perturbation audit** (`traceaml-experiment perturbation-check`): for randomly sampled busy minutes `m`, the input keeps:
   - every row before `m`, unchanged;
   - a random half of the rows at `m` (the other half, same-minute peers, are deleted);
   - the next 7 days of rows rewritten: about 30% dropped, receivers swapped with the next row's receiver, amounts multiplied by 7.

   Every kept row at `m` must match the full build exactly. Run 1 (seed 20221016, 3 minutes): 512 probe rows, 557 peers removed, 0 mismatches (`features/perturbation-check.json`). Run 2 (seed 7): 9 of 12 planned minutes completed before it was stopped to free the CPU, with 1,553 probe rows, 1,607 peers removed, and 0 mismatches (`features/perturbation-check-seed7-partial.jsonl`). An earlier version of this audit deleted peers at 2,000 minutes in one build and reported mismatches. That design was flawed: deleted peers at an early minute are legitimate history for later probes. It was replaced by the per-minute design above.
4. **Validation excluding the final validation day:** C1 validation AP is 0.6678 on Sept 7, 0.6165 on Sept 8, and 0.3727 on the partial Sept 9 (00:00-03:16, 50 positives). Excluding Sept 9 gives 0.6288. The gain is not driven by the end of the validation window.
5. **Ablation and importance:** gain is spread across many features. The top C1 feature, `payment_format`, has 16% of gain, and it was already the top feature of the Iteration 1 feature set. No single column explains the jump; see [Feature-family ablation](#feature-family-ablation-validation).

No check found leakage. The remaining risk is not temporal leakage but **synthetic-generator regularity**. AMLworld laundering follows scripted typologies, and recency and account-age features may exploit generator artifacts that real data would not have. See [Interpretation](#interpretation).

## Model and selection procedure

LightGBM binary classifier with these defaults:

- `learning_rate` 0.05, `num_leaves` 63, `min_data_in_leaf` 200, `feature_fraction` 0.7;
- `bagging_fraction` 0.8 / `bagging_freq` 1, `lambda_l2` 10, `max_bin` 255;
- up to 3,000 rounds with early stopping after 200 rounds on validation AP;
- native categoricals mapped from train-observed values, with an unknown bucket.

Negative sampling, where used, keeps every train positive plus a seeded fraction of train negatives. Validation is never sampled.

- **Stage 1:** Iteration 1 parameters versus the regularized defaults, and the negative-sampling rate.
- **Stage 2:** single-parameter variations around the defaults, plus seeds.
- **Stage 3:** family leave-one-out and add-one ablations, and the Iteration 1 graph-arm replication.
- **Stage 4:** the C2 validation run.

The hyperparameter surface is flat. All stage-2 variants land within 0.574-0.597 validation AP, inside about two seed standard deviations (seed sd about 0.008), so the defaults were kept. C1 is the highest-validation-AP all-family run (0.6088, no negative sampling).

**Selected model C1 (`s1_all_reg_neg100`):** all seven families (76 features, 74 effective), the defaults above, no negative sampling, seed 42. Best iteration 377. Validation threshold 0.474807. Fit 195 s on 3,554,957 rows with 6 threads.

## Validation runs

Every validation run, in ledger order (`validation_runs.jsonl`). Validation only; none of these touched test labels. "all" means the seven families. AP is average precision. Validation prevalence is 0.0998%.

| # | Run | Families / change | Neg. rate | Features | Train AP | Val AP | Val P / R / F1 at max-F1 | Val recall @0.1% | Best iter | Fit s |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `s1_base_iter1params` | base; num_leaves=127, min_data_in_leaf=100, feature_fraction=0.8, lambda_l2=0.0 | 1.0 | 9 | 0.7833 | 0.0864 | 0.232 / 0.166 / 0.193 | 0.189 | 363 | 84 |
| 2 | `s1_base_reg_neg010` | base | 0.1 | 9 | 0.2428 | 0.2195 | 0.410 / 0.245 / 0.306 | 0.288 | 374 | 28 |
| 3 | `s1_all_reg_neg010` | all | 0.1 | 76 | 0.7473 | 0.5966 | 0.796 / 0.504 / 0.617 | 0.571 | 497 | 53 |
| 4 | `s1_all_reg_neg030` | all | 0.3 | 76 | 0.7202 | 0.5832 | 0.807 / 0.495 / 0.613 | 0.545 | 274 | 54 |
| 5 | `s1_all_reg_neg100` | all | 1.0 | 76 | 0.8246 | 0.6088 | 0.820 / 0.522 / 0.638 | 0.564 | 377 | 157 |
| 6 | `s2_nl15` | all; num_leaves=15 | 0.1 | 76 | 0.6694 | 0.5737 | 0.778 / 0.474 / 0.589 | 0.547 | 1461 | 109 |
| 7 | `s2_nl31` | all; num_leaves=31 | 0.1 | 76 | 0.6459 | 0.5874 | 0.745 / 0.520 / 0.612 | 0.557 | 470 | 52 |
| 8 | `s2_nl127` | all; num_leaves=127 | 0.1 | 76 | 0.8604 | 0.5894 | 0.719 / 0.525 / 0.607 | 0.563 | 585 | 75 |
| 9 | `s2_mdl50` | all; min_data_in_leaf=50 | 0.1 | 76 | 0.8579 | 0.5790 | 0.701 / 0.517 / 0.595 | 0.558 | 1071 | 91 |
| 10 | `s2_mdl1000` | all; min_data_in_leaf=1000 | 0.1 | 76 | 0.7271 | 0.5948 | 0.812 / 0.488 / 0.610 | 0.563 | 480 | 75 |
| 11 | `s2_ff050` | all; feature_fraction=0.5 | 0.1 | 76 | 0.8198 | 0.5888 | 0.820 / 0.486 / 0.610 | 0.555 | 808 | 159 |
| 12 | `s2_l2_0` | all; lambda_l2=0.0 | 0.1 | 76 | 0.9722 | 0.5740 | 0.810 / 0.482 / 0.604 | 0.554 | 2223 | 213 |
| 13 | `s2_l2_50` | all; lambda_l2=50.0 | 0.1 | 76 | 0.6951 | 0.5922 | 0.825 / 0.495 / 0.618 | 0.564 | 755 | 76 |
| 14 | `s2_lr002` | all; learning_rate=0.02 | 0.1 | 76 | 0.7352 | 0.5914 | 0.821 / 0.495 / 0.617 | 0.559 | 1072 | 115 |
| 15 | `s2_spw5` | all; scale_pos_weight=5.0 | 0.1 | 76 | 0.8917 | 0.5941 | 0.789 / 0.503 / 0.614 | 0.563 | 1150 | 127 |
| 16 | `s2_nl31_mdl1000_ff050` | all; num_leaves=31, min_data_in_leaf=1000, feature_fraction=0.5 | 0.1 | 76 | 0.6511 | 0.5944 | 0.775 / 0.497 / 0.606 | 0.564 | 552 | 66 |
| 17 | `s2_seed1` | all; seed 1 | 0.1 | 76 | 0.6680 | 0.5961 | 0.798 / 0.504 / 0.618 | 0.557 | 300 | 44 |
| 18 | `s2_seed2` | all; seed 2 | 0.1 | 76 | 0.8042 | 0.6082 | 0.865 / 0.499 / 0.633 | 0.575 | 738 | 99 |
| 19 | `s2_seed3` | all; seed 3 | 0.1 | 76 | 0.8598 | 0.5887 | 0.783 / 0.499 / 0.609 | 0.564 | 1044 | 171 |
| 20 | `s3_drop_txlocal` | all minus txlocal | 0.1 | 68 | 0.7624 | 0.5922 | 0.854 / 0.484 / 0.618 | 0.561 | 567 | 73 |
| 21 | `s3_base_plus_txlocal` | base+txlocal | 0.1 | 17 | 0.2172 | 0.2321 | 0.364 / 0.263 / 0.306 | 0.289 | 180 | 26 |
| 22 | `s3_drop_velocity` | all minus velocity | 0.1 | 54 | 0.6572 | 0.5939 | 0.865 / 0.482 / 0.619 | 0.551 | 335 | 73 |
| 23 | `s3_base_plus_velocity` | base+velocity | 0.1 | 31 | 0.4747 | 0.3021 | 0.435 / 0.317 / 0.367 | 0.343 | 467 | 76 |
| 24 | `s3_drop_amount` | all minus amount | 0.1 | 62 | 0.6528 | 0.5758 | 0.719 / 0.479 / 0.575 | 0.547 | 498 | 61 |
| 25 | `s3_base_plus_amount` | base+amount | 0.1 | 23 | 0.7720 | 0.4362 | 0.757 / 0.353 / 0.481 | 0.430 | 1106 | 148 |
| 26 | `s3_drop_counterparty` | all minus counterparty | 0.1 | 60 | 0.6562 | 0.5425 | 0.727 / 0.467 / 0.569 | 0.517 | 339 | 38 |
| 27 | `s3_base_plus_counterparty` | base+counterparty | 0.1 | 25 | 0.5038 | 0.5031 | 0.684 / 0.433 / 0.530 | 0.493 | 514 | 41 |
| 28 | `s3_drop_cycle` | all minus cycle | 0.1 | 74 | 0.7473 | 0.5966 | 0.796 / 0.504 / 0.617 | 0.571 | 497 | 54 |
| 29 | `s3_base_plus_cycle` | base+cycle | 0.1 | 11 | 0.2428 | 0.2195 | 0.410 / 0.245 / 0.306 | 0.288 | 374 | 28 |
| 30 | `s3_drop_kyc` | all minus kyc | 0.1 | 71 | 0.7830 | 0.5864 | 0.827 / 0.514 / 0.634 | 0.566 | 662 | 60 |
| 31 | `s3_base_plus_kyc` | base+kyc | 0.1 | 14 | 0.4002 | 0.2667 | 0.385 / 0.289 / 0.331 | 0.318 | 496 | 36 |
| 32 | `s3_all_no_recency` | all minus 8 recency/age columns | 0.1 | 68 | 0.6038 | 0.5803 | 0.730 / 0.504 / 0.596 | 0.547 | 204 | 32 |
| 33 | `s3_iter1params_seed1` | base; Iteration 1 params; seed 1 | 1.0 | 9 | 0.9875 | 0.1216 | 0.230 / 0.217 / 0.224 | 0.221 | 817 | 140 |
| 34 | `s3_iter1params_gfeat_seed1` | base+gfeat; Iteration 1 params; seed 1 | 1.0 | 14 | 0.9997 | 0.2281 | 0.414 / 0.263 / 0.322 | 0.293 | 2794 | 475 |
| 35 | `s3_iter1params_seed2` | base; Iteration 1 params; seed 2 | 1.0 | 9 | 0.9977 | 0.1510 | 0.300 / 0.196 / 0.237 | 0.220 | 1579 | 224 |
| 36 | `s3_iter1params_gfeat_seed2` | base+gfeat; Iteration 1 params; seed 2 | 1.0 | 14 | 0.9993 | 0.2227 | 0.396 / 0.242 / 0.300 | 0.279 | 1716 | 421 |
| 37 | `s3_iter1params_seed3` | base; Iteration 1 params; seed 3 | 1.0 | 9 | 0.9973 | 0.1424 | 0.280 / 0.207 / 0.238 | 0.232 | 1361 | 294 |
| 38 | `s3_iter1params_gfeat_seed3` | base+gfeat; Iteration 1 params; seed 3 | 1.0 | 14 | 0.9938 | 0.1948 | 0.357 / 0.263 / 0.303 | 0.288 | 748 | 187 |
| 39 | `s3_iter1params_anystop` | base; Iteration 1 params; stop on any metric | 1.0 | 9 | 0.7759 | 0.0862 | 0.229 / 0.166 / 0.192 | 0.186 | 357 | 105 |
| 40 | `s3_iter1params_gfeat_anystop` | base+gfeat; Iteration 1 params; stop on any metric | 1.0 | 14 | 0.4933 | 0.1915 | 0.308 / 0.226 / 0.261 | 0.251 | 128 | 64 |
| 41 | `s3_reg_gfeat_seed42` | base+gfeat; seed 42 | 0.1 | 14 | 0.3812 | 0.2897 | 0.454 / 0.288 / 0.353 | 0.328 | 603 | 62 |
| 42 | `s3_reg_gfeat_seed1` | base+gfeat; seed 1 | 0.1 | 14 | 0.3554 | 0.2905 | 0.386 / 0.317 / 0.348 | 0.338 | 526 | 55 |
| 43 | `s3_reg_gfeat_seed2` | base+gfeat; seed 2 | 0.1 | 14 | 0.3365 | 0.2794 | 0.419 / 0.291 / 0.343 | 0.317 | 377 | 44 |
| 44 | `s3_reg_base_seed1` | base; seed 1 | 0.1 | 9 | 0.2645 | 0.2154 | 0.304 / 0.282 / 0.292 | 0.284 | 599 | 58 |
| 45 | `s3_reg_base_seed2` | base; seed 2 | 0.1 | 9 | 0.2791 | 0.2145 | 0.326 / 0.261 / 0.289 | 0.282 | 664 | 63 |
| 46 | `s4_all_no_kyc_neg100` | all minus kyc | 1.0 | 71 | 0.8053 | 0.5881 | 0.850 / 0.500 / 0.630 | 0.564 | 396 | 184 |

"Iteration 1 params" means `num_leaves=127, min_data_in_leaf=100, feature_fraction=0.8, lambda_l2=0`. `gfeat` is the five-column Iteration 1 graph contract read from the vectorized sidecar. Fit seconds include contention from concurrent jobs on the shared host.

## Feature-family ablation (validation)

Reference: all families at 10% negatives, validation AP 0.5966 (four seeds: mean 0.597, sd about 0.008). Base reference: 0.2195.

| Family | Leave-one-out val AP | Delta vs all | Add-to-base val AP | Delta vs base |
| --- | ---: | ---: | ---: | ---: |
| counterparty | 0.5425 | -0.0541 | 0.5031 | +0.2836 |
| amount | 0.5758 | -0.0208 | 0.4362 | +0.2167 |
| kyc | 0.5864 | -0.0102 | 0.2667 | +0.0472 |
| txlocal | 0.5922 | -0.0044 | 0.2321 | +0.0126 |
| velocity | 0.5939 | -0.0027 | 0.3021 | +0.0826 |
| cycle | 0.5966 | 0.0000 (inert) | 0.2195 | 0.0000 (inert) |
| recency/age columns only (8) | 0.5803 | -0.0163 | n/a | n/a |

Counterparty structure is the most valuable and least redundant family, followed by per-currency amount history. Velocity carries a lot of signal alone (+0.083 over base), but it is largely redundant with counterparty and amount. The txlocal and velocity leave-one-out deltas are within seed noise.

**Top C1 features by LightGBM gain share:**

| Feature | Gain share |
| --- | ---: |
| `payment_format` | 0.161 |
| `pair_mins_since_last` | 0.109 |
| `s_age_mins` | 0.071 |
| `r_in_n_1h` | 0.061 |
| `s_mins_since_last_out` | 0.060 |
| `s_cur_out_n_all` | 0.033 |
| `r_age_mins` | 0.028 |
| `s_entity_accounts` | 0.027 |
| `log_amount_paid` | 0.024 |
| `s_passthrough_ratio_7d` | 0.024 |
| `s_distinct_out_24h` | 0.021 |
| `log_amount_received` | 0.019 |

The Iteration 1 parameterization put 90% of gain on `sender_prior_out` (run 1).

## Why the Iteration 1 graph arm looked worse

Iteration 1 reported LightGBM + 5 graph features at test AP 0.1521 versus 0.1587 without them. The validation replication shows the following.

1. **The Iteration 1 configuration is unstable and memorizes.** With `num_leaves=127`, `min_data_in_leaf=100`, and no L2, base-only validation AP across seeds and stopping rules is 0.086, 0.122, 0.151, and 0.142, with train AP 0.78-0.99. The saved Iteration 1 model scores 0.1538 on the same validation matrix, inside that spread. A single run cannot resolve a 0.007 AP difference when run-to-run spread is about 0.065.
2. **On validation, the same graph columns help in every replicate.**
   - With Iteration 1 parameters, base+gfeat beats base for seeds 1, 2, and 3 (0.228 / 0.223 / 0.195 versus 0.122 / 0.151 / 0.142) and under the any-metric stopping rule (0.192 versus 0.086).
   - With regularized parameters, base+gfeat averages 0.287 over 3 seeds versus 0.215 for base.
3. **The richer counterparty family adds much more:** base+counterparty reaches 0.503. That family includes pair recency, pair counts, all-time and 7-day distinct counts, and reverse-flow recency, beyond the 24h counts and binary reverse flag.

Conclusion: the Iteration 1 graph result was model variance on one test look plus a feature set limited to 24h windows. The graph features themselves are not harmful. This replication was not re-run on test; the test-side answer is C1 versus C2 versus C3.

## Test ledger

All test evaluations run in Iteration 2, in order (`artifacts/runs/hi-small-v8/iteration-2-detection/test_ledger.jsonl`). There were no other test looks.

| # | UTC time | Entry | Kind | Test AP | Test F1 at val threshold | Recall @0.1% |
| ---: | --- | --- | --- | ---: | ---: | ---: |
| 1 | 2026-10-15 20:35 | `iteration1_lgbm_baseline` | Re-analysis of the frozen Iteration 1 test scores with the extended metric set; no training | 0.1587 | 0.2428 | 15.89% |
| 2 | 2026-10-16 13:22 | C1 `s1_all_reg_neg100` | Pre-declared candidate | 0.7525 | 0.7330 | 47.73% |
| 3 | 2026-10-16 13:26 | C2 `s4_all_no_kyc_neg100` | Pre-declared candidate | 0.7326 | 0.7201 | 47.53% |
| 4 | 2026-10-16 13:27 | C3 `s1_base_reg_neg010` | Pre-declared candidate | 0.2409 | 0.3034 | 21.40% |

## Final comparison versus Iteration 1

### Full test cohort (761,639 rows, 1,561 positives, prevalence 0.2050%)

| Model | Val AP | Test AP | Threshold | Alerts | TP | Precision | Recall | F1 | Budget TP / 762 | Budget precision | Budget recall | Budget alerts in Sept 11-18 tail |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Iteration 1 | 0.1538 | 0.1587 | 0.1555 | 1,627 | 387 | 23.79% | 24.79% | 0.2428 | 248 | 32.55% | 15.89% | 132 |
| C3 | 0.2195 | 0.2409 | n/a | 997 | 388 | 38.92% | 24.86% | 0.3034 | 334 | 43.83% | 21.40% | 173 |
| C2 | 0.5881 | 0.7326 | n/a | 1,194 | 992 | 83.08% | 63.55% | 0.7201 | 742 | 97.38% | 47.53% | 413 |
| **C1** | **0.6088** | **0.7525** | 0.4748 | 1,282 | 1,042 | 81.28% | 66.75% | 0.7330 | 745 | 97.77% | 47.73% | 409 |

### Sept 9-10 bulk (760,531 rows, 906 positives, prevalence 0.1191%; budget 761 alerts ranked within the bulk)

| Model | AP | Precision at val threshold | Recall | F1 | Budget TP / 761 | Budget precision | Budget recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Iteration 1 | 0.0877 | 13.22% | 20.64% | 0.1612 | 137 | 18.00% | 15.12% |
| C3 | 0.1503 | 24.47% | 21.52% | 0.2290 | 189 | 24.84% | 20.86% |
| C2 | 0.5744 | 72.57% | 51.10% | 0.5997 | 484 | 63.60% | 53.42% |
| **C1** | **0.5996** | **69.60%** | **53.31%** | **0.6038** | **495** | **65.05%** | **54.64%** |

### Sept 11-18 tail (1,108 rows, 655 positives)

At the validation threshold, Iteration 1 catches 200 tail positives with 213 tail alerts. C3 catches 193 with 200 alerts, C2 529 with 556, and C1 559 with 588. Per-day test AP on Sept 11-18 is 0.97-1.00 for C1 and 0.82-0.99 even for C3: the tail is near-separable for any model with history counts. Under the global 0.1% budget, 409 of C1's 762 alerts fall in the tail. Full-cohort budget recall is therefore strongly tail-driven; use the bulk table for review-capacity comparisons.

Per-day test AP for C1: Sept 9 0.5649, Sept 10 0.6376. Validation per-day AP for C1: Sept 7 0.6678, Sept 8 0.6165, partial Sept 9 0.3727. Bulk test AP (0.600) is consistent with validation AP (0.609); the higher full-cohort test AP comes from the tail.

## Per-currency amount-weighted recall at the 0.1% budget

Definition: see [Data and protocol](#data-and-protocol). Each value is a ratio within one payment currency; values are not comparable as money across rows and are never summed. Counts are alerted positives / all positives in that currency.

**Full test cohort, global top 762 alerts:**

| Payment currency | Positives | Iteration 1 | C3 | C2 | C1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| US Dollar | 595 | 0.005 (83) | 0.133 (117) | 0.540 (287) | 0.543 (269) |
| Euro | 406 | 0.004 (74) | 0.006 (101) | 0.675 (196) | 0.315 (206) |
| Saudi Riyal | 145 | 0.090 (52) | 0.418 (73) | 0.008 (39) | 0.412 (51) |
| Yen | 65 | 0.019 (8) | 0.011 (4) | 0.928 (33) | 0.687 (36) |
| Swiss Franc | 56 | 0.000 (0) | 0.007 (6) | 0.937 (34) | 0.935 (29) |
| Yuan | 43 | 0.002 (6) | 0.001 (6) | 0.502 (30) | 0.502 (29) |
| Australian Dollar | 42 | 0.000 (1) | 0.001 (1) | 0.014 (22) | 0.016 (24) |
| Rupee | 39 | 0.004 (5) | 0.003 (3) | 0.032 (24) | 0.977 (23) |
| Mexican Peso | 36 | 0.118 (3) | 0.074 (2) | 0.628 (17) | 0.675 (19) |
| Ruble | 34 | 0.259 (7) | 0.193 (5) | 0.542 (18) | 0.586 (19) |
| UK Pound | 29 | 0.004 (4) | 0.006 (6) | 0.012 (12) | 0.012 (12) |
| Canadian Dollar | 25 | 0.133 (3) | 0.402 (6) | 0.785 (13) | 0.553 (10) |
| Shekel | 19 | 0.000 (0) | 0.000 (2) | 0.001 (8) | 0.001 (8) |
| Brazil Real | 17 | 0.077 (2) | 0.128 (2) | 0.456 (9) | 0.525 (10) |
| Bitcoin | 10 | 0.000 (0) | 0.000 (0) | 0.000 (0) | 0.000 (0) |

**Sept 9-10 bulk, within-bulk top 761 alerts, C1 versus Iteration 1:**

| Currency | Positives | Iteration 1 | C1 |
| --- | ---: | ---: | ---: |
| US Dollar | 351 | 0.108 (37) | 0.974 (184) |
| Euro | 245 | 0.003 (45) | 0.765 (146) |
| Saudi Riyal | 73 | 0.918 (32) | 0.109 (49) |
| Yen | 29 | 0.237 (6) | 0.925 (20) |
| Yuan | 28 | 0.001 (3) | 0.501 (18) |
| Australian Dollar | 25 | 0.000 (1) | 0.007 (14) |
| Swiss Franc | 25 | 0.000 (0) | 0.683 (14) |
| Mexican Peso | 22 | 0.077 (1) | 0.870 (11) |
| Ruble | 20 | 0.240 (3) | 0.410 (8) |
| Rupee | 20 | 0.298 (6) | 0.683 (12) |
| UK Pound | 20 | 0.255 (2) | 0.573 (4) |
| Shekel | 16 | 0.000 (0) | 0.999 (6) |
| Canadian Dollar | 13 | 0.000 (0) | 0.695 (5) |
| Bitcoin | 10 | 0.000 (0) | 0.000 (0) |
| Brazil Real | 9 | 0.050 (1) | 0.228 (4) |

The Iteration 1 model caught 14% of US Dollar positives by count but only 0.5% of their paid amount; it found mostly small transfers. C1 catches large-value laundering far better in most currencies. A few weighted figures stay near zero despite count recall of 20-60%: Australian Dollar, UK Pound, and Shekel on the full cohort, and Saudi Riyal in C2. In those currencies, one or two very large laundering transfers dominate the denominator and are missed. These are high-variance ratios over few transactions. Bitcoin positives (10, all paid in Bitcoin) are never alerted by any model.

## Build, training, and scoring cost

| Step | Measurement |
| --- | --- |
| Feature build, 76 features x 5,078,345 rows | 177 s total (base 13.8, txlocal 0.7, velocity 31.6, amount 26.9, counterparty 59.1, cycle 4.1, kyc 1.6, Parquet write 16.6). DuckDB, 6 threads, 4 GB limit, offline batch. |
| Load train + validation matrices (81 columns, float32) | about 60 s; float32 matrices are about 1.4 GB (train) + 0.3 GB (validation); peak RSS was not instrumented |
| C1 fit (3,554,957 rows, 6 threads, 377 + 200 rounds) | 157 s in the grid, 195 s in the test run under contention |
| 10% negative sampling, all families | 44-171 s depending on rounds; validation AP within noise of full data |
| C1 batch scoring of the test cohort (prepared matrix, 6 threads) | 4.38 s for 761,639 rows (about 174,000 rows/s) |
| C3 batch scoring (9 features) | 2.48 s (about 307,000 rows/s) |
| Real-data truncation audit | about 3 min |
| Real-data perturbation audit | about 1-3 min per sampled minute (one partial rebuild each) |

**Online-scoring implications.** Scoring is cheap; feature state is the cost. Every family except `txlocal` and `kyc` needs per-account or per-pair state updated after each minute closes:

- `velocity` and `amount` need bounded 7-day ring buffers per account and per (account, currency).
- `counterparty` needs per-ordered-pair last-seen times and counts (about 1.0M pairs on this dataset), plus per-account expiring distinct sets.
- `cycle` needs neighbour indexes but contributes nothing here, so it can be dropped from serving.

All state updates must be applied after the whole minute is scored, to preserve the same-minute rule. The replay API serves precomputed scores, so none of this runs online today.

## Interpretation

- **What improved:**
  1. Regularization and negative sampling fixed the Iteration 1 overfit (train AP 0.78 to 0.24 on the base features, validation 0.086-0.15 to 0.22).
  2. Past-only behavioural and graph-structural history lifted validation AP to about 0.60. Pair recency, account age and recency, counterparty fan counts, per-currency amount z-scores, and pass-through ratios did most of the work.
- **What to discount:**
  - Full-cohort test numbers (AP 0.75, budget recall 47.7%) are inflated by the 1,108-row tail, which holds 42% of test positives and is nearly separable for every model.
  - Bulk AP 0.600 is the representative figure, and it agrees with validation.
  - Validation numbers are slightly optimistic because early stopping and selection share the validation split.
  - AMLworld is a simulator with scripted typologies. Strong recency and account-age signal may partly reflect generator regularities, for example how laundering accounts are scheduled, rather than behaviour that transfers to real data.
- **KYC registry:** removing the static account registry (C2) costs 0.020 test AP and 0.025 bulk AP. Most of the gain does not depend on it. It is treated as known at serving time because it is static account metadata, but account counts per entity come from the full registry snapshot, not a dated one.
- **Account age** means minutes since an account's first appearance in this dataset (which starts Sept 1), not true account age. Early train rows have truncated histories.
- **Graph value:** graph-structural features are now the strongest family for detection, in addition to their investigation role. This reverses the Iteration 1 conclusion. The reversal is backed by validation replicates and the C1/C2 versus C3 test comparison.

## Reproduction

Run from the repository root. Outputs go to the ignored artifact directory. The experiment runner is the `traceaml-experiment` console script in `ml/`.

```sh
RUN=artifacts/runs/hi-small-v8/iteration-2-detection
DATA=data/processed/hi-small-v8

uv run --directory ml traceaml-experiment features \
  --source-parquet ../$DATA/transactions.parquet \
  --split-manifest ../$DATA/split-manifest.json \
  --accounts-csv ../data/raw/hi-small/HI-Small_accounts.csv \
  --output-dir ../$RUN/features

uv run --directory ml traceaml-experiment validate \
  --features-dir ../$RUN/features --prepared-parquet ../$DATA/transactions.parquet \
  --graph-features ../$DATA/graph-features-batch.parquet \
  --grid experiments/iteration2_grid.json --output-dir ../$RUN --skip-done

# Pre-declared candidates only (ml/experiments/iteration2_candidates.json); each appends to the test ledger.
uv run --directory ml traceaml-experiment test \
  --features-dir ../$RUN/features --prepared-parquet ../$DATA/transactions.parquet \
  --grid experiments/iteration2_grid.json --output-dir ../$RUN --candidate s1_all_reg_neg100

uv run --directory ml traceaml-experiment truncation-check \
  --features-dir ../$RUN/features --prepared-parquet ../$DATA/transactions.parquet \
  --cutoff "2022-09-09 03:16"
uv run --directory ml traceaml-experiment perturbation-check \
  --features-dir ../$RUN/features --prepared-parquet ../$DATA/transactions.parquet \
  --cutoff "2022-09-09 03:16" --sample-minutes 3

uv run --directory ml traceaml-experiment publish \
  --features-dir ../$RUN/features --output-dir ../$RUN --candidate s1_all_reg_neg100
```

## Artifacts (local, Git-ignored)

Under `artifacts/runs/hi-small-v8/iteration-2-detection/`:

- `features/features.parquet`, `features/features-manifest.json` (families, columns, timings, null counts, hashes, serving-state notes)
- `features/truncation-check.json`, `features/perturbation-check.json`, `features/perturbation-check-seed7-partial.jsonl`
- `validation_runs.jsonl` (46 records), `test_ledger.jsonl` (4 records)
- `candidates/<name>/` with `model.txt`, `config.json` (threshold, category mappings, hashes), `metrics.json`, `test_scores.parquet` (offline, includes labels), and `validation_scores.parquet`
- `inference_scores.parquet` (`transaction_id`, `timestamp`, `score`; 761,639 rows; no labels), `inference_scores-manifest.json`, and `api_threshold_config.json` (`thresholds.lgbm = 0.474807`)

To switch the replay API to the Iteration 2 model, set `TRACEAML_SCORE_ARTIFACT` to the new `inference_scores.parquet` and `TRACEAML_BASELINE_CONFIG_PATH` to `api_threshold_config.json`. Leave `TRACEAML_SCORE_MODEL=lgbm`.
