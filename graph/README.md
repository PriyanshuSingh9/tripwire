# TraceAML Graph (`graph`)

uv-managed FalkorDB graph layer. Python >= 3.12. All commands run from the
repository root with `uv run --project graph`. It covers idempotent
transaction-graph ingestion and the bounded investigation queries
(`trace_funds`, `inspect_counterparties`, `detect_flow_patterns`).

This package never reads `target_is_laundering`, `split`, or pattern metadata.
Its Parquet projection is an explicit allowlist, and all command output is
aggregate-only (counts, timings, IDs never printed as row samples).

## Setup

```sh
uv sync --project graph
```

## Loader

Batch-loads first-class transfers:

```text
(:Account)-[:SENT]->(:Transaction)-[:RECEIVED]->(:Account)
```

```sh
uv run --project graph traceaml-load-graph --parquet data/processed/hi-small-v8/transactions.parquet
```

Representative smoke load (file-order prefix, for memory/runtime probing only,
never an evaluation sample):

```sh
uv run --project graph traceaml-load-graph --parquet data/processed/hi-small-v8/transactions.parquet --limit 1000
```

Validate without a database:

```sh
uv run --project graph traceaml-load-graph --parquet data/processed/hi-small-v8/transactions.parquet --limit 5000 --dry-run
```

Loader guarantees:

- Row-derived transaction IDs (`hi8-tx-{source_row_number:08d}`) are preserved,
  so the 9 exact duplicate source rows become 9 distinct `Transaction` nodes.
- Account keys are validated as `{bank}::{account}` with both components kept
  as strings (leading zeroes preserved); rows with empty components, embedded
  separators, or key mismatches abort the load with the offending
  `source_row_number` only.
- `--mode merge` (default): each batch de-duplicates its accounts client-side,
  upserts them once (`UNWIND $accounts`, `last_epoch` only ever increases),
  then `MERGE`s transactions and edges. Re-running creates no duplicate nodes
  or relationships.
- `--mode create`: fresh-load fast path. It refuses to run unless the graph has
  no `Transaction` nodes, creates every distinct account once with its final
  `last_epoch` (Arrow group-by over the account and timestamp columns only),
  then `CREATE`s transactions and edges without existence checks. The unique
  constraints still reject duplicate IDs.
- `--window-around TX --window-seconds S` loads only rows whose timestamp is
  within `S` seconds (inclusive) of `TX`'s timestamp. The iteration-1 demo
  slice is `--window-around hi8-tx-03863669 --window-seconds 86400`
  (998,021 rows, 354,660 accounts); `make graph-load-demo` runs it.
- Stored properties are an explicit allowlist. `Transaction`: `transaction_id`,
  `timestamp`, `timestamp_epoch`, `sender_account_key`, `receiver_account_key`,
  amounts, currencies, and `payment_format`. `Account`: `account_key`, `bank`,
  `account_id`, and `last_epoch` (latest sent-or-received epoch).
  `source_row_number` is not stored; it is recoverable from the row-derived ID.
- Only `Account.account_key` and `Transaction.transaction_id` get range
  indexes, created before their unique constraints through the supported
  client API (`create_node_range_index`, then `create_node_unique_constraint`).
  Transaction timestamp indexes are intentionally absent (ADR-017).
- `BATCH_CYPHER` is a self-contained single-statement equivalent of merge
  mode for small direct loads (for example the API integration test).
- Count reconciliation compares ingested rows against graph-side
  `Transaction`/`SENT`/`RECEIVED` counts and fails on mismatch.
- The summary reports rows, batches, elapsed time, peak memory, and
  reconciliation counts as JSON.

FalkorDB must already be running (`docker compose up -d falkordb` from the
repository root). Connection defaults: host `localhost`, port `6379`, graph
`traceaml`, overridable with `--host`, `--port`, `--graph-name` or the
`TRACEAML_FALKORDB_HOST` / `TRACEAML_FALKORDB_PORT` environment variables.

## Bounded investigation queries

`src/graph/queries.py` builds the three approved tools; `queries.cypher`
holds the same parameterized statements as a declarative reference. Every
query declares explicit bounds: hop depth 1-3, time window capped at 30 days,
row limit 1-100, and a client-side execution timeout (default 5 s, max 30 s)
passed as `timeout=` milliseconds to `graph.query`. Results carry source
transaction IDs, timestamps, amounts with their own currencies, account keys,
paths, and `query_duration_ms`.

Query shape (ADR-017): every anchored statement binds the indexed account or
transaction first and expands only that node's adjacency, so cost grows with
the anchor's degree, not with graph size or window volume.

- `fan_out`/`fan_in`: one statement returns the distinct-counterparty count,
  the transfer count, and the earliest `result_limit` transfers when the
  threshold is met.
