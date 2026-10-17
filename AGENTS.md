# Project Agent Directives: TraceAML

The repository is named Tripwire. The product is TraceAML: a graph-powered AML detection and investigation workbench using synthetic IBM AMLworld data and FalkorDB.

## Architecture invariants

- **Toolchain separation:** Manage `apps/web/` with pnpm. Manage `services/*` and `ml/` with uv. Do not use npm, yarn, bun, system pip, or global Python package installs.
- **Graph authority:** FalkorDB is the source of truth for graph topology and structural investigation evidence. UI graph content must derive from executed Cypher queries.
- **First-class transactions:** Represent transfers as `(:Account)-[:SENT]->(:Transaction)-[:RECEIVED]->(:Account)` with stable transaction IDs.
- **Evaluation isolation:** Target labels and laundering typology truth stay in offline evaluation data. Do not write them to the inference graph, return them in inference queries, or expose test labels to feature generation.
- **Temporal integrity:** Features for transaction time `t` use no information later than `t`. Freeze the test cohort and tune only on training and validation data.
- **Policy ownership:** ML and LLM outputs are scores or proposals. Deterministic policy owns `allow`, `review`, and simulated `block` decisions. Use safe human-review or deterministic fallback when a provider fails.
- **Evidence-backed reports:** Investigation reports may reference only records returned by approved tools. Validate IDs, paths, amounts, and timestamps before presenting claims as evidence.
- **Case memory:** Only explicit analyst confirmation promotes suspected entities to confirmed memory. Preserve dismissal history and provenance.
- **Data handling:** Raw and processed datasets, credentials, and generated model artifacts are excluded from Git. Do not download the dataset; the user will provide it.

## Team ownership

- **Kanishka:** `apps/web/`; frontend, information architecture, Cytoscape interaction, and benchmark presentation.
- **Ashwani:** `graph/` and graph ingestion/query implementation; FalkorDB schema, indexes, Cypher, and structural extraction.
- **Parth:** `services/api/`; FastAPI contracts, replay orchestration, deterministic policy, persistence, and investigation integration.
- **User, Fraud Intelligence and Evaluation Lead:** `ml/`; data protocol, baselines, graph feature design with Ashwani, benchmark methods, and review of fraud/performance claims. The user also coordinates integration and reviews delegated work.

Primary ownership does not block cross-review. Shared contracts belong in `docs/contracts.md` and should be agreed before parallel interface changes.

## Environment and tooling

- Use pnpm for web dependencies and scripts.
- Use uv for Python virtual environments, dependency resolution, and commands.
- Use ripgrep (`rg`) for repository searches.
- Keep generated outputs under ignored data/artifact paths.
- Keep `docs/architecture.md`, `docs/contracts.md`, `docs/decisions.md`, and role charters synchronized when the stack, schemas, APIs, or ownership changes.

## Code and documentation quality

- Zero emoji in source code, comments, documentation, UI strings, commit messages, and filenames.
- Strict TypeScript with no untyped `any`.
- Use validated Pydantic models for backend request and response contracts.
- Handle service boundaries with typed results or explicit HTTP errors; do not leak unhandled exceptions.
- Preserve unrelated comments, docstrings, and type annotations during edits.
- Use conventional commit prefixes when committing.
