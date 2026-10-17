# Iteration 2 graph performance

Measured 2026-10-16 on the local development machine: Intel Core i5-13400F, 16 logical CPUs, 15.8 GiB RAM, Windows 11. FalkorDB module 4.22.1 ran in Docker Desktop with a 7.67 GiB VM limit. The iteration-1 report names a Ryzen 9 8945HS; the benchmark hardware string (read from the OS) reports the Intel CPU above. Iteration-1 figures are therefore quoted as published, and every "before" column below was re-measured on this machine with the frozen iteration-1 statements (`--implementation legacy`).

Two datasets are used:

- **Real data:** IBM AMLworld HI-Small v8, itself synthetic AML data. Labels are never loaded: the loader's column allowlist excludes `target_is_laundering` and `split`, and `CALL db.propertyKeys()` on the loaded graph lists only allowlisted properties.
- **Synthetic latency fixture:** `traceaml-synthetic-fixture`, seeded and label-free. Its numbers describe query and load mechanics on graphs of similar size and degree skew. They are not claims about HI-Small.

FalkorDB was shared with other agents during these runs. All figures are serial, single-client latencies; none is a concurrency or capacity test.

## Protocol

`traceaml-graph-bench` selects anchors from the graph:

- the iteration-1 case (`--anchor-transactions hi8-tx-03863669`) on real data;
- the latest transfer of the top 3 out-degree and top 3 in-degree accounts (`high`);
- 6 transactions at seeded uniform offsets (`typical`).

Each anchor runs the API's five investigation calls: `trace_funds` (both directions, 2 hops), `inspect_counterparties` by transaction, `fan_out` at the sender, and `fan_in` and `pass_through` at the receiver. Every call uses an 86,400-second window, a 50-result limit, and the default 5-second timeout (30 seconds for legacy runs). The stress tools are a 3-hop trace and an account-anchored inspect. `investigation` is the serial sum of the five API calls. The metric is each tool's `query_duration_ms`, which covers all of its database calls.

Current runs used 2 warmups and 10-12 samples per anchor and tool. Legacy runs used 1 warmup and 3-5 samples, because one legacy call takes up to seconds; the case-only legacy run used 12 samples.

## Root causes

1. **Planner picked the timestamp index.** With a `Transaction.timestamp_epoch` range index present, account-anchored statements started from a range scan over every transaction in the window (528,982 rows for one day of a 1M graph), traversed back to the account, and then filtered. This happened even for accounts with five transfers, so a zero-match pattern still cost about a second.
2. **`UNION ... ORDER BY ... LIMIT` binds to the last branch only.** `TRACE_ACCOUNT_LATEST_EPOCH` returned an arbitrary sent epoch rather than the latest one (wrong for 497 of 500 accounts on the equivalence fixture). Account windows ended too early and missed transfers. The same binding meant `trace_funds` limited only its last branch: the legacy 3-hop case trace returned 3,404 paths for a limit of 50.
3. **Pass-through cross product.** The two-leg join materialized every (incoming, outgoing) pair through the middle account before ordering and limiting.
4. **Per-transfer second expansion.** Fan gates traversed to every counterparty node only to read its key, which the transaction node already stores.

## Results: real two-day demo slice

The slice reproduces iteration 1 exactly: transactions within +/- 86,400 s (inclusive) of `hi8-tx-03863669`, giving 998,021 transactions and 354,660 accounts (`make graph-load-demo`). The load took 63.1 s in create mode, including 8.0 s for the account phase. Graph memory was 561 MB (`GRAPH.MEMORY USAGE`), or 568 MB with the iteration-1 timestamp indexes added back for the legacy runs.

Iteration-1 case, `hi8-tx-03863669` (ms):

| Tool | Iteration 1 published P50 | Legacy rerun P50 / P95 | Current P50 / P95 |
| --- | ---: | ---: | ---: |
| `trace_funds` (2 hops) | 5.5 | 26.8 / 39.8 | 11.4 / 17.6 |
| `inspect_counterparties` | 1.2 | 5.2 / 10.9 | 4.8 / 8.5 |
| `fan_out` | 1,728 | 23.2 / 66.1 | 3.2 / 6.8 |
| `fan_in` | 891 | 955 / 5,452 | 3.0 / 5.0 |
| `pass_through` | 923 | 992 / 3,958 | 2.4 / 3.2 |
| Investigation (sum of five) | 3,481 | 1,956 / 7,540 | 24.5 / 36.6 |
| `trace_funds` (3 hops, stress) | n/a | 1,571 / 2,063 | 61.1 / 67.4 |

Run alone, the case's 2-hop trace measured 6.1 ms current vs 7.5 ms legacy. The current 11.4 ms comes from the full run, where it is interleaved with hub-anchor calls. The iteration-1 fan-out figure was measured with a different arbitrary window end (root cause 2), so it does not correspond to the legacy rerun.