- `pass_through`: an outgoing epoch histogram, then the first `result_limit`
  qualifying incoming legs, then a detail fetch at only the needed epochs.
  This avoids the incoming x outgoing cross product.
- `trace_funds`: `result_limit` applies per (direction, depth) branch.
- Account windows end at `Account.last_epoch`.

Unknown anchors raise `TransactionNotFound` / `AccountNotFound`. Both are
`ValueError` subclasses and keep the iteration-1 messages.
`src/graph/legacy_queries.py` freezes the iteration-1 statements for
equivalence tests and `--implementation legacy` benchmarks.

## Synthetic latency fixture

`traceaml-synthetic-fixture` writes a seeded Parquet file with the normalized
`transactions.parquet` columns and dtypes. Its properties:

- `{bank}::{account}` keys, with leading-zero bank IDs and hex account IDs.
- Heavy-tailed degrees (uniform base plus a Zipf-like tail), repeated pairs,
  and self "Reinvestment" transfers.
- Minute timestamps, 15 currencies with about 3% cross-currency transfers, and
  seven payment formats.
- Planted fan-out, fan-in, and 1-3-hop pass-through structures, so pattern
  queries return matches.

It carries no labels: `target_is_laundering` and `split` are all null, and
planted-structure membership is never written. It is a synthetic latency
fixture, so its numbers are not claims about HI-Small or about detection
quality.

```sh
uv run --project graph traceaml-synthetic-fixture --preset demo-1m   # 1,000,000 rows, 2 days
uv run --project graph traceaml-synthetic-fixture --preset full-5m   # 5,078,345 rows, 18 days
```

Output goes to `data/interim/synthetic/{preset}/transactions.parquet` with a
`manifest.json` of aggregate shape statistics (Git-ignored). `make
graph-synth` (`traceaml-synth-transactions`) is a separate generator for the
feature builders; it emits random noise labels and plants no flow structures.

## Latency benchmark

`traceaml-graph-bench` runs a warmup + N-sample protocol for every tool
against a named graph. It writes JSON under `artifacts/benchmarks/`
(Git-ignored) with P50/P95/mean/max per tool and per anchor class, the raw
samples, graph size and memory (`GRAPH.MEMORY USAGE`, `INFO memory`), the
FalkorDB module version, and a hardware string.

Anchors are chosen deterministically from the graph, never from labels:

- `high`: the latest transfer of each top out-degree and in-degree account.
- `typical`: transactions at seeded uniform offsets.
- `case`: explicit IDs from `--anchor-transactions`.

Each anchor runs the API's investigation calls (`trace_funds` both directions,
`inspect_counterparties` by transaction, `fan_out` at the sender, and `fan_in`
plus `pass_through` at the receiver). It also runs a 3-hop trace and an
account-anchored inspect as stress tools. `investigation` is the serial sum of
the five API calls. Output is aggregate-only unless `--record-anchors` is
passed (synthetic fixtures only).

```sh
uv run --project graph traceaml-graph-bench --graph-name agentgraph-real-demo \
  --anchor-transactions hi8-tx-03863669 --dataset-label "HI-Small v8 demo slice"
make graph-bench BENCH_GRAPH=agentgraph-real-demo BENCH_ARGS="--anchor-transactions hi8-tx-03863669"
uv run --project graph traceaml-graph-bench --graph-name agentgraph-baseline-1m --implementation legacy
```

FalkorDB is shared, so take medians over repeated runs and re-measure
suspicious numbers. Results and rebuild commands are in
`docs/benchmarks/iteration-2-graph-performance.md`.

## Tests

```sh
uv run --directory graph pytest -q
uv run --directory graph ruff check .
```

Unit tests use an in-memory fake graph client at the `graph.query` seam and
never need Docker or the dataset. The live tests (`test_equivalence_live.py`
and the FalkorDB feature parity test) skip automatically when FalkorDB is
unreachable. When it is reachable, they build seeded synthetic graphs named
`agentgraph-equiv-*` and drop them afterwards. The equivalence tests prove the
optimized tools return the same records as the frozen iteration-1 statements.

## Point-in-time graph features

Two builders produce the same target-free sidecar (`transaction_id` plus
five `gfeat_*` int64 columns) with row-for-row identical values:

- `traceaml-build-graph-features-batch` (default, `make graph-features`):
  vectorized offline build from the Parquet projection. No database needed.
- `traceaml-build-graph-features` (cross-check,
  `make graph-features-falkordb`): streams minute groups through a FalkorDB
  feature graph.

### Vectorized builder (default)

```sh
uv run --project graph traceaml-build-graph-features-batch \
  --parquet data/processed/hi-small-v8/transactions.parquet \
  --features-out data/processed/hi-small-v8/graph-features.parquet \
  --manifest-out data/processed/hi-small-v8/graph-features-manifest.json
```

