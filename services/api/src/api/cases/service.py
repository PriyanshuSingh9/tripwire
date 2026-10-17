"""Case review rules: creation from scored transactions and explicit decisions.

- A case can only be opened for a transaction present in the target-free
  score artifact; its risk score is snapshotted from that artifact.
- At most one case per transaction may be open or confirmed at a time; a
  dismissed case does not block a new one, and stays in history.
- ``confirm`` and ``dismiss`` are terminal, explicit analyst actions with
  reviewer provenance. Reviewing a terminal case is a conflict, never an
  overwrite.
"""

from __future__ import annotations

import json
from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.cases.models import CaseModel
from api.cases.repository import CaseRepository
from api.cases.schemas import (
    OPEN_STATUSES,
    CaseCreateRequest,
    CaseEvidenceEntry,
    CaseHistoryEntry,
    CaseItem,
    CaseListResponse,
    CaseReviewRequest,
    CaseStatus,
)
from api.exceptions import ConflictError, NotFoundError, TransactionNotFoundError


class ScoreLookup(Protocol):
    """Subset of the score repository the case service needs."""

    def score_for(self, transaction_id: str) -> float | None: ...


class CaseNotFoundError(NotFoundError):
    """The case ID does not exist."""

    detail = "case not found"


class CaseAlreadyOpenError(ConflictError):
    """The transaction already has an open or confirmed case."""

    detail = "transaction already has an active case"


class CaseAlreadyReviewedError(ConflictError):
    """The case already carries a terminal decision."""

    detail = "case already reviewed"


def to_case_item(model: CaseModel) -> CaseItem:
    """Project an ORM case onto the response contract."""
    return CaseItem.model_validate(
        {
            "case_id": model.case_id,
            "transaction_id": model.transaction_id,
            "status": model.status,
            "risk_score": model.risk_score,
            "opened_by": model.opened_by,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "history": [
                CaseHistoryEntry.model_validate(
                    {
                        "review_id": review.review_id,
                        "action": review.action,
                        "reviewer_id": review.reviewer_id,
                        "pattern_type": review.pattern_type,
                        "notes": review.notes,
                        "timestamp": review.timestamp,
                    }
                )
                for review in model.reviews
            ],
            "evidence": [
                CaseEvidenceEntry.model_validate(
                    {
                        "evidence_id": item.evidence_id,
                        "review_id": item.review_id,
                        "action": item.action,
                        "proposed_by": item.proposed_by,
                        "investigation_id": item.investigation_id,
                        "reason": item.reason,
                        "evidence_refs": json.loads(item.evidence_refs_json),
                        "tool_run_ids": json.loads(item.tool_run_ids_json),
                        "tool_trace": json.loads(item.tool_trace_json),
                        "risk_score": item.risk_score,
                        "decision": item.decision,
                        "policy_version": item.policy_version,
                        "timestamp": item.timestamp,
                    }
                )
                for item in model.evidence
            ],
        }
    )


class CaseService:
    """Case listing, creation, and review workflows."""

    def __init__(self, session: AsyncSession) -> None:
        self.repo = CaseRepository(session)

    async def list_cases(
        self, status: CaseStatus | None, limit: int, offset: int
    ) -> CaseListResponse:
        models, total = await self.repo.list_cases(status=status, limit=limit, offset=offset)
        return CaseListResponse(
            items=[to_case_item(model) for model in models],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_case(self, case_id: str) -> CaseItem:
        case = await self.repo.get_by_id(case_id)
        if case is None:
            raise CaseNotFoundError()
        return to_case_item(case)

    async def create_case(self, request: CaseCreateRequest, scores: ScoreLookup) -> CaseItem:
        score = scores.score_for(request.transaction_id)
        if score is None:
            raise TransactionNotFoundError()
        active = await self.repo.find_by_transaction(
            request.transaction_id, (*OPEN_STATUSES, "confirmed")
        )
        if active is not None:
            raise CaseAlreadyOpenError()
        try:
            case_id = await self.repo.create(
                request.transaction_id, score, request.opened_by, request.notes
            )
        except IntegrityError as exc:
            await self.repo.session.rollback()
            raise CaseAlreadyOpenError() from exc
        return await self.get_case(case_id)

    async def review_case(self, case_id: str, request: CaseReviewRequest) -> CaseItem:
        case = await self.repo.get_by_id(case_id)
        if case is None:
            raise CaseNotFoundError()
        if case.status not in OPEN_STATUSES:
            raise CaseAlreadyReviewedError()
        await self.repo.record_review(
            case,
            request.action,
            request.reviewer_id,
            request.pattern_type,
            request.notes,
        )
        return await self.get_case(case_id)
