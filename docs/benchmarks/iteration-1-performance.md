# Iteration 1 benchmark results

Measured 2026-10-16 on the local development machine. These results use synthetic IBM AMLworld HI-Small v8 data and the frozen chronological test cohort. They describe this implementation and hardware, not real-world AML performance.

## Environment and scope

- CPU: AMD Ryzen 9 8945HS, 8 cores / 16 threads.
- Memory: 14 GiB system RAM. FalkorDB used about 1.48 GiB after loading the demo graph.
- Model: LightGBM with 9 tabular and past-only behavioral features; the graph arm adds five point-in-time graph features.
- Frozen test cohort: 761,639 transactions. Both arms used the same split, validation threshold selection, and 0.1% test alert budget.
- Investigation graph: target-free two-day slice with 998,021 transactions, 354,660 accounts, 998,021 `SENT` edges, and 998,021 `RECEIVED` edges. This is not the full 5,078,345-transaction graph.
- Investigation case: `hi8-tx-03863669`, 2 hops, 86,400-second window, and 50-result limit.

## Detection results

| Model | Test average precision | Test precision / recall / F1 at validation threshold | Recall at 0.1% alert budget |
| --- | ---: | ---: | ---: |
| LightGBM baseline | 0.1587 | 23.79% / 24.79% / 0.2428 | 15.89% (248 / 1,561 positives) |
| LightGBM + graph features | 0.1521 | 15.04% / 24.73% / 0.1871 | 14.73% (230 / 1,561 positives) |

The graph features did not improve this benchmark. Average precision fell by 0.0067, and recall at the fixed alert budget fell by 1.15 percentage points. Keep tabular LightGBM as the detection baseline. Graph value in this iteration is the investigation workflow, not a better classifier score.

## Temporal label distribution

Training prevalence is 0.0803%, validation prevalence is 0.0998%, and total test prevalence is 0.2050%. The daily test report shows that the test period is heavily imbalanced over time: September 9–10 contain 760,531 transactions and 906 positives (0.1191%); September 11–18 contain only 1,108 transactions but 655 positives (59.12%). The final tail is a small fraction of test rows but contains about 42% of test positives. September 18 has 8 positives, below the daily report's 10-positive minimum for model metrics.

This distribution shift limits the meaning of one aggregate test score. Preserve the chronological split and report the day-level counts alongside aggregate metrics. The observed shift is a property of this synthetic dataset and is not evidence of real transaction prevalence.

## Reproducibility and leakage checks

A fresh baseline run into `artifacts/runs/hi-small-v8/repro-check/` matched the primary run byte for byte for `baseline_metrics.json`, `test_scores.parquet`, `models/lgbm.txt`, and `models/logreg.joblib`. The `baseline_config.json` includes a run timestamp, so that file is compared by configuration rather than file hash. The test-score artifact hash was `3442807e78b2cdd94b3f2fa14e54dc811b1f6f02260267dd9b8070543efb98d2` in both runs.

The ML suite passed 17 tests, including target exclusion and same-minute/future-activity leakage checks. The graph suite passed 46 tests, including feature-stream timing and inference-property allowlists. The API suite passed 21 tests, including target-free response validation and the benchmark performance contract. The web lint and production build passed.

The feature sidecar contains all 5,078,345 transaction IDs and five target-free columns. Extraction took 1,563 seconds (26 minutes), including 1.3 seconds for sorting and 1,561.8 seconds for graph queries and writes. The largest same-minute batch contained 11,193 rows. The builder excluded target and split columns and processed complete same-minute groups before updating graph state.

## Scoring and API latency

Model inference timings use the trained LightGBM booster with one CPU thread and an already prepared numeric matrix. They exclude feature retrieval, categorical encoding, graph access, and API serialization. Twenty single-row warmups were run. Held-out predictions matched the frozen score artifact exactly, with maximum absolute difference 0.0.