Evidence for the case:

| Tool | Current vs legacy | Detail |
| --- | --- | --- |
| `trace_funds` | identical | 65 paths, 15 transactions |
| `inspect_counterparties` | identical | 17 records |
| `fan_out` | changed | legacy window ended 31.7 h before the sender's latest activity: 2 distinct counterparties, no match; corrected window: 7 counterparties, 7 matches |
| `fan_in`, `pass_through` | unchanged | no matches at the receiver in either |

All anchors (case + 6 high + 6 typical; top hub degree 36,114) (ms):

| Tool | Legacy P50 / P95 | Current P50 / P95 | Current high-degree P95 |
| --- | ---: | ---: | ---: |
| `trace_funds` | 9.1 / 971 | 9.1 / 42.2 | 43.3 |
| `inspect_counterparties` | 4.6 / 75.7 | 4.8 / 40.0 | 48.1 |
| `fan_out` | 291 / 1,417 | 2.9 / 43.2 | 52.3 |
| `fan_in` | 809 / 1,344 | 3.1 / 7.6 | 8.4 |
| `pass_through` | 729 / 1,393 | 3.1 / 56.3 | 58.9 |
| Investigation (sum) | 2,296 / 4,736 | 28.6 / 124 | 135 |
| `inspect_counterparties` by account (stress) | 576 / 3,090 | 5.0 / 38.2 | 42.7 |
| `trace_funds` 3 hops (stress) | 60.7 / 1,067 | 57.4 / 106 | 113 |

## Results: synthetic 1M fixture

`demo-1m` preset: 1,000,000 transactions over 2 days and 351,992 active accounts. The maximum out-degree is 10,322 and the median active degree is 3. Graph memory was 576 MB with the iteration-1 loader and schema, and 552 MB after this iteration.

The iteration-1 loader took 130.1 s (batch 1,000, MERGE per row). Create mode took 89.9 s (accounts 9.9 s, batch 5,000).

Latency, all anchors (ms):

| Tool | Legacy P50 / P95 | Current P50 / P95 | Current high-degree P95 |
| --- | ---: | ---: | ---: |
| `trace_funds` | 19.9 / 943 | 11.8 / 27.1 | 28.8 |
| `inspect_counterparties` | 6.2 / 76.7 | 4.5 / 26.1 | 29.7 |
| `fan_out` | 57.6 / 1,087 | 3.3 / 26.2 | 28.4 |
| `fan_in` | 380 / 3,597 | 3.2 / 27.1 | 30.7 |
| `pass_through` | 313 / 1,501 | 5.6 / 35.0 | 39.9 |
| Investigation (sum) | 1,512 / 5,450 | 36.8 / 90.5 | 98.9 |
| `inspect_counterparties` by account (stress) | 96.4 / 2,610 | 5.2 / 31.8 | 34.9 |
| `trace_funds` 3 hops (stress) | 873 / 1,880 | 62.1 / 86.7 | 92.6 |

Every tool the API calls is under the 50 ms P95 target on this fixture. The 3-hop trace is not; it is an opt-in depth whose cost is the number of 3-hop paths around hubs.

## Full-scale load

Both graphs were loaded with `--mode create` and the default batch of 5,000. Memory is `GRAPH.MEMORY USAGE`; the peak is the server's `used_memory_peak` while the other agents' graphs were also resident.

| Graph | Transactions | Accounts | Load time | Graph memory | Server peak |
| --- | ---: | ---: | ---: | ---: | ---: |
| HI-Small v8, full (real) | 5,078,345 | 515,088 | 520.9 s (accounts 38.2 s) | 2,514 MB | 4.13 GiB |
| Synthetic `full-5m` | 5,078,345 | 549,907 | 507.4 s (accounts 20.8 s) | 2,402 MB | 3.93 GiB |
| HI-Small v8, demo slice (real) | 998,021 | 354,660 | 63.1 s | 561 MB | n/a |

Iteration 1 projected about 7 GiB for the full graph from a 500k prefix (706 MiB). The measured full graph is 2,514 MB, roughly 500 bytes per transaction. Its largest components are Transaction attributes (1,175 MB), the two lookup indexes (734 MB), and relation matrices (332 MB). Load throughput falls from about 15.8k rows/s on the slice to about 9.7k rows/s at full scale. All transaction, `SENT`, and `RECEIVED` counts reconciled. `CALL db.propertyKeys()` listed only the allowlisted properties.

## Latency at full scale (real, 5,078,345 transactions)

The top hubs have degree 169,756 (sender) and 103,671; typical anchors have degree 27-203 (ms).

