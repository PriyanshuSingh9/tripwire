"""LLM report draft schema and deterministic verification against evidence.

The model proposes a draft; code decides whether it is shown. Verification
checks, against records returned by executed graph queries only:

* every cited transaction ID (supporting and counter-evidence) exists;
* every structured claim matches the evidenced record field by field;
* every cited path equals a path a query returned;
* identifiers written in the prose (transaction IDs, account keys) exist, and
  amount-sized numbers in the prose match evidenced amounts or counts.

Any failure raises ``EvidenceValidationError`` and the caller falls back to
the deterministic report. The recommendation is not part of the draft: code
fixes it to human review.
"""

from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from api import schemas
from api.investigate import EvidenceBuilder
from api.verifier import EvidenceValidationError, validate_report

TRANSACTION_ID_RE = re.compile(r"\b[A-Za-z0-9_]+-tx-\d+\b")
ACCOUNT_KEY_RE = re.compile(r"\b[0-9A-Za-z]+::[0-9A-Za-z]+\b")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?")
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
AMOUNT_SIZED = 1000.0
_SCHEMA_DROP_KEYS = frozenset(
    {"title", "maxLength", "minLength", "maxItems", "minItems", "additionalProperties", "default"}
)


class LlmReportDraft(BaseModel):
    """What the model must return. Extra fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    hypothesis: str = Field(min_length=1, max_length=800)
    supporting_transaction_ids: list[str] = Field(min_length=1, max_length=25)
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=25)
    cited_claims: list[schemas.CitedClaim] = Field(default_factory=list, max_length=25)
    cited_paths: list[schemas.CitedPath] = Field(default_factory=list, max_length=10)
    # A proposal only. The application's action gate decides whether it runs.
    proposed_action: Literal["none", "open_case", "enrich_case"] = "none"
    action_reason: str = Field(default="", max_length=400)


def report_json_schema() -> dict[str, Any]:
    """JSON schema for the provider, with refs inlined and limits removed.

    Providers accept only a subset of JSON Schema, so size limits are stated
    in the prompt and enforced here by Pydantic after parsing instead.
    """
    raw = LlmReportDraft.model_json_schema()
    definitions = raw.get("$defs", {})

    dropped = _SCHEMA_DROP_KEYS | {"$defs"}

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = str(node["$ref"]).rsplit("/", 1)[-1]
                return resolve(definitions[name])
            union = node.get("anyOf")
            if isinstance(union, list):
                # Optional[X] is anyOf [X, null]; fields that may be absent are
                # simply omitted from "required", so keep only X.
                branches = [b for b in union if b.get("type") != "null"]
                if len(branches) == 1 and len(union) == 2:
                    extra = {k: v for k, v in node.items() if k != "anyOf"}
                    return resolve({**branches[0], **extra})
            return {k: resolve(v) for k, v in node.items() if k not in dropped}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = resolve(raw)
    return resolved


def _check_claim(claim: schemas.CitedClaim, record: schemas.TransactionEvidence) -> None:
    """A claim's stated fields must equal the evidenced record exactly."""
    for name in ("timestamp", "payment_currency", "source_account_key", "destination_account_key"):
        stated = getattr(claim, name)
        if stated is not None and stated != getattr(record, name):
            raise EvidenceValidationError(f"claim {name} does not match evidence")
    if claim.amount_paid is not None and (
        record.amount_paid is None
        or not math.isclose(claim.amount_paid, record.amount_paid, rel_tol=1e-9, abs_tol=1e-9)
    ):
        raise EvidenceValidationError("claim amount does not match evidence")


def _check_prose(text: str, builder: EvidenceBuilder, allowed_numbers: set[float]) -> None:
    """Identifiers and amount-sized numbers in prose must come from evidence."""
    for token in TRANSACTION_ID_RE.findall(text):
        if token not in builder.transactions:
            raise EvidenceValidationError("prose cites a transaction absent from evidence")
    for token in ACCOUNT_KEY_RE.findall(text):
        if token not in builder.accounts:
            raise EvidenceValidationError("prose cites an account absent from evidence")
    remainder = TRANSACTION_ID_RE.sub(" ", text)
    remainder = ACCOUNT_KEY_RE.sub(" ", remainder)
    remainder = DATE_RE.sub(" ", remainder)
    amounts = {
        value
        for record in builder.transactions.values()
        for value in (record.amount_paid, record.amount_received)
        if value is not None
    }
    for token in NUMBER_RE.findall(remainder):
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        if value < AMOUNT_SIZED:
            continue
        known = amounts | allowed_numbers
        if not any(math.isclose(value, item, rel_tol=1e-9, abs_tol=1e-6) for item in known):
            raise EvidenceValidationError("prose states a number absent from evidence")


def verify_draft(
    draft: LlmReportDraft,
    builder: EvidenceBuilder,
    *,
    allowed_numbers: set[float],
) -> None:
    """Raise ``EvidenceValidationError`` unless every claim is backed by evidence."""
    transaction_ids = set(builder.transactions)
    account_keys = set(builder.accounts)
    validate_report(
        draft.supporting_transaction_ids,
        draft.counterevidence_ids,
        transaction_ids,
        [
            {"transaction_ids": path.transaction_ids, "account_keys": path.account_keys}
            for path in draft.cited_paths
        ],
        account_keys,
    )
    for claim in draft.cited_claims:
        record = builder.transactions.get(claim.transaction_id)
        if record is None:
            raise EvidenceValidationError("claim cites a transaction absent from evidence")
        _check_claim(claim, record)
    returned_paths = {(tuple(p.transaction_ids), tuple(p.account_keys)) for p in builder.paths}
    for cited in draft.cited_paths:
        if (tuple(cited.transaction_ids), tuple(cited.account_keys)) not in returned_paths:
            raise EvidenceValidationError("cited path was not returned by a query")
    counts = {
        float(len(builder.transactions)),
        float(len(builder.accounts)),
        float(len(builder.paths)),
    }
    _check_prose(draft.hypothesis, builder, allowed_numbers | counts)
    if draft.action_reason:
        _check_prose(draft.action_reason, builder, allowed_numbers | counts)