| Workload | Repeats | P50 | P95 | Throughput |
| --- | ---: | ---: | ---: | ---: |
| One prepared transaction, LightGBM only | 200 | 0.029 ms | 0.041 ms | — |
| Batch of 100, LightGBM only | 100 | 1.046 ms | 1.330 ms | — |
| Batch of 1,000, LightGBM only | 50 | 11.441 ms | 12.774 ms | — |
| Batch of 10,000, LightGBM only | 20 | 72.446 ms | 77.624 ms | — |
| Entire held-out cohort, 761,639 rows | 3 | 5.004 s median | — | 152,214 transactions/s |
| `GET /api/alerts?limit=50` | 30 after 5 warmups | 1.087 ms | 1.720 ms | 874 requests/s, serial |
| `GET /api/benchmarks` | 30 after 5 warmups | 1.082 ms | 1.954 ms | 806 requests/s, serial |

The replay API serves precomputed scores. The raw LightGBM timings are a separate model inference measurement; the API does not run the model for each request.

## FalkorDB query and investigation latency

Direct query measurements used 2 warmups and 12 serial samples per operation, with a 5-second query timeout. API investigation measurements used 3 warmups and 12 measured HTTP requests over loopback. Each report passed deterministic evidence validation and returned the same 25 transactions, 13 accounts, and 75 paths.

| Operation | Query P50 | Query P95 |
| --- | ---: | ---: |
| `trace_funds` | 5.46 ms | 6.65 ms |
| `inspect_counterparties` | 1.19 ms | 1.64 ms |
| `detect_flow_patterns` fan-out | 1,728 ms | 1,841 ms |
| `detect_flow_patterns` fan-in | 891 ms | 943 ms |
| `detect_flow_patterns` pass-through | 923 ms | 977 ms |
| Full `POST /api/investigations` request | 3,487 ms | 3,582 ms |
| Graph query time summed inside that API request | 3,481 ms | 3,576 ms |

The fan-pattern scans dominate investigation latency. The measured serial rate was 0.286 investigation requests per second for this case and query configuration. This is not a concurrent capacity test. Fan-in and pass-through returned no matches for the selected account but still took about 0.9 seconds each.

## Cost and limits

- No external LLM provider was called. The current report is deterministic, so this run incurred no LLM token charges.
- Cost per 1,000 scored transactions and cost per investigation are not priced. Electricity, hardware depreciation, and future hosted-service charges were not available as inputs.
- Dollar-weighted recall is not reported. The data carries paid and received values in potentially different currencies, and no valuation basis has been selected.
- API and query throughput figures are serial loopback measurements without concurrency. They are not production capacity claims.
- The transaction-evidence graph contains only the two-day demo slice. These query timings do not establish full-graph latency or capacity.
- Feature extraction is an offline batch measurement, not online scoring latency.

## Local run artifacts

Generated run artifacts are Git-ignored under `artifacts/runs/hi-small-v8/`:

- `graph-ablation/baseline_metrics.json` and `graph-ablation/baseline_config.json` contain the same-cohort graph comparison and configuration.
- `latency/model-inference.json` contains raw booster timings and score parity.
- `latency/api.json` contains HTTP latency, serial request rates, and evidence counts.
- `latency/graph-queries.json` contains per-query internal and client-wall timings.
- `latency/benchmarks-endpoint.json` contains benchmark endpoint timings.

Rebuild the graph feature sidecar and model ablation with:

```bash
uv run --project graph traceaml-build-graph-features \
  --parquet data/processed/hi-small-v8/transactions.parquet \
  --features-out data/processed/hi-small-v8/graph-features.parquet \
  --manifest-out data/processed/hi-small-v8/graph-features-manifest.json \
  --feature-graph traceaml-features

uv run --project ml traceaml-train-baselines \
  --graph-features data/processed/hi-small-v8/graph-features.parquet \
  --output-dir artifacts/runs/hi-small-v8/graph-ablation
```

The local latency artifacts preserve the sample summaries for this run. Repeat the same workload and hardware protocol before comparing future service changes.

The versioned dashboard snapshot is `docs/benchmarks/iteration-1-performance.json`. The API reads it through `TRACEAML_PERFORMANCE_PATH`; raw transaction data and generated run artifacts remain local and Git-ignored.
