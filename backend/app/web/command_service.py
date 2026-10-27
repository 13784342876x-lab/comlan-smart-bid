from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from app.technical_fulltext_composer import load_json_object, sha256_file
from app.technical_fulltext_human_review import (
    PATCH_SCHEMA_VERSION,
    TechnicalFulltextHumanReviewError,
    TechnicalFulltextHumanReviewService,
    _stable_id,
)
from app.technical_fulltext_human_review_finalization import (
    accepted_machine_revision_instruction,
    TechnicalFulltextHumanReviewFinalizationError,
    TechnicalFulltextHumanReviewFinalizer,
)
from app.technical_review_projection import semantic_confirmation_source_ids
from app.web.command_dtos import (
    FactConfirmationCommandDTO,
    IssueDecisionCommandDTO,
    MaterialVerificationCommandDTO,
    RiskAcceptanceCommandDTO,
)
from app.web.dtos import ReviewMutationResultDTO
from app.web.project_resolver import ReadOnlyProjectResolver
from app.web.query_service import ProjectReadService


class WebCommandError(RuntimeError):
    pass


class PreconditionRequiredError(WebCommandError):
    pass


class StaleReviewError(WebCommandError):
    pass


class ReviewSourceNotFoundError(WebCommandError):
    pass


class ReviewCommandValidationError(WebCommandError):
    pass


