"""Case review routes under ``/api/cases``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.cases.schemas import (
    CaseCreateRequest,
    CaseItem,
    CaseListResponse,
    CaseReviewRequest,
    CaseStatus,
)
from api.cases.service import CaseService
from api.dependencies import get_case_session, get_score_repository
from api.scores import ScoreRepository

router = APIRouter(prefix="/api/cases", tags=["cases"])

MAX_CASE_PAGE = 100


def get_case_service(
    session: Annotated[AsyncSession, Depends(get_case_session)],
) -> CaseService:
    return CaseService(session)


ServiceDep = Annotated[CaseService, Depends(get_case_service)]
CaseIdPath = Annotated[str, Path(min_length=1, max_length=64)]


@router.get("", response_model=CaseListResponse)
async def list_cases(
    service: ServiceDep,
    status: CaseStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_CASE_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CaseListResponse:
    """List cases, most recently updated first, optionally by status."""
    return await service.list_cases(status=status, limit=limit, offset=offset)


@router.post("", response_model=CaseItem, status_code=201)
async def create_case(
    request: CaseCreateRequest,
    service: ServiceDep,
    scores: Annotated[ScoreRepository, Depends(get_score_repository)],
) -> CaseItem:
    """Open a suspected case for a transaction in the score artifact."""
    return await service.create_case(request, scores)


@router.get("/{case_id}", response_model=CaseItem)
async def get_case(case_id: CaseIdPath, service: ServiceDep) -> CaseItem:
    """Return one case with its full history."""
    return await service.get_case(case_id)


@router.post("/{case_id}/review", response_model=CaseItem)
async def review_case(
    case_id: CaseIdPath, request: CaseReviewRequest, service: ServiceDep
) -> CaseItem:
    """Record an explicit confirm or dismiss decision with reviewer provenance."""
    return await service.review_case(case_id, request)
