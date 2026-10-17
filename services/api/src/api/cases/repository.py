"""Data access for cases and their append-only review history."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.cases.models import CaseEvidenceModel, CaseModel, CaseReviewModel, now_iso
from api.cases.schemas import CaseStatus, HistoryAction
from api.schemas import ToolTraceEntry


@dataclass(frozen=True)
class EvidenceWrite:
    """Evidence references and provenance for one agent or policy case action."""

    action: str
    proposed_by: str
    investigation_id: str
    reason: str
    evidence_refs: list[str]
    tool_run_ids: list[str]
    tool_trace: list[ToolTraceEntry]
    risk_score: float
    decision: str
    policy_version: str


class CaseRepository:
    """Async repository; the only write paths are inserts and status moves."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, case_id: str) -> CaseModel | None:
        """Fetch a case with fresh history."""
        stmt = (
            select(CaseModel)
            .where(CaseModel.case_id == case_id)
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_transaction(
        self, transaction_id: str, statuses: tuple[CaseStatus, ...]
    ) -> CaseModel | None:
        """Return a case for the transaction in one of the given statuses."""
        stmt = (
            select(CaseModel)
            .where(CaseModel.transaction_id == transaction_id)
            .where(CaseModel.status.in_(statuses))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_cases(
        self, status: CaseStatus | None, limit: int, offset: int
    ) -> tuple[list[CaseModel], int]:
        """List cases, most recently updated first, with a total count."""
        query = select(CaseModel)
        count_query = select(func.count(CaseModel.case_id))
        if status is not None:
            query = query.where(CaseModel.status == status)
            count_query = count_query.where(CaseModel.status == status)
        query = (
            query.order_by(CaseModel.updated_at.desc(), CaseModel.case_id)
            .offset(offset)
            .limit(limit)
        )
        total = (await self.session.execute(count_query)).scalar() or 0
        items = list((await self.session.execute(query)).scalars().all())
        return items, int(total)

    def _append(
        self,
        case_id: str,
        action: HistoryAction,
        reviewer_id: str,
        timestamp: str,
        pattern_type: str | None = None,
        notes: str | None = None,
    ) -> str:
        review_id = f"rev-{uuid.uuid4().hex[:12]}"
        self.session.add(
            CaseReviewModel(
                review_id=review_id,
                case_id=case_id,
                action=action,
                reviewer_id=reviewer_id,
                pattern_type=pattern_type,
                notes=notes,
                timestamp=timestamp,
            )
        )
        return review_id

    def _add_evidence(
        self, case_id: str, review_id: str, evidence: EvidenceWrite, timestamp: str
    ) -> None:
        self.session.add(
            CaseEvidenceModel(
                evidence_id=f"evd-{uuid.uuid4().hex[:12]}",
                case_id=case_id,
                review_id=review_id,
                action=evidence.action,
                proposed_by=evidence.proposed_by,
                investigation_id=evidence.investigation_id,
                reason=evidence.reason,
                evidence_refs_json=json.dumps(evidence.evidence_refs),
                tool_run_ids_json=json.dumps(evidence.tool_run_ids),
                tool_trace_json=json.dumps([e.model_dump() for e in evidence.tool_trace]),
                risk_score=evidence.risk_score,
                decision=evidence.decision,
                policy_version=evidence.policy_version,
                timestamp=timestamp,
            )
        )

    async def create(
        self,
        transaction_id: str,
        risk_score: float,
        opened_by: str,
        notes: str | None,
        evidence: EvidenceWrite | None = None,
    ) -> str:
        """Insert a suspected case plus its ``open`` history entry.

        With ``evidence`` the graph evidence is written in the same commit.
        """
        case_id = f"case-{uuid.uuid4().hex[:12]}"
        timestamp = now_iso()
        self.session.add(
            CaseModel(
                case_id=case_id,
                transaction_id=transaction_id,
                status="suspected",
                risk_score=risk_score,
                opened_by=opened_by,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await self.session.flush()
        review_id = self._append(case_id, "open", opened_by, timestamp, notes=notes)
        if evidence is not None:
            self._add_evidence(case_id, review_id, evidence, timestamp)
        await self.session.commit()
        return case_id

    async def add_enrichment(
        self, case: CaseModel, reviewer_id: str, evidence: EvidenceWrite
    ) -> None:
        """Append an ``enrich`` history entry and its evidence; status is unchanged."""
        timestamp = now_iso()
        review_id = self._append(
            case.case_id, "enrich", reviewer_id, timestamp, notes=evidence.reason
        )
        self._add_evidence(case.case_id, review_id, evidence, timestamp)
        case.updated_at = timestamp
        await self.session.commit()

    async def record_review(
        self,
        case: CaseModel,
        action: HistoryAction,
        reviewer_id: str,
        pattern_type: str | None,
        notes: str | None,
    ) -> None:
        """Append one review entry and move the case status.

        Prior entries are never modified; dismissal keeps every record.
        """
        timestamp = now_iso()
        self._append(case.case_id, action, reviewer_id, timestamp, pattern_type, notes)
        case.status = "confirmed" if action == "confirm" else "dismissed"
        case.updated_at = timestamp
        await self.session.commit()