Same CLI shape as the FalkorDB builder (`--limit`, `--window-seconds`,
`--spill-dir`). DuckDB reads only the four allowlisted columns ordered by
`(timestamp, transaction_id)`. NumPy then deduplicates transfers to
pair-minute events. Event `i` of pair `(a, b)` contributes to query minutes `e`
in `(t_i, min(next_i, t_i + window)]`, so each distinct-counterparty count is
`#{starts < e} - #{ends < e}` over account-major sorted keys (two binary
searches). Prior reverse flow compares the first `b -> a` transfer minute
with `e`. Semantics shared with the FalkorDB builder:

- Group epoch is the timestamp truncated to the minute; naive timestamps are UTC.
- Rows see only transfers in strictly earlier minutes; same-minute peers are
  mutually invisible.
- Recent-eligible: the pair's latest earlier transfer minute is
  `>= group_epoch - window` (inclusive lower bound).
- Reverse flow is all-time prior (not windowed).
- Self-transfers are ordinary pairs: an account counts as its own out- and
  in-counterparty.
- Duplicate rows each get an output row.
- `--limit` drops the final, possibly partial minute.

The batch builder rejects null projected values instead of coercing them.
The manifest records the source sha256, row reconciliation, distinct
minutes/accounts, window, semantics, phase timings, and peak process memory.

Synthetic benchmark on the local machine. The data is random with Zipf-like degrees and is
not HI-Small:

| Input | FalkorDB builder | Batch builder |
| --- | ---: | ---: |
| 200,000-row prefix | 130.1 s | 1.1 s (identical output) |
| 5,078,345 rows, 18 days | not run (Iteration 1 real data: 1,563 s) | 11.3-13.2 s, 1.4 GiB peak |

Reproduce with:

```sh
uv run --project graph traceaml-synth-transactions --out data/interim/synthetic/hi-small-shape.parquet
uv run --project graph traceaml-build-graph-features-batch \
  --parquet data/interim/synthetic/hi-small-shape.parquet \
  --features-out data/interim/synthetic/bench/batch.parquet \
  --manifest-out data/interim/synthetic/bench/batch.json
```

Parity tests: `tests/test_feature_batch.py` compares the batch builder with
a brute-force evaluation of the semantics and with the streaming builder on an
in-memory fake graph. `tests/test_feature_parity.py` compares it with the
real FalkorDB builder on the seeded adversarial synthetic table. That test uses
throwaway `agentfeat-parity-*` graphs and skips when FalkorDB is unreachable.

### FalkorDB builder (cross-check)

`traceaml-build-graph-features` streams the normalized Parquet through a
DuckDB out-of-core sort over exactly four columns
(`transaction_id`, `timestamp`, `sender_account_key`,
`receiver_account_key`) into a temporary ignored sorted Parquet, then
processes complete same-minute groups against a separate aggregate feature
graph (`traceaml-features`; `FAccount` nodes, one directed `FLOW` edge per
distinct account pair with `last_epoch`). Features for a minute are queried
from prior graph state first; the minute applies only afterwards, so
same-minute peers never observe one another. The main
transaction-evidence graph is never touched, and labels/splits/patterns are
never selected.

```sh
uv run --project graph traceaml-build-graph-features \
  --parquet data/processed/hi-small-v8/transactions.parquet \
  --features-out data/processed/hi-small-v8/graph-features.parquet \
  --manifest-out data/processed/hi-small-v8/graph-features-manifest.json
```

Smoke (plumbing only; prefix-relative features, never evaluation data):

```sh
uv run --project graph traceaml-build-graph-features \
  --parquet data/processed/hi-small-v8/transactions.parquet \
  --features-out data/interim/traceaml-graph-features/smoke/features.parquet \
  --manifest-out data/interim/traceaml-graph-features/smoke/manifest.json \
  --feature-graph traceaml-features-smoke --limit 50000
```

Aggregate-only preflight without sorting, database, or output:

```sh
uv run --project graph traceaml-build-graph-features \
  --parquet data/processed/hi-small-v8/transactions.parquet \
  --features-out data/interim/traceaml-graph-features/preflight/features.parquet \
  --manifest-out data/interim/traceaml-graph-features/preflight/manifest.json \
  --dry-run
```

Candidate features (`gfeat_` prefix): recent 24-hour distinct
out/in counterparties for sender and receiver, and a prior reverse-flow indicator. A `FLOW` edge
is recent-eligible iff `group_epoch - window <= last_epoch < group_epoch`.
The manifest records the source hash, row counts, window, feature
definitions, UTC assumption, and proof that target/split were not read.
FalkorDB must already be running; an existing feature graph is deleted and
recreated at the start of every build so reruns reproduce identical output.
The `FAccount.account_key` range index is created before lookups to avoid
label scans.
