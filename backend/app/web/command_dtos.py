from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr


class CommandDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocationOverrideCommandDTO(CommandDTO):
    chapter_id: str | None = None
    section_id: str | None = None
    paragraph_id: str | None = None
    location_note: str | None = None


class IssueDecisionCommandDTO(CommandDTO):
    decision: Literal["accepted", "modified", "rejected"]
    revision_instruction: str | None = None
    rejection_reason: str | None = None
    optional_comment: str | None = None
    use_machine_location: bool = True
    location_override: LocationOverrideCommandDTO | None = None
    location_override_reason: str | None = None
    change_reason: str | None = None


class FactConfirmationCommandDTO(CommandDTO):
    confirmation_status: Literal["confirmed", "rejected"]
    confirmed_value: Any | None = None
    source_description: str | None = None
    confirmation_basis: Literal[
        "human_provided", "document_verified", "explicitly_confirmed"
    ] | None = None
    confirmation_note: str | None = None
    rejection_reason: str | None = None
    change_reason: str | None = None


class MaterialVerificationCommandDTO(CommandDTO):
    verification_status: Literal["verified", "invalid", "expired", "not_provided"]
    material_name: str | None = None
    subject_match: bool | None = None
    validity_date: str | None = None
    verification_note: str | None = None
    candidate_reference: str | None = None
    file_reference: str | None = None
    change_reason: str | None = None


class RiskAcceptanceCommandDTO(CommandDTO):
    reason: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    optional_comment: str | None = None
    confirmed: Literal[True]
    change_reason: str | None = None


class AIModifyCommandDTO(CommandDTO):
    source_type: str = Field(min_length=1, max_length=100)


class ProjectFactCommandDTO(CommandDTO):
    status: Literal["confirmed"]
    value: Any
    source_description: str = Field(min_length=1, max_length=4000)
    confirmation_basis: str = Field(min_length=1, max_length=4000)
    note: str | None = Field(default=None, max_length=4000)
    change_reason: str | None = Field(default=None, max_length=4000)


class HumanConfirmationCommandDTO(CommandDTO):
    answer: StrictBool | StrictStr
    source_description: str = Field(min_length=1, max_length=4000)
    confirmation_basis: str = Field(min_length=1, max_length=4000)
    note: str | None = Field(default=None, max_length=4000)
    change_reason: str | None = Field(default=None, max_length=4000)


class WritingGenerationCommandDTO(CommandDTO):
    expected_input_fingerprint: str = Field(min_length=64, max_length=64)
