# Ashwani: Graph and Data Engineering Charter

## Role

Own FalkorDB schema, transaction graph materialization, Cypher investigation queries, and graph-derived structural signals.

## Primary paths

- `graph/`
- Graph ingestion/query modules under `services/` or `ml/`, coordinated with Parth and the evaluation lead

## Responsibilities

- Validate source account and transaction identifiers with the data owner when the dataset arrives.
- Use bank-aware composite account keys where IDs are not globally unique.
- Ingest accounts and first-class transaction nodes idempotently.
- Maintain parameterized, bounded queries for `trace_funds`, `inspect_counterparties`, and `detect_flow_patterns`.
- Return transaction IDs, timestamps, amounts/currencies, paths, limits, and query timings from actual FalkorDB results.
- Define graph features with the evaluation lead and document their point-in-time availability.
- Keep target labels and typology ground truth out of the inference graph and query results.

## Interfaces and boundaries

- Implement executable query and ingestion behavior; `docs/contracts.md` records the shared shape.
- Coordinate API integration with Parth and feature/benchmark design with the evaluation lead.
- Do not let client-side or LLM-generated structures substitute for queried topology.
- Keep traversal depth, time range, result size, and execution time bounded.

## Completion evidence

Ingestion is repeatable without duplicate graph entities, and each approved tool returns bounded, source-backed results whose records can be traced to the input data.

## Current implementation status

- **Implemented:** first-class account/transaction graph loader, indexes, idempotent batched ingestion, and all three bounded investigation tools. The 500,000-row capacity graph reconciled transaction and edge counts.
- **Implemented:** trace responses include source/destination keys for every returned transaction; pass-through queries bound both transfers to the request window.
- **Verified:** a target-free 998,021-transaction demo slice loaded and supported a live validated API investigation for a held-out case.
- **Complete:** point-in-time graph feature extraction wrote and reconciled all 5,078,345 transaction IDs with five target-free columns. It took 1,563 seconds. The same-cohort ablation did not improve average precision or recall at the fixed alert budget. Query timing is in [the performance report](../../benchmarks/iteration-1-performance.md).
- **Review handoff:** the integration implementation touched `graph/`; review it against this ownership charter before extending graph behavior.
