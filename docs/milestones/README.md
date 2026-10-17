# Team Ownership and Delivery Guide

The files in this directory are role charters and shared delivery gates. They replace the earlier day-by-day plans. The current scope and technical choices live in [the PRD](../prd.md), [architecture](../architecture.md), [contracts](../contracts.md), and [decisions](../decisions.md).

## Member charters

| Member | Primary role | Charter |
| --- | --- | --- |
| Kanishka | Frontend and product UI | [Frontend charter](kanishka/milestones.md) |
| Ashwani | Graph and data engineering | [Graph charter](ashwani/milestones.md) |
| Parth | Backend and investigation integration | [Backend charter](parth/milestones.md) |
| Priyanshu | Fraud intelligence, evaluation, and integration lead | [Evaluation charter](priyanshu/milestones.md) |

The existing `milestones.md` filenames are retained so project references do not break. They now document ownership and interfaces, not dated implementation plans.

## Shared delivery gates

1. **Repository foundation:** local web/API/FalkorDB setup, versioned contracts, and ignored data/artifact paths are in place.
2. **Data readiness:** user-provided files are profiled; schema, license, hashes, label prevalence, account identity, and temporal coverage are recorded.
3. **Evaluation readiness:** split manifest and leakage checks exist; the three model experiments use identical frozen cohorts.
4. **Graph readiness:** idempotent ingestion and bounded investigation queries return source-backed records.
5. **Vertical slice:** selected transaction can be scored, investigated, verified, and shown in the workbench.
6. **Benchmark readiness:** metrics, alert budget, latency, throughput, cost assumptions, and execution environment are recorded.
7. **Submission readiness:** setup instructions, schema/query documentation, reproducible results, and demo materials are complete.

These gates describe outcomes and do not prescribe dates or sequence details for individual members.

## Integration agreements

- Keep one authority for public request/response schemas in `docs/contracts.md`.
- Coordinate account keys and transaction property names across ingestion, graph queries, features, API, and UI.
- Keep labels out of the inference graph and evaluation test labels out of feature generation.
- UI fixtures may support layout work, but must be labelled as fixtures and must not be presented as retrieved evidence or benchmark results.
- Review security, data license, and evidence provenance claims before public release.
