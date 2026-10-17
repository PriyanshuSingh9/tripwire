# 01 — Product and Track 1 Alignment

**Track:** Track 1 — Agents That Act on Connected Data  
**Goal:** Make the graph change the agent's actions, not merely the report it writes.

## 1. Product definition

TraceAML is a synthetic-AML investigation workbench. It takes a transaction selected from the frozen replay cohort, retrieves the corresponding model score, queries FalkorDB for transaction relationships, and runs a bounded, evidence-grounded investigation. Based on validated graph findings and deterministic policy, it can open or enrich a local investigation case for human review.

The product is not a bank-integrated AML system. It does not block real payments, file regulatory reports, or determine criminal guilt. A graph motif is a reason to investigate—not proof of laundering.

## 2. What the official Track 1 page emphasizes

The official event page names these focus areas:

1. Multi-hop reasoning.
2. Graph queries and algorithms as agent tools.
3. Tool selection through MCP.
4. Actions based on what the graph shows.
5. Explainable results.

Source: <https://www.wemakedevs.org/hackathons/falkordb>

The rules require FalkorDB to be the primary graph database and a central part of the product. The rules explicitly reject a graph visualization that merely sits on ordinary storage. Required submission materials include a public repository, README with setup and graph model, Cypher queries or graph algorithms, demo video, deployment or complete local setup, declared track, and disclosure of AI coding-assistant use.

Source: <https://www.wemakedevs.org/hackathons/falkordb/rules>

The event guide's practical recommendation is to let the agent choose among fixed, human-written query tools rather than letting it compose arbitrary Cypher. Tool results should include the path, tool name, arguments, duration and result count. It distinguishes “not in graph” from “nothing is wrong.”

Source: <https://www.wemakedevs.org/blogs/how-to-build-an-agent-that-reasons-over-a-graph>

These official focus areas are not published as numerical judging weights. This document does not invent such weights.

## 3. Map Track 1 to implementation and proof

| Track 1 focus | Product behavior | Implementation surface | Proof the judge can inspect |
|---|---|---|---|
| Multi-hop reasoning | Investigate an account's nearby and connected transaction flows across bounded hops | `graph/src/graph/queries.py`; investigation service | Rendered path with actual transaction IDs, amounts, currencies and timestamps; compare shallow and deeper trace |
| Graph queries as tools | Agent chooses a fixed operation instead of making up a graph query | MCP tool server/adaptor backed by existing query functions | Tool trace shows selected tool, validated arguments, duration and returned rows |
| Algorithms as tools | Where useful, expose one additional graph algorithm that adds a defensible signal | Optional graph-algorithm wrapper | Algorithm output is linked to nodes/edges and explained in the UI |
| MCP selection | MCP is the interface through which the agent runtime discovers and invokes approved graph tools | Thin MCP server/client integration | Working request reaches MCP tool, calls the existing query implementation, and returns structured evidence |
| Action based on graph | Evidence can cause a controlled case to be opened or enriched; no evidence yields no unsupported action | API policy + case service | Case history shows evidence references, action reason and review state |
| Explainability | Every report claim is grounded in returned graph records | verifier + report schema + evidence UI | Clicking a claim reveals the source transactions and path; unsupported claims are rejected or fallback is used |
| Reliability and bounded behavior | Depth, result counts, time windows, tool count and deadlines are capped | Query input validation, MCP schemas, API timeouts | Tests show invalid arguments and high-degree anchors are bounded and handled safely |

## 4. The graph must be causally relevant to product behavior

Use this counterfactual test:

> If FalkorDB were unavailable or its graph relationships were removed, which TraceAML behavior would disappear or materially change?

A strong answer:

- The system could still display the offline model score, but it could not produce a real multi-hop path or graph-based investigation evidence.
- It would be unable to verify claims about account-to-account paths that depend on relationships stored in FalkorDB.
- It would not be able to open/enrich a case with verified graph pattern evidence.

A weak answer:

- The graph only changes the color of nodes in Cytoscape.
- The LLM receives a prewritten narrative and the database has no influence on tool selection or case actions.
- Query results can be substituted with hard-coded example rows without the demo visibly failing.

The implementation should make the strong answer true. Preserve Parquet for offline analytics and FalkorDB for topology and interactive evidence; the two roles do not conflict.

## 5. Define the four investigation outcomes

Every investigation should return one of four explicit result classes:

1. **`evidence_found`** — one or more bounded graph queries returned relevant paths or defined patterns. Include the actual records and their paths.
2. **`no_matching_path`** — the queried graph contains no path matching the selected operation and bounds. This only means the query did not find a path; it does not prove the transaction is safe.
3. **`unknown_anchor`** — the transaction/account reference does not resolve in the graph. Do not launch further traversal.
4. **`inconclusive`** — timeout, result truncation, high-degree cap, or incomplete evidence prevented a reliable answer. Preserve the reason; do not turn it into a negative finding.

Never conflate `no_matching_path` with `allow`, and never allow the agent to infer that the absence of a path proves the absence of laundering.

## 6. What the agent may and may not do

### Allowed

- Choose among approved read-only graph tools.
- Request a bounded follow-up query based on prior results.
- Summarize returned evidence and identify uncertainty or counterevidence.
- Propose opening or enriching an investigation case.
- Produce a report that contains source record IDs and validated structured claims.

### Not allowed

- Generate arbitrary Cypher and have it executed.
- Change the LightGBM score, decision threshold or `allow`/`review` policy.
- Treat synthetic laundering labels or typology truth as online evidence.
- Claim that a pattern proves criminal conduct.
- Block payments, file official reports, or perform external irreversible actions.
- Invent transaction IDs, entities, amounts, timestamps or paths.

The action layer can accept a **case-management** write after deterministic validation. This is enough to demonstrate an agent acting on graph evidence without turning a hackathon prototype into a high-risk payment-control system.

## 7. Recommended demo acceptance test

Use one fixed transaction from the test cohort and one case where the graph returns no qualifying path or yields an inconclusive result.

For the positive evidence case, a judge should see:

1. Score and score provenance from the target-free replay artifact.
2. An agent-selected MCP tool and its parameters.
3. FalkorDB's actual multi-hop transaction path.
4. Evidence verification passing against returned records.
5. A controlled case open/enrich action with an audit entry.

For the negative/inconclusive case, show that no unsupported graph claim or escalation is produced, and make the difference between “no path found” and “unknown/incomplete graph evidence” visible.

## 8. Graph-algorithm decision

Do not add an algorithm merely to check a box. First test whether one algorithm improves the actual investigation story and can be explained in one sentence. A bounded shortest-path or connected-component/community result may fit, depending on the current transaction-node schema and installed FalkorDB procedures. Verify the schema and runtime support before committing.

If there is insufficient time to integrate a graph algorithm safely, the three existing graph query tools already demonstrate genuine multi-hop graph work. Do not destabilize the current product for an untested algorithm integration.

## 9. Definition of done for Track 1 alignment

- [ ] FalkorDB is required to return the real investigation evidence.
- [ ] Agent tool choice is visible and executed through the documented MCP boundary.
- [ ] The model never composes raw Cypher.
- [ ] Query parameters, depth, rows, time windows and timeouts are bounded.
- [ ] Returned paths contain real transaction references.
- [ ] A validated graph result can cause a controlled case action.
- [ ] The UI explains why that action occurred and exposes the evidence.
- [ ] Empty, unknown and incomplete results remain distinct.
- [ ] The demo has a positive-evidence case and a negative/inconclusive case.
