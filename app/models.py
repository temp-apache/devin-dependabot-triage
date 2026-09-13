"""The contract between the Devin session and this service."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Decision(str, Enum):
    APPROVE_AND_MERGE = "approve_and_merge"
    DECLINE = "decline"
    ESCALATE = "escalate"


class ReviewResult(BaseModel):
    """Structured output requested from the Devin session."""

    decision: Decision
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(description="one or two sentences, posted as the review body")
    evidence: list[str] = Field(
        default_factory=list,
        description="concrete findings: file:line references, command output, version ranges",
    )
    merged_by_devin: bool = False


#: JSON Schema (Draft 7) handed to the Devin API. Kept in sync with ReviewResult by
#: ``test_schema_matches_the_model``.
REVIEW_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "confidence", "summary", "evidence"],
    "properties": {
        "decision": {
            "type": "string",
            "enum": [d.value for d in Decision],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "merged_by_devin": {"type": "boolean"},
    },
}