| Tool | Case P50 / P95 | All anchors P50 / P95 | High-degree P95 | Typical P95 |
| --- | ---: | ---: | ---: | ---: |
| `trace_funds` | 11.9 / 18.0 | 6.0 / 115 | 133 | 51.5 |
| `inspect_counterparties` | 3.9 / 8.1 | 3.9 / 81.6 | 86.5 | 12.8 |
| `fan_out` | 2.4 / 3.7 | 1.6 / 45.1 | 53.9 | 3.0 |
| `fan_in` | 1.8 / 2.8 | 1.6 / 4.0 | 5.0 | 2.6 |
| `pass_through` | 2.2 / 3.4 | 1.6 / 56.9 | 59.2 | 5.0 |
| Investigation (sum) | 23.2 / 29.3 | 36.4 / 189 | 207 | 64.8 |
| `inspect_counterparties` by account (stress) | 4.6 / 6.0 | 2.8 / 45.9 | 53.0 | 4.5 |
| `trace_funds` 3 hops (stress) | 63.1 / 72.6 | 49.6 / 222 | 238 | 151 |

Synthetic `full-5m` (top hub degree 51,866; the server was busy with another workload when this run started) (ms):

| Tool | All anchors P50 / P95 | High-degree P95 | Typical P95 |
| --- | ---: | ---: | ---: |
| `trace_funds` | 5.6 / 39.1 | 16.1 | 45.3 |
| `inspect_counterparties` | 5.3 / 70.6 | 73.8 | 29.5 |
| `fan_out` | 3.7 / 44.6 | 48.7 | 10.5 |
| `fan_in` | 2.6 / 38.3 | 41.1 | 3.4 |
| `pass_through` | 2.8 / 5.8 | 5.3 | 5.8 |
| Investigation (sum) | 49.7 / 125 | 136 | 80.3 |

Per-call cost now grows with the anchor's degree, not with graph size: the case and typical anchors stay in single-digit milliseconds per tool at 5M. The remaining cost above 50 ms comes from anchors whose accounts have tens of thousands of transfers. Every statement expands the anchor's full adjacency and reads each transfer's epoch (about 1 microsecond per edge). Avoiding that would need a time-ordered adjacency or per-account time buckets, which are not implemented.

## Rebuilding the measured graphs

Graphs were deleted after measurement. Rebuild with:

```sh
# Real two-day slice and full graph (HI-Small v8 Parquet from ml prepare)
uv run --project graph traceaml-load-graph --parquet data/processed/hi-small-v8/transactions.parquet \
  --graph-name agentgraph-real-demo --mode create --window-around hi8-tx-03863669 --window-seconds 86400
uv run --project graph traceaml-load-graph --parquet data/processed/hi-small-v8/transactions.parquet \
  --graph-name agentgraph-real-full --mode create

# Synthetic fixtures
uv run --project graph traceaml-synthetic-fixture --preset demo-1m
uv run --project graph traceaml-synthetic-fixture --preset full-5m
uv run --project graph traceaml-load-graph --parquet data/interim/synthetic/demo-1m/transactions.parquet \
  --graph-name agentgraph-opt-1m --mode create

# Benchmarks (JSON under artifacts/benchmarks/)
uv run --project graph traceaml-graph-bench --graph-name agentgraph-real-demo \
  --anchor-transactions hi8-tx-03863669 --samples 12
uv run --project graph traceaml-graph-bench --graph-name agentgraph-real-demo \
  --anchor-transactions hi8-tx-03863669 --implementation legacy --warmup 1 --samples 3 --timeout-ms 30000
```

The legacy "before" runs need the iteration-1 schema. For the synthetic graph, load it with the iteration-1 loader. For the real slice, add the two dropped indexes to the slice graph: `CREATE INDEX FOR (t:Transaction) ON (t.timestamp_epoch)` and `CREATE INDEX FOR (t:Transaction) ON (t.timestamp)`.

## Limits

- All figures are serial, single-client latencies on a shared FalkorDB, not concurrency or capacity results. Re-run the protocol before comparing future changes.
- Iteration-1 published figures came from the hardware named in that report; the "before" columns here are same-machine reruns.
- Hub-anchored calls on the full graph exceed 50 ms (P95 up to 133 ms for `trace_funds` and 87 ms for `inspect_counterparties`).
- The 3-hop trace is an opt-in stress depth and is not under 50 ms at any scale.

## Rejected alternative

Copying `timestamp_epoch` onto `SENT`/`RECEIVED` edges and filtering on the edge property was slower than filtering on the node property: 15.7 ms vs 11.2 ms median over 8,808 edges. Dropping the `:Transaction` label check on expanded nodes cut the traversal itself from 7.7 to 2.0 ms.
