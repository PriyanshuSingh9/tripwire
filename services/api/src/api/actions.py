"""Deterministic gate for graph-driven case actions.

An agent (or, without an LLM, deterministic policy) may propose
``open_case`` or ``enrich_case`` after an investigation. Code decides whether
the proposal runs. The gate requires that:

1. the outcome is ``evidence_found`` (a query completed and returned a path or
   pattern; ``no_matching_path`` and ``inconclusive`` never escalate);
2. opening a case requires the deterministic policy decision to be ``review``;
3. evidence references are present and every one exists in the evidence the
   executed tool runs returned for this investigation;
4. the case state allows it (one active case per transaction; enrichment
   needs an open case and is not repeated with identical references).

The action writes only to the local case store, with the evidence references,
tool-run IDs and the score and decision snapshot. It cannot change a score or
a policy decision and never touches a payment.
"""

from __future__ import annotations

import json
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import schemas
from api.cases.models import CaseModel
from api.cases.repository import CaseRepository, EvidenceWrite
from api.cases.schemas import OPEN_STATUSES

AGENT_ACTOR = "traceaml-agent"
POLICY_ACTOR = "traceaml-policy"
MAX_EVIDENCE_REFS = 25
Proposer = Literal["agent", "policy"]


def _evidence_ids(response: schemas.InvestigationResponse) -> set[str]:
    return {record.transaction_id for record in response.evidence.transactions}


def policy_refs(response: schemas.InvestigationResponse) -> list[str]:
    """Transactions on returned paths, anchor first, bounded and ordered."""
    ordered: list[str] = [response.transaction_id]
    for path in response.evidence.paths:
        for transaction_id in path.transaction_ids:
            if transaction_id not in ordered:
                ordered.append(transaction_id)
    return ordered[:MAX_EVIDENCE_REFS]


def tool_run_ids(response: schemas.InvestigationResponse) -> list[str]:
    """Runs that returned evidence, so an action cites what supported it."""
    return [
        entry.tool_run_id
        for entry in response.tool_trace
        if entry.status == "ok" and entry.outcome == "evidence_found"
    ]


def derive_proposal(
    response: schemas.InvestigationResponse, *, has_active_case: bool
) -> tuple[schemas.ProposedAction, Proposer, str, list[str]]:
    """Who proposes what, why, and citing which transactions."""
    report = response.report
    if report is not None and report.source == "llm":
        reason = report.action_reason or "agent proposed this action from graph evidence"
        return report.proposed_action, "agent", reason, list(report.supporting_transaction_ids)
    if response.outcome == "evidence_found" and response.decision == "review":
        action: schemas.ProposedAction = "enrich_case" if has_active_case else "open_case"
        paths = len(response.evidence.paths)
        reason = (
            f"policy {response.policy_version}: graph evidence found ({paths} path(s)) "
            f"for a transaction scored for review; human review required"
        )
        return action, "policy", reason, policy_refs(response)
    return "none", "policy", "no graph-supported action", []


def gate(
    action: schemas.ProposedAction,
    response: schemas.InvestigationResponse,
    refs: list[str],
) -> schemas.ActionDenialCode | None:
    """Stateless checks. Returns a denial code, or ``None`` when allowed."""
    if response.outcome != "evidence_found":
        return "outcome_not_actionable"
    if action == "open_case" and response.decision != "review":
        return "decision_not_review"
    if not refs:
        return "evidence_refs_missing"
    known = _evidence_ids(response)
    if any(ref not in known for ref in refs):
        return "evidence_ref_unverified"
    return None


def _result(
    action: schemas.ProposedAction,
    by: Proposer,
    reason: str,
    refs: list[str],
    runs: list[str],
    status: Literal["executed", "denied", "no_action"],
    denial: schemas.ActionDenialCode | None = None,
    case_id: str | None = None,
) -> schemas.CaseActionResult:
    return schemas.CaseActionResult(
        proposed_action=action,
        proposed_by=by,
        reason=reason,
        status=status,
        denial_code=denial,
        case_id=case_id,
        evidence_refs=refs,
        tool_run_ids=runs,
    )


async def apply_case_action(
    response: schemas.InvestigationResponse, session: AsyncSession
) -> schemas.CaseActionResult:
    """Derive the proposal, run the gate, and write the case if allowed."""
    repo = CaseRepository(session)
    active: CaseModel | None = await repo.find_by_transaction(
        response.transaction_id, (*OPEN_STATUSES, "confirmed")
    )
    action, by, reason, refs = derive_proposal(response, has_active_case=active is not None)
    runs = tool_run_ids(response)
    if action == "none":
        return _result(action, by, reason, refs, runs, "no_action")
    denial = gate(action, response, refs)
    if denial is not None:
        return _result(action, by, reason, refs, runs, "denied", denial)

    evidence = EvidenceWrite(
        action=action,
        proposed_by=by,
        investigation_id=response.investigation_id,
        reason=reason,
        evidence_refs=refs,
        tool_run_ids=runs,
        tool_trace=response.tool_trace,
        risk_score=response.risk_score,
        decision=response.decision,
        policy_version=response.policy_version,
    )
    actor = AGENT_ACTOR if by == "agent" else POLICY_ACTOR

    if action == "open_case":
        if active is not None:
            return _result(action, by, reason, refs, runs, "denied", "case_already_active")
        try:
            case_id = await repo.create(
                response.transaction_id, response.risk_score, actor, reason, evidence
            )
        except IntegrityError:
            await session.rollback()
            return _result(action, by, reason, refs, runs, "denied", "case_already_active")
        return _result(action, by, reason, refs, runs, "executed", case_id=case_id)

    if active is None or active.status not in OPEN_STATUSES:
        return _result(action, by, reason, refs, runs, "denied", "no_open_case")
    if any(
        sorted(json.loads(item.evidence_refs_json)) == sorted(refs)
        for item in active.evidence
        if item.action == "enrich_case"
    ):
        return _result(action, by, reason, refs, runs, "denied", "duplicate_enrichment")
    await repo.add_enrichment(active, actor, evidence)
    return _result(action, by, reason, refs, runs, "executed", case_id=active.case_id)

