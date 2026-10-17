# Tracer AML implementation reference

Reviewed 2026-10-15. Tracer is a useful engineering and evaluation reference for TraceAML, but its reported benchmark is not directly comparable to our planned transaction-level replay.

## Structures to learn from

Tracer divides its workflow into reproducible stages: source normalization and Parquet output, feature construction, a shared split, a tabular baseline, operating-point analysis, graph modeling, ring extraction, and investigation evaluation. Its scripts keep generated datasets, model files, and metric JSON outside the application UI. That separation is a good pattern for TraceAML's `ml/` project.

For the model comparison, Tracer trains its LightGBM control on the same feature table and split as its graph model. Its operating-point script compares alert counts at matched recall. TraceAML will keep the shared-cohort comparison and alert-budget analysis, while choosing thresholds on validation data and reporting those thresholds unchanged on test.

Tracer also treats investigation as a human-reviewed workflow and evaluates factual grounding separately from detection quality. TraceAML should preserve that separation and use deterministic evidence validation for transaction IDs, paths, amounts, and timestamps.

## Adaptations required for TraceAML

Tracer's README describes a stratified 60/20/20 account-level split. Its feature script aggregates activity across the full observation period before splitting accounts. This supports its account-level graph experiment, but it does not establish what was knowable at an earlier transaction timestamp. TraceAML's primary benchmark is chronological and transaction-level, so historical features must be computed from strictly earlier timestamps. Transactions in the same source minute are treated as simultaneous for feature availability.

Tracer's graph builder derives an illicit-account target from laundering transaction labels. TraceAML keeps labels and typology truth in the offline evaluation path; they are not graph properties available to inference queries or the investigator. The patterns sidecar is evaluation metadata only and must never supply negative labels or model features.

Tracer uses PyTorch Geometric, NetworkX, LangGraph, Streamlit, and a stratified account protocol. TraceAML keeps its selected stack: FalkorDB for graph topology and investigation queries, Next.js for the analyst UI, and a Python service for scoring and evidence validation. The reference's reported model scores and claims are not adopted as TraceAML results.

## Reuse and attribution

The reference repository is published under Apache-2.0. TraceAML currently borrows workflow ideas and evaluation questions; no Tracer source files have been copied or vendored. If code is reused later, preserve the applicable license and notices, mark modified files, and record the exact upstream revision.

## Primary sources

- [Tracer repository and README](https://github.com/hugocorreia123/tracer-aml-graph-intelligence)
- [Data normalization and graph build](https://github.com/hugocorreia123/tracer-aml-graph-intelligence/blob/main/scripts/build_graph.py)
- [Feature construction and account split](https://github.com/hugocorreia123/tracer-aml-graph-intelligence/blob/main/scripts/make_features.py)
- [LightGBM baseline](https://github.com/hugocorreia123/tracer-aml-graph-intelligence/blob/main/scripts/train_baseline.py)
- [Fixed-recall operating-point analysis](https://github.com/hugocorreia123/tracer-aml-graph-intelligence/blob/main/scripts/operating_point.py)
- [Apache-2.0 license](https://github.com/hugocorreia123/tracer-aml-graph-intelligence/blob/main/LICENSE)