class ReviewCommandService:
    """Translate HTTP commands into canonical Stage12-B patches and re-project state."""

    def __init__(self, resolver: ReadOnlyProjectResolver) -> None:
        self.resolver = resolver
        self.read_service = ProjectReadService(resolver)
        self.human_service = TechnicalFulltextHumanReviewService()
        self.finalizer = TechnicalFulltextHumanReviewFinalizer()

    def current_token(self, project_id: str) -> str:
        artifacts = self.resolver.resolve(project_id)
        return sha256_file(artifacts.human_review_json)

    def decide_issue(
        self, project_id: str, source_id: str, command: IssueDecisionCommandDTO, if_match: str | None
    ) -> ReviewMutationResultDTO:
        artifacts, human, token = self._load_bound(project_id, if_match)
        record = self._find(human.get("issue_decisions") or [], "source_id", source_id, "issue")
        if command.decision == "accepted":
            package = load_json_object(
                artifacts.review_package_json, label="Stage12-A review package"
            )
            review_item = self._find(
                package.get("review_items") or [], "source_id", source_id, "issue"
            )
            if not accepted_machine_revision_instruction(review_item):
                raise ReviewCommandValidationError(
                    "当前审核项没有可执行的机器修改建议，请选择自定义修改"
                )
        update = {
            "source_type": record["source_type"],
            "source_id": source_id,
            "decision": command.decision,
            "rejection_reason": command.rejection_reason,
            "revision_instruction": command.revision_instruction,
            "optional_comment": command.optional_comment,
            "use_machine_location": command.use_machine_location,
            "location_override": command.location_override.model_dump(exclude_none=True)
            if command.location_override
            else None,
            "location_override_reason": command.location_override_reason,
            "risk_acceptance_ref": None,
        }
        self._with_change_reason(update, command.change_reason, record.get("decision") != "pending")
        return self._apply(artifacts, human, token, issue_decisions=[update], updated_kind="issue", updated_id=source_id)

    def confirm_fact(
        self, project_id: str, source_id: str, command: FactConfirmationCommandDTO, if_match: str | None
    ) -> ReviewMutationResultDTO:
        artifacts, human, token = self._load_bound(project_id, if_match)
        self._find(human.get("fact_confirmations") or [], "source_id", source_id, "fact")
        package = load_json_object(
            artifacts.review_package_json, label="Stage12-A review package"
        )
        semantic_ids = semantic_confirmation_source_ids(
            package, human, artifacts.review_package_json, source_id
        )
        records = {
            str(item.get("source_id") or ""): item
            for item in human.get("fact_confirmations") or []
        }
        updates: list[dict[str, Any]] = []
        for semantic_id in semantic_ids:
            record = records[semantic_id]
            update = {
                "source_type": record["source_type"],
                "source_id": semantic_id,
                "confirmation_status": command.confirmation_status,
                "confirmed_value": command.confirmed_value,
                "source_description": command.source_description,
                "confirmation_basis": command.confirmation_basis,
                "confirmation_note": command.confirmation_note,
                "rejection_reason": command.rejection_reason,
            }
            self._with_change_reason(
                update,
                command.change_reason,
                record.get("confirmation_status") != "pending",
            )
            updates.append(update)
        return self._apply(
            artifacts,
            human,
            token,
            fact_confirmations=updates,
            updated_kind="fact",
            updated_id=source_id,
        )

    def verify_material(
        self, project_id: str, source_id: str, command: MaterialVerificationCommandDTO, if_match: str | None
    ) -> ReviewMutationResultDTO:
        artifacts, human, token = self._load_bound(project_id, if_match)
        record = self._find(
            human.get("material_verifications") or [], "material_or_evidence_ref", source_id, "material"
        )
        verifiable_upstream = {"candidate", "candidate_found", "provided", "available", "verified"}
        has_reference = bool(record.get("candidate_reference") or record.get("file_reference"))
        if record.get("upstream_status") not in verifiable_upstream and not has_reference:
            raise ReviewCommandValidationError("当前没有可核验的候选材料；尚未查询不是材料不存在")
        update = {
            "source_type": record["source_type"],
            "material_or_evidence_ref": source_id,
            "verification_status": command.verification_status,
            "material_name": command.material_name,
            "subject_match": command.subject_match,
            "validity_date": command.validity_date,
            "verification_note": command.verification_note,
            "candidate_reference": command.candidate_reference,
            "file_reference": command.file_reference,
        }
        self._with_change_reason(update, command.change_reason, record.get("verification_status") != "pending")
        return self._apply(artifacts, human, token, material_verifications=[update], updated_kind="material", updated_id=source_id)

    def accept_risk(
        self, project_id: str, source_id: str, command: RiskAcceptanceCommandDTO, if_match: str | None
    ) -> ReviewMutationResultDTO:
        artifacts, human, token = self._load_bound(project_id, if_match)
        record = self._find(human.get("issue_decisions") or [], "source_id", source_id, "issue")
        risk_id = _stable_id("HRA-", {"source_refs": [source_id], "scope": command.scope})
        issue_update: dict[str, Any] = {
            "source_type": record["source_type"],
            "source_id": source_id,
            "decision": "accepted",
            "rejection_reason": None,
            "revision_instruction": None,
            "optional_comment": command.optional_comment,
            "use_machine_location": True,
            "location_override": None,
            "location_override_reason": None,
            "risk_acceptance_ref": risk_id,
        }
        risk_update: dict[str, Any] = {
            "risk_acceptance_id": risk_id,
            "source_refs": [source_id],
            "accepted": True,
            "reason": command.reason,
            "scope": command.scope,
        }
        initialized = record.get("decision") != "pending"
        self._with_change_reason(issue_update, command.change_reason, initialized)
        existing_risk = next((item for item in human.get("risk_acceptances") or [] if item.get("risk_acceptance_id") == risk_id), None)
        self._with_change_reason(risk_update, command.change_reason, existing_risk is not None)
        return self._apply(
            artifacts,
            human,
            token,
            issue_decisions=[issue_update],
            risk_acceptances=[risk_update],
            updated_kind="risk",
            updated_id=risk_id,
        )

    def _load_bound(self, project_id: str, if_match: str | None):
        if not if_match:
            raise PreconditionRequiredError("Human Review mutation requires If-Match")
        artifacts = self.resolver.resolve(project_id)
        human = load_json_object(artifacts.human_review_json, label="Stage12-B human review")
        current = sha256_file(artifacts.human_review_json)
        supplied = self._normalize_etag(if_match)
        if supplied != current:
            raise StaleReviewError("审核内容已发生变化，请刷新最新审核后重试")
        return artifacts, human, current

    @staticmethod
    def _normalize_etag(value: str) -> str:
        value = value.strip()
        if value.startswith("W/"):
            value = value[2:].strip()
        return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value

    @staticmethod
    def _find(records: list[dict[str, Any]], key: str, source_id: str, label: str) -> dict[str, Any]:
        match = next((item for item in records if str(item.get(key)) == source_id), None)
        if match is None:
            raise ReviewSourceNotFoundError(f"{label} source is not part of this project review: {source_id}")
        return match

    @staticmethod
    def _with_change_reason(update: dict[str, Any], reason: str | None, required: bool) -> None:
        if reason:
            update["change_reason"] = reason
        elif required:
            raise ReviewCommandValidationError("修改已有人工结论时必须填写变更原因")

    def _apply(
        self,
        artifacts,
        human: dict[str, Any],
        base_token: str,
        *,
        issue_decisions: list[dict[str, Any]] | None = None,
        fact_confirmations: list[dict[str, Any]] | None = None,
        material_verifications: list[dict[str, Any]] | None = None,
        risk_acceptances: list[dict[str, Any]] | None = None,
        updated_kind: str,
        updated_id: str,
    ) -> ReviewMutationResultDTO:
        patch = {
            "schema_version": PATCH_SCHEMA_VERSION,
            "human_review_id": human["artifact_identity"]["human_review_id"],
            "base_human_review_sha256": base_token,
            "review_package_id": human["review_package_ref"]["review_package_id"],
            "review_package_sha256": human["review_package_ref"]["review_package_sha256"],
            "audited_content_sha256": human["audited_content_sha256"],
            "issue_decisions": issue_decisions or [],
            "fact_confirmations": fact_confirmations or [],
            "material_verifications": material_verifications or [],
            "risk_acceptances": risk_acceptances or [],
        }
        try:
            with tempfile.TemporaryDirectory(prefix="comlan_web_review_") as temp_dir:
                patch_path = Path(temp_dir) / "human_review.patch.json"
                patch_path.write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8")
                self.human_service.apply(
                    human_review_json_path=artifacts.human_review_json,
                    patch_json_path=patch_path,
                )
            self.finalizer.finalize(
                review_package_json_path=artifacts.review_package_json,
                human_review_json_path=artifacts.human_review_json,
                output_dir=artifacts.human_review_json.parent,
            )
        except TechnicalFulltextHumanReviewError as exc:
            message = str(exc)
            if "changed since" in message or "concurrently" in message or "lineage" in message:
                raise StaleReviewError("审核内容已发生变化，请刷新最新审核后重试") from exc
            raise ReviewCommandValidationError(self._public_message(message)) from exc
        except TechnicalFulltextHumanReviewFinalizationError as exc:
            raise WebCommandError("Stage12 finalization failed after review mutation") from exc
        new_token = sha256_file(artifacts.human_review_json)
        return ReviewMutationResultDTO(
            updated_kind=updated_kind,
            updated_id=updated_id,
            version_token=new_token,
            review=self.read_service.review(artifacts.project_id),
            overview=self.read_service.overview(artifacts.project_id),
        )

    @staticmethod
    def _public_message(message: str) -> str:
        return re.sub(r"[A-Za-z]:[\\/][^\n]+", "[server path]", message)
