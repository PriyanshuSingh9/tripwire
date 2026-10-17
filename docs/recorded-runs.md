# Recorded runs for the simulation

The simulation (`/`) has a Recorded mode that replays real API responses captured earlier. It exists so the demo and the guided walkthrough work without a live graph, a live model, or a particular dataset state.

## What is stored

`apps/web/public/recorded/runs.json`, generated locally and ignored by Git. It holds transaction-level records from the synthetic dataset, so it is never committed. Only the schema (`apps/web/app/lib/recorded.ts`) and the generator are in the repository.

Each entry is exactly what `POST /api/investigations` returned, or the HTTP error it returned (status and detail). Nothing is written by hand. Entries never contain target labels or typology truth, because the API does not return them.

## Capture

1. Start FalkorDB with the graph loaded and the API (`TRACEAML_FALKORDB_HOST`, `TRACEAML_GRAPH_NAME`).
2. Copy `apps/web/scripts/capture-spec.example.json`, replace each `transaction_id`, and keep one entry per scenario:
   - `evidence_found`: a review alert whose investigation returns paths (use `act: true` to open a case).
   - `gate_denied`: the same transaction again with `act: true` until the gate denies it (for example `duplicate_enrichment`).
   - `no_matching_path`: an alert whose bounded queries return no path.
   - `inconclusive`: a high-degree anchor with a small result cap, if one exists in your data.
   - `graph_offline`: stop FalkorDB (`docker compose stop falkordb`) before capturing this entry.
3. Run `node scripts/capture-runs.mjs <spec.json>` from `apps/web`. Set `TRACEAML_API_ORIGIN` if the API is not on `http://127.0.0.1:8000`.

Recorded mode labels every replay "Recorded run on synthetic data" and states that nothing was sent to the API.
