# TraceAML — Next Moves Planning Pack

**Prepared:** 2026-10-16  
**Planning horizon:** Graph Hacks submission closes 2026-10-18 at 11:59 PM IST  
**Repository reported:** Tripwire / product TraceAML  
**Reviewed revision:** `84b658b` on `main`  
**Target track:** Track 1 — Agents That Act on Connected Data

This pack turns the current implementation report into an actionable build, evaluation, product, and submission plan. It is a planning artifact, not a code change to the Tripwire repository. The build order prioritizes the Track 1 agent-to-graph-to-case path while benchmark corrections proceed in parallel.

## Executive decision

Do not redesign TraceAML or add a new model family. Stabilize the benchmark, make the existing graph tools available through a small MCP boundary, connect graph evidence to a controlled investigation action, finish the case-review UI, then run a clean release and demo pass.

TraceAML's product promise:

> Given a flagged transaction, TraceAML investigates connected money flows through FalkorDB, chooses bounded graph tools, returns a path that a reviewer can verify, and opens or enriches a review case when deterministic policy allows it. It never claims that a graph pattern proves crime, never lets the LLM alter the risk score, and never blocks a real payment.

## Documents

1. [00 — Current assessment and decisions](00-current-assessment.md) — where the implementation stands, what is aligned, and the main unresolved risks.
2. [01 — Product and Track 1 alignment](01-product-and-track-alignment.md) — official event requirements translated into TraceAML behavior and acceptance criteria.
3. [02 — Build plan](02-build-plan.md) — prioritized implementation sequence, dependencies, scope boundaries, and acceptance gates.
4. [03 — Benchmark plan](03-benchmark-plan.md) — detection metrics, split discipline, test governance, performance and cost protocol.
5. [04 — Graph tools, MCP, agent and action design](04-agent-mcp-action-design.md) — the minimum architecture that makes graph reasoning and tool selection visible.
6. [05 — Demo and presentation plan](05-demo-and-presentation.md) — product story, walkthrough, video storyboard and claims to avoid.
7. [06 — Release checklist](06-release-checklist.md) — tests, clean setup, repository hygiene, and official deliverables.
8. [07 — Task board and ownership](07-task-board-and-ownership.md) — actionable work items with suggested owners and exit criteria.
9. [08 — Risk register](08-risk-register.md) — correctness, benchmark, integration, and demo failure risks with mitigations.

## Authority and evidence

This plan uses two evidence sources:

- The team's implementation report, reviewed revision `84b658b`, dated 2026-10-16. The report says it summarizes repository state, benchmark snapshots, local artifacts, and remaining gaps; it also says no full test suite or live Gemini call was run during that report update.
- The official Graph Hacks / FalkorDB event pages, checked 2026-10-16:
  - [Hackathon overview and Track 1](https://www.wemakedevs.org/hackathons/falkordb)
  - [Official rules and submission requirements](https://www.wemakedevs.org/hackathons/falkordb/rules)
  - [WeMakeDevs: How to Build an Agent That Reasons Over a Graph](https://www.wemakedevs.org/blogs/how-to-build-an-agent-that-reasons-over-a-graph)

Official event pages define the requirements. Recommendations in this pack—such as the MCP bridge shape, action gating, demo structure, and benchmark freeze procedure—are engineering recommendations intended to meet those requirements reliably; they are not represented as extra official rules.

## Time-box rule

The deadline is close. Choose correctness, graph-driven behavior, a coherent user journey and submission completeness over new model architectures or infrastructure. If a task does not improve one of those outcomes and cannot be finished safely before submission, defer it.
