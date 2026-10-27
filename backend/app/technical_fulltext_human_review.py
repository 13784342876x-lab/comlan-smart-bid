from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from app.technical_fulltext_composer import (
    atomic_write_json,
    atomic_write_markdown,
    load_json_object,
    sha256_file,
    stable_json_sha256,
    utc_now,
)
from app.technical_fulltext_review_package import (
    SCHEMA_VERSION as REVIEW_PACKAGE_SCHEMA_VERSION,
    TechnicalFulltextReviewPackageAssembler,
)


SCHEMA_VERSION = "technical_fulltext_human_review_v1"
PATCH_SCHEMA_VERSION = "technical_fulltext_human_review_patch_v1"
MARKDOWN_NOTICE = (
    "Stage12-B human review records are human-input overlays; "
    "machine findings, severity, disposition, scoring, and response content remain immutable."
)


class TechnicalFulltextHumanReviewError(RuntimeError):
    """Raised when a Stage12-B artifact or update violates its bound package."""


def _unique(values: Iterable[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in values
            if str(value or "").strip()
        )
    )


def _clean_optional(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _stable_id(prefix: str, value: Any) -> str:
    return prefix + stable_json_sha256(value)[:16].upper()


def _editable_snapshot(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: deepcopy(record.get(field)) for field in fields}


class TechnicalFulltextHumanReviewService:
    """Initialize and update the version-bound Stage12-B human input overlay."""

    ISSUE_EDITABLE_FIELDS = (
        "decision",
        "rejection_reason",
        "revision_instruction",
        "optional_comment",
        "use_machine_location",
        "location_override",
        "location_override_reason",
        "risk_acceptance_ref",
    )
    FACT_EDITABLE_FIELDS = (
        "confirmation_status",
        "confirmed_value",
        "source_description",
        "confirmation_basis",
        "confirmation_note",
        "rejection_reason",
    )
    MATERIAL_EDITABLE_FIELDS = (
        "verification_status",
        "material_name",
        "subject_match",
        "validity_date",
        "verification_note",
        "candidate_reference",
        "file_reference",
    )
    RISK_EDITABLE_FIELDS = ("source_refs", "accepted", "reason", "scope")

    def __init__(
        self,
        *,
        schema_path: str | Path | None = None,
        patch_schema_path: str | Path | None = None,
    ):
        schema_root = Path(__file__).resolve().parent.parent / "schemas"
        self.schema_path = (
            Path(schema_path).expanduser().resolve()
            if schema_path is not None
            else schema_root / "technical_fulltext_human_review_v1.json"
        )
        self.patch_schema_path = (
            Path(patch_schema_path).expanduser().resolve()
            if patch_schema_path is not None
            else schema_root / "technical_fulltext_human_review_patch_v1.json"
        )

    def initialize(
        self,
        *,
        review_package_json_path: str | Path,
        output_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        package_path, package, package_sha = self._load_review_package(
            review_package_json_path
        )
        target_dir = (
            Path(output_dir).expanduser().resolve()
            if output_dir is not None
            else package_path.parent
        )
        json_path = target_dir / "technical_fulltext.human_review.json"
        markdown_path = target_dir / "technical_fulltext.human_review.md"

        if json_path.exists():
            existing = load_json_object(json_path, label="Stage12-B human review")
            self._validate_payload(existing)
            try:
                self._validate_package_binding(existing, package_path, package, package_sha)
            except TechnicalFulltextHumanReviewError:
                # Never silently rebind an existing human overlay after a
                # Stage12-A lineage/contract refresh. Preserve it first.
                self._archive_stale_review_lineage(
                    target_dir=target_dir,
                    existing=existing,
                    json_path=json_path,
                    markdown_path=markdown_path,
                )
            else:
                return self._result(
                    status="already_initialized",
                    payload=existing,
                    json_path=json_path,
                    markdown_path=markdown_path,
                )

        now = utc_now()
        identity = package["artifact_identity"]
        review_items = package.get("review_items") or []
        issue_decisions = []
        seen_issues: set[tuple[str, str]] = set()
        for item in review_items:
            source_type = str(item.get("source_type") or "")
            if source_type not in {"FAI", "FSF"}:
                continue
            key = (source_type, str(item["source_id"]))
            if key in seen_issues:
                raise TechnicalFulltextHumanReviewError(
                    f"duplicate Stage12-A issue source: {key}"
                )
            seen_issues.add(key)
            issue_decisions.append(
                {
                    "source_type": source_type,
                    "source_id": key[1],
                    "decision": "pending",
                    "rejection_reason": None,
                    "revision_instruction": None,
                    "optional_comment": None,
                    "use_machine_location": True,
                    "location_override": None,
                    "location_override_reason": None,
                    "risk_acceptance_ref": None,
                    "created_at": now,
                    "updated_at": now,
                    "change_history": [],
                }
            )

        fact_sources: dict[tuple[str, str], dict[str, Any]] = {}
        for item in review_items:
            if item.get("source_type") == "FCA":
                key = ("FCA", str(item["source_id"]))
                fact_sources[key] = {
                    "source_type": "FCA",
                    "source_id": key[1],
                    "fact_key": None,
                }
        for item in package.get("fact_material_queue") or []:
            source_type = str(item.get("source_type") or "")
            if source_type not in {"FCA", "FACT"}:
                continue
            key = (source_type, str(item["source_id"]))
            fact_sources.setdefault(
                key,
                {
                    "source_type": source_type,
                    "source_id": key[1],
                    "fact_key": key[1] if source_type == "FACT" else None,
                },
            )
        fact_confirmations = [
            {
                **source,
                "confirmation_status": "pending",
                "confirmed_value": None,
                "source_description": None,
                "confirmation_basis": None,
                "confirmation_note": None,
                "rejection_reason": None,
                "created_at": now,
                "updated_at": now,
                "change_history": [],
            }
            for _, source in sorted(fact_sources.items())
        ]

        material_verifications = []
        seen_materials: set[tuple[str, str]] = set()
        for item in package.get("fact_material_queue") or []:
            source_type = str(item.get("source_type") or "")
            if source_type not in {"EVIDENCE_QUERY", "EVIDENCE"}:
                continue
            source_id = str(item["source_id"])
            key = (source_type, source_id)
            if key in seen_materials:
                continue
            seen_materials.add(key)
            upstream_status = str(item.get("status") or "")
            material_verifications.append(
                {
                    "source_type": source_type,
                    "material_or_evidence_ref": source_id,
                    "upstream_status": upstream_status,
                    "verification_status": "pending",
                    "material_name": None,
                    "subject_match": None,
                    "validity_date": None,
                    "verification_note": None,
                    "candidate_reference": None,
                    "file_reference": None,
                    "created_at": now,
                    "updated_at": now,
                    "change_history": [],
                }
            )

        payload = {
            "schema_version": SCHEMA_VERSION,
            "artifact_identity": {
                "human_review_id": _stable_id(
                    "THR-",
                    {
                        "review_package_id": identity["review_package_id"],
                        "review_package_sha256": package_sha,
                    },
                ),
                "document_id": identity["document_id"],
                "output_key": identity["output_key"],
                "stage": "Stage12-B",
                "status": "in_progress",
            },
            "review_package_ref": {
                "path": str(package_path),
                "schema_version": REVIEW_PACKAGE_SCHEMA_VERSION,
                "review_package_id": identity["review_package_id"],
                "review_package_sha256": package_sha,
            },
            "stage11_audit_sha256": package["source_lineage"][
                "final_stage11_audit"
            ]["file_sha256"],
            "scoring_assessment_sha256": package["source_lineage"][
                "stage11_scoring_assessment"
            ]["file_sha256"],
            "audited_content_sha256": package["reviewed_content_sha256"],
            "created_at": now,
            "updated_at": now,
            "issue_decisions": issue_decisions,
            "fact_confirmations": fact_confirmations,
            "material_verifications": material_verifications,
            "risk_acceptances": [],
            "summary": {},
            "pre_stage13_dependencies": deepcopy(
                package.get("pre_stage13_dependencies") or []
            ),
        }
        payload["summary"] = self._summary(payload)
        self._validate_payload(payload)
        self._verify_package_unchanged(package_path, package_sha)
        return self._persist(
            status="initialized",
            payload=payload,
            json_path=json_path,
            markdown_path=markdown_path,
        )

    @staticmethod
    def _archive_stale_review_lineage(
        *,
        target_dir: Path,
        existing: dict[str, Any],
        json_path: Path,
        markdown_path: Path,
    ) -> None:
        identity = existing.get("artifact_identity") or {}
        package_ref = existing.get("review_package_ref") or {}
        archive_dir = target_dir / "history" / (
            f"{identity.get('human_review_id', 'THR-STALE')}_"
            f"package_{str(package_ref.get('review_package_sha256') or '')[:8]}"
        )
        archive_dir.mkdir(parents=True, exist_ok=True)
        for path in (
            json_path,
            markdown_path,
            target_dir / "technical_fulltext.review_finalization.json",
            target_dir / "technical_fulltext.review_finalization.md",
            target_dir / "technical_fulltext.stage13_handoff.json",
        ):
            if path.exists():
                shutil.copy2(path, archive_dir / path.name)

    def apply(
        self,
        *,
        human_review_json_path: str | Path,
        patch_json_path: str | Path,
    ) -> dict[str, Any]:
        review_path = Path(human_review_json_path).expanduser().resolve()
        patch_path = Path(patch_json_path).expanduser().resolve()
        review = load_json_object(review_path, label="Stage12-B human review")
        patch = load_json_object(patch_path, label="Stage12-B human review patch")
        self._validate_payload(review)
        self._validate_patch(patch)
        review_sha = sha256_file(review_path)
        if patch["base_human_review_sha256"] != review_sha:
            raise TechnicalFulltextHumanReviewError(
                "human review changed since the patch was prepared"
            )
        self._validate_patch_binding(review, patch)

        package_ref = review["review_package_ref"]
        package_path, package, package_sha = self._load_review_package(
            package_ref["path"]
        )
        self._validate_package_binding(review, package_path, package, package_sha)
        allowed_refs = self._allowed_source_refs(package)

        updated = deepcopy(review)
        now = utc_now()
        self._apply_issue_updates(updated, patch.get("issue_decisions") or [], now)
        self._apply_fact_updates(updated, patch.get("fact_confirmations") or [], now)
        self._apply_material_updates(
            updated, patch.get("material_verifications") or [], now
        )
        self._apply_risk_updates(
            updated,
            patch.get("risk_acceptances") or [],
            now,
            allowed_refs=allowed_refs,
        )
        self._validate_risk_links(updated)
        updated["updated_at"] = now
        updated["summary"] = self._summary(updated)
        self._validate_payload(updated)
        self._verify_package_unchanged(package_path, package_sha)
        if sha256_file(review_path) != review_sha:
            raise TechnicalFulltextHumanReviewError(
                "human review changed concurrently during update"
            )
        markdown_path = review_path.with_name("technical_fulltext.human_review.md")
        return self._persist(
            status="updated",
            payload=updated,
            json_path=review_path,
            markdown_path=markdown_path,
        )

    def inspect(self, human_review_json_path: str | Path) -> dict[str, Any]:
        path = Path(human_review_json_path).expanduser().resolve()
        payload = load_json_object(path, label="Stage12-B human review")
        self._validate_payload(payload)
        package_path, package, package_sha = self._load_review_package(
            payload["review_package_ref"]["path"]
        )
        self._validate_package_binding(payload, package_path, package, package_sha)
        return {
            "schema_version": payload["schema_version"],
            "artifact_identity": deepcopy(payload["artifact_identity"]),
            "review_package_ref": deepcopy(payload["review_package_ref"]),
            "audited_content_sha256": payload["audited_content_sha256"],
            "summary": deepcopy(payload["summary"]),
            "updated_at": payload["updated_at"],
            "human_review_sha256": sha256_file(path),
            "stale": False,
        }

    @staticmethod
    def _allowed_source_refs(package: dict[str, Any]) -> set[str]:
        refs: set[str] = set()
        for item in package.get("review_items") or []:
            refs.add(str(item.get("source_id") or ""))
            refs.update(_unique(item.get("requirement_ids") or []))
            refs.update(_unique(item.get("finding_refs") or []))
            refs.update(_unique(item.get("confirmation_refs") or []))
        for item in package.get("response_matrix") or []:
            refs.add(str(item.get("requirement_id") or ""))
        for item in package.get("scoring_matrix") or []:
            refs.add(str(item.get("score_item_id") or ""))
        for item in package.get("fact_material_queue") or []:
            refs.add(str(item.get("source_id") or ""))
        refs.discard("")
        return refs

    def _apply_issue_updates(
        self, payload: dict[str, Any], updates: list[dict[str, Any]], now: str
    ) -> None:
        records = {
            (item["source_type"], item["source_id"]): item
            for item in payload["issue_decisions"]
        }
        seen: set[tuple[str, str]] = set()
        for update in updates:
            key = (update["source_type"], update["source_id"])
            if key in seen:
                raise TechnicalFulltextHumanReviewError(
                    f"duplicate issue update in one patch: {key}"
                )
            seen.add(key)
            if key not in records:
                raise TechnicalFulltextHumanReviewError(
                    f"issue source is not in the bound review package: {key}"
                )
            record = records[key]
            self._apply_record_update(
                record,
                update,
                self.ISSUE_EDITABLE_FIELDS,
                now,
                initialized=self._issue_has_human_input(record),
            )
            self._validate_issue_decision(record)

    def _apply_fact_updates(
        self, payload: dict[str, Any], updates: list[dict[str, Any]], now: str
    ) -> None:
        records = {
            (item["source_type"], item["source_id"]): item
            for item in payload["fact_confirmations"]
        }
        seen: set[tuple[str, str]] = set()
        for update in updates:
            key = (update["source_type"], update["source_id"])
            if key in seen:
                raise TechnicalFulltextHumanReviewError(
                    f"duplicate fact confirmation update: {key}"
                )
            seen.add(key)
            if key not in records:
                raise TechnicalFulltextHumanReviewError(
                    f"fact/FCA source is not in the bound review package: {key}"
                )
            record = records[key]
            self._apply_record_update(
                record,
                update,
                self.FACT_EDITABLE_FIELDS,
                now,
                initialized=record["confirmation_status"] != "pending",
            )
            self._validate_fact_confirmation(record)

    def _apply_material_updates(
        self, payload: dict[str, Any], updates: list[dict[str, Any]], now: str
    ) -> None:
        records = {
            (item["source_type"], item["material_or_evidence_ref"]): item
            for item in payload["material_verifications"]
        }
        seen: set[tuple[str, str]] = set()
        for update in updates:
            key = (update["source_type"], update["material_or_evidence_ref"])
            if key in seen:
                raise TechnicalFulltextHumanReviewError(
                    f"duplicate material update: {key}"
                )
            seen.add(key)
            if key not in records:
                raise TechnicalFulltextHumanReviewError(
                    f"material/evidence source is not in the bound review package: {key}"
                )
            record = records[key]
            self._apply_record_update(
                record,
                update,
                self.MATERIAL_EDITABLE_FIELDS,
                now,
                initialized=record["verification_status"] != "pending",
            )
            self._validate_material_verification(record)

    def _apply_risk_updates(
        self,
        payload: dict[str, Any],
        updates: list[dict[str, Any]],
        now: str,
        *,
        allowed_refs: set[str],
    ) -> None:
        records = {
            item["risk_acceptance_id"]: item for item in payload["risk_acceptances"]
        }
        for update in updates:
            source_refs = sorted(_unique(update["source_refs"]))
            unknown = [value for value in source_refs if value not in allowed_refs]
            if unknown:
                raise TechnicalFulltextHumanReviewError(
                    f"risk acceptance references unknown package sources: {unknown}"
                )
            risk_id = update.get("risk_acceptance_id") or _stable_id(
                "HRA-", {"source_refs": source_refs, "scope": update["scope"]}
            )
            update = {**update, "risk_acceptance_id": risk_id, "source_refs": source_refs}
            if risk_id in records:
                record = records[risk_id]
                self._apply_record_update(
                    record,
                    update,
                    self.RISK_EDITABLE_FIELDS,
                    now,
                    initialized=True,
                )
            else:
                record = {
                    "risk_acceptance_id": risk_id,
                    "source_refs": source_refs,
                    "accepted": bool(update["accepted"]),
                    "reason": _clean_optional(update.get("reason")),
                    "scope": str(update["scope"]).strip(),
                    "created_at": now,
                    "updated_at": now,
                    "change_history": [],
                }
                payload["risk_acceptances"].append(record)
                records[risk_id] = record
            if record["accepted"] and not record.get("reason"):
                raise TechnicalFulltextHumanReviewError(
                    "accepted risk requires a non-empty reason"
                )

    @staticmethod
    def _apply_record_update(
        record: dict[str, Any],
        update: dict[str, Any],
        editable_fields: tuple[str, ...],
        now: str,
        *,
        initialized: bool,
    ) -> None:
        previous = _editable_snapshot(record, editable_fields)
        candidate = deepcopy(previous)
        for field in editable_fields:
            if field in update:
                candidate[field] = _clean_optional(update[field])
        if candidate == previous:
            return
        change_reason = _clean_optional(update.get("change_reason"))
        if initialized and not change_reason:
            raise TechnicalFulltextHumanReviewError(
                "updating an existing human input requires change_reason"
            )
        record["change_history"].append(
            {
                "changed_at": now,
                "change_reason": change_reason or "initial_human_input",
                "previous": previous,
            }
        )
        record.update(candidate)
        record["updated_at"] = now

    @staticmethod
    def _issue_has_human_input(record: dict[str, Any]) -> bool:
        return bool(
            record.get("decision") != "pending"
            or record.get("rejection_reason")
            or record.get("revision_instruction")
            or record.get("optional_comment")
            or record.get("location_override")
            or record.get("risk_acceptance_ref")
        )

    @staticmethod
    def _validate_issue_decision(record: dict[str, Any]) -> None:
        decision = record["decision"]
        if decision == "rejected" and not record.get("rejection_reason"):
            raise TechnicalFulltextHumanReviewError(
                "rejected issue decision requires rejection_reason"
            )
        if decision == "modified" and not record.get("revision_instruction"):
            raise TechnicalFulltextHumanReviewError(
                "modified issue decision requires revision_instruction"
            )
        if decision != "rejected" and record.get("rejection_reason"):
            raise TechnicalFulltextHumanReviewError(
                "rejection_reason is only valid for a rejected issue decision"
            )
        if decision != "modified" and record.get("revision_instruction"):
            raise TechnicalFulltextHumanReviewError(
                "revision_instruction is only valid for a modified issue decision"
            )
        override = record.get("location_override")
        if override is not None:
            if record.get("use_machine_location") is not False:
                raise TechnicalFulltextHumanReviewError(
                    "location_override requires use_machine_location=false"
                )
            if not record.get("location_override_reason"):
                raise TechnicalFulltextHumanReviewError(
                    "location_override requires location_override_reason"
                )
        elif record.get("use_machine_location") is not True or record.get(
            "location_override_reason"
        ):
            raise TechnicalFulltextHumanReviewError(
                "machine location remains the default unless an override is supplied"
            )

    @staticmethod
    def _validate_fact_confirmation(record: dict[str, Any]) -> None:
        status = record["confirmation_status"]
        if status == "confirmed":
            if record.get("confirmed_value") in (None, "", [], {}):
                raise TechnicalFulltextHumanReviewError(
                    "confirmed fact requires confirmed_value"
                )
            if not record.get("source_description") or record.get(
                "confirmation_basis"
            ) not in {"human_provided", "document_verified", "explicitly_confirmed"}:
                raise TechnicalFulltextHumanReviewError(
                    "confirmed fact requires a human source description and confirmation_basis"
                )
        elif record.get("confirmed_value") is not None or record.get(
            "confirmation_basis"
        ) is not None:
            raise TechnicalFulltextHumanReviewError(
                "only a confirmed fact may carry confirmed_value/confirmation_basis"
            )
        if status == "rejected" and not record.get("rejection_reason"):
            raise TechnicalFulltextHumanReviewError(
                "rejected fact confirmation requires rejection_reason"
            )
        if status != "rejected" and record.get("rejection_reason"):
            raise TechnicalFulltextHumanReviewError(
                "fact rejection_reason is only valid when status=rejected"
            )

    @staticmethod
    def _validate_material_verification(record: dict[str, Any]) -> None:
        status = record["verification_status"]
        if status == "verified":
            if not record.get("material_name"):
                raise TechnicalFulltextHumanReviewError(
                    "verified material requires material_name"
                )
            if record.get("subject_match") is not True:
                raise TechnicalFulltextHumanReviewError(
                    "verified material requires subject_match=true"
                )
            if not (record.get("candidate_reference") or record.get("file_reference")):
                raise TechnicalFulltextHumanReviewError(
                    "verified material requires a candidate or file reference"
                )
            if not record.get("verification_note"):
                raise TechnicalFulltextHumanReviewError(
                    "verified material requires verification_note"
                )
        if status == "pending" and record.get("upstream_status") == "query_not_executed":
            return

    @staticmethod
    def _validate_risk_links(payload: dict[str, Any]) -> None:
        risks = {
            item["risk_acceptance_id"]: item for item in payload["risk_acceptances"]
        }
        for decision in payload["issue_decisions"]:
            risk_ref = decision.get("risk_acceptance_ref")
            if not risk_ref:
                continue
            if risk_ref not in risks or risks[risk_ref].get("accepted") is not True:
                raise TechnicalFulltextHumanReviewError(
                    "issue risk_acceptance_ref must identify an accepted risk record"
                )
            if decision["source_id"] not in risks[risk_ref]["source_refs"]:
                raise TechnicalFulltextHumanReviewError(
                    "risk acceptance must reference the linked issue source"
                )
            if decision["decision"] == "rejected":
                raise TechnicalFulltextHumanReviewError(
                    "risk acceptance is not a rejected machine finding"
                )

    def _load_review_package(
        self, path: str | Path
    ) -> tuple[Path, dict[str, Any], str]:
        target = Path(path).expanduser().resolve()
        package = load_json_object(target, label="Stage12-A review package")
        TechnicalFulltextReviewPackageAssembler()._validate_payload(package)
        if package.get("schema_version") != REVIEW_PACKAGE_SCHEMA_VERSION:
            raise TechnicalFulltextHumanReviewError(
                "Stage12-B requires technical_fulltext_review_package_v1"
            )
        return target, package, sha256_file(target)

    @staticmethod
    def _validate_package_binding(
        review: dict[str, Any],
        package_path: Path,
        package: dict[str, Any],
        package_sha: str,
    ) -> None:
        ref = review["review_package_ref"]
        identity = package["artifact_identity"]
        checks = {
            "review package path": (str(package_path), str(Path(ref["path"]).resolve())),
            "review package ID": (identity["review_package_id"], ref["review_package_id"]),
            "review package SHA256": (package_sha, ref["review_package_sha256"]),
            "audited content SHA256": (
                package["reviewed_content_sha256"],
                review["audited_content_sha256"],
            ),
            "Stage11 audit SHA256": (
                package["source_lineage"]["final_stage11_audit"]["file_sha256"],
                review["stage11_audit_sha256"],
            ),
            "scoring assessment SHA256": (
                package["source_lineage"]["stage11_scoring_assessment"][
                    "file_sha256"
                ],
                review["scoring_assessment_sha256"],
            ),
        }
        for label, (actual, expected) in checks.items():
            if actual != expected:
                raise TechnicalFulltextHumanReviewError(
                    f"stale human review: {label} mismatch"
                )

    @staticmethod
    def _validate_patch_binding(review: dict[str, Any], patch: dict[str, Any]) -> None:
        expected = {
            "human_review_id": review["artifact_identity"]["human_review_id"],
            "review_package_id": review["review_package_ref"]["review_package_id"],
            "review_package_sha256": review["review_package_ref"][
                "review_package_sha256"
            ],
            "audited_content_sha256": review["audited_content_sha256"],
        }
        for field, value in expected.items():
            if patch[field] != value:
                raise TechnicalFulltextHumanReviewError(
                    f"patch binding mismatch: {field}"
                )

    @staticmethod
    def _verify_package_unchanged(path: Path, expected_sha: str) -> None:
        if sha256_file(path) != expected_sha:
            raise TechnicalFulltextHumanReviewError(
                "Stage12-A review package changed during Stage12-B operation"
            )

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        self._validate_against_schema(payload, self.schema_path, "human review")
        issue_keys = [
            (item["source_type"], item["source_id"])
            for item in payload["issue_decisions"]
        ]
        fact_keys = [
            (item["source_type"], item["source_id"])
            for item in payload["fact_confirmations"]
        ]
        material_keys = [
            (item["source_type"], item["material_or_evidence_ref"])
            for item in payload["material_verifications"]
        ]
        if len(issue_keys) != len(set(issue_keys)):
            raise TechnicalFulltextHumanReviewError("duplicate canonical issue decision")
        if len(fact_keys) != len(set(fact_keys)):
            raise TechnicalFulltextHumanReviewError("duplicate canonical fact/FCA confirmation")
        if len(material_keys) != len(set(material_keys)):
            raise TechnicalFulltextHumanReviewError("duplicate canonical material verification")
        for item in payload["issue_decisions"]:
            self._validate_issue_decision(item)
        for item in payload["fact_confirmations"]:
            self._validate_fact_confirmation(item)
        for item in payload["material_verifications"]:
            self._validate_material_verification(item)
        self._validate_risk_links(payload)

    def _validate_patch(self, payload: dict[str, Any]) -> None:
        self._validate_against_schema(payload, self.patch_schema_path, "human review patch")

    @staticmethod
    def _validate_against_schema(
        payload: dict[str, Any], schema_path: Path, label: str
    ) -> None:
        schema = load_json_object(schema_path, label=f"{label} schema")
        errors = sorted(
            Draft202012Validator(schema).iter_errors(payload),
            key=lambda item: list(item.path),
        )
        if errors:
            detail = "; ".join(
                f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
                for error in errors[:8]
            )
            raise TechnicalFulltextHumanReviewError(
                f"{label} schema validation failed: {detail}"
            )

    @staticmethod
    def _summary(payload: dict[str, Any]) -> dict[str, Any]:
        issue_counts = {name: 0 for name in ("pending", "accepted", "rejected", "modified")}
        for item in payload["issue_decisions"]:
            issue_counts[item["decision"]] += 1
        fact_counts = {name: 0 for name in ("pending", "confirmed", "rejected")}
        for item in payload["fact_confirmations"]:
            fact_counts[item["confirmation_status"]] += 1
        material_counts = {
            name: 0 for name in ("pending", "verified", "invalid", "expired", "not_provided")
        }
        for item in payload["material_verifications"]:
            material_counts[item["verification_status"]] += 1
        return {
            "issue_decision_count": len(payload["issue_decisions"]),
            "issue_decision_counts": issue_counts,
            "fai_decision_count": sum(
                item["source_type"] == "FAI" for item in payload["issue_decisions"]
            ),
            "fsf_decision_count": sum(
                item["source_type"] == "FSF" for item in payload["issue_decisions"]
            ),
            "fact_confirmation_count": len(payload["fact_confirmations"]),
            "fact_confirmation_counts": fact_counts,
            "fca_confirmation_count": sum(
                item["source_type"] == "FCA" for item in payload["fact_confirmations"]
            ),
            "project_fact_confirmation_count": sum(
                item["source_type"] == "FACT" for item in payload["fact_confirmations"]
            ),
            "material_verification_count": len(payload["material_verifications"]),
            "material_verification_counts": material_counts,
            "query_not_executed_count": sum(
                item["upstream_status"] == "query_not_executed"
                for item in payload["material_verifications"]
            ),
            "accepted_risk_count": sum(
                item["accepted"] is True for item in payload["risk_acceptances"]
            ),
            "risk_acceptance_record_count": len(payload["risk_acceptances"]),
        }

    def _persist(
        self,
        *,
        status: str,
        payload: dict[str, Any],
        json_path: Path,
        markdown_path: Path,
    ) -> dict[str, Any]:
        markdown = self._render_markdown(payload)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_sha = atomic_write_json(
            json_path,
            payload,
            required_fields=(
                "schema_version",
                "artifact_identity",
                "review_package_ref",
                "issue_decisions",
                "fact_confirmations",
                "material_verifications",
                "risk_acceptances",
                "summary",
            ),
        )
        markdown_sha = atomic_write_markdown(
            markdown_path,
            markdown,
            required_text=MARKDOWN_NOTICE,
        )
        return {
            "status": status,
            "human_review_id": payload["artifact_identity"]["human_review_id"],
            "summary": deepcopy(payload["summary"]),
            "outputs": {
                "technical_fulltext_human_review_json": str(json_path),
                "technical_fulltext_human_review_json_sha256": json_sha,
                "technical_fulltext_human_review_markdown": str(markdown_path),
                "technical_fulltext_human_review_markdown_sha256": markdown_sha,
            },
        }

    @staticmethod
    def _result(
        *,
        status: str,
        payload: dict[str, Any],
        json_path: Path,
        markdown_path: Path,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "human_review_id": payload["artifact_identity"]["human_review_id"],
            "summary": deepcopy(payload["summary"]),
            "outputs": {
                "technical_fulltext_human_review_json": str(json_path),
                "technical_fulltext_human_review_json_sha256": sha256_file(json_path),
                "technical_fulltext_human_review_markdown": str(markdown_path),
                "technical_fulltext_human_review_markdown_sha256": (
                    sha256_file(markdown_path) if markdown_path.exists() else None
                ),
            },
        }

    @staticmethod
    def _render_markdown(payload: dict[str, Any]) -> str:
        summary = payload["summary"]
        package = payload["review_package_ref"]
        lines = [
            "# 全文人工审核与决策记录",
            "",
            f"> {MARKDOWN_NOTICE}",
            f"> Review Package: `{package['review_package_id']}` / `{package['review_package_sha256']}`",
            f"> Audited content: `{payload['audited_content_sha256']}`",
            "",
            "## 当前状态",
            "",
            f"- 问题决策：{summary['issue_decision_count']}；pending {summary['issue_decision_counts']['pending']} / accepted {summary['issue_decision_counts']['accepted']} / rejected {summary['issue_decision_counts']['rejected']} / modified {summary['issue_decision_counts']['modified']}",
            f"- 事实确认：{summary['fact_confirmation_count']}；pending {summary['fact_confirmation_counts']['pending']} / confirmed {summary['fact_confirmation_counts']['confirmed']} / rejected {summary['fact_confirmation_counts']['rejected']}",
            f"- 材料核验：{summary['material_verification_count']}；pending {summary['material_verification_counts']['pending']} / verified {summary['material_verification_counts']['verified']}",
            f"- 主动风险接受：{summary['accepted_risk_count']}",
            "",
            "## Issue Decisions",
            "",
            "| Source | Decision | Location | Revision / Rejection | Risk |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in payload["issue_decisions"]:
            detail = item.get("revision_instruction") or item.get("rejection_reason") or "-"
            lines.append(
                f"| {item['source_type']}:{item['source_id']} | {item['decision']} | "
                f"{'machine' if item['use_machine_location'] else 'override'} | {detail} | "
                f"{item.get('risk_acceptance_ref') or '-'} |"
            )
        lines.extend(
            [
                "",
                "## Fact Confirmations",
                "",
                "| Source | Status | Value | Source description |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in payload["fact_confirmations"]:
            lines.append(
                f"| {item['source_type']}:{item['source_id']} | {item['confirmation_status']} | "
                f"{item.get('confirmed_value') or '-'} | {item.get('source_description') or '-'} |"
            )
        lines.extend(
            [
                "",
                "## Material Verifications",
                "",
                "| Source | Upstream | Verification | Material |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in payload["material_verifications"]:
            lines.append(
                f"| {item['source_type']}:{item['material_or_evidence_ref']} | "
                f"{item['upstream_status']} | {item['verification_status']} | "
                f"{item.get('material_name') or '-'} |"
            )
        lines.extend(
            [
                "",
                "## Risk Acceptances",
                "",
            ]
        )
        if not payload["risk_acceptances"]:
            lines.append("- 无。")
        for item in payload["risk_acceptances"]:
            lines.append(
                f"- `{item['risk_acceptance_id']}`: accepted={item['accepted']}; "
                f"scope={item['scope']}; refs={', '.join(item['source_refs'])}; "
                f"reason={item.get('reason') or '-'}"
            )
        lines.extend(
            [
                "",
                "## Pre-Stage13 Dependency",
                "",
                "- Stage11.2 iterative replay 的 requirement_location provider missing/duplicate unit ID 稳定性问题仍保持 open；本阶段未修复、未绕过、未放宽校验。",
                "",
            ]
        )
        return "\n".join(lines)


def inspect_technical_fulltext_human_review(path: str | Path) -> dict[str, Any]:
    return TechnicalFulltextHumanReviewService().inspect(path)
