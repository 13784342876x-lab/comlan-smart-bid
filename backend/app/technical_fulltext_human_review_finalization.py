from __future__ import annotations

import re
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
from app.technical_fulltext_human_review import (
    SCHEMA_VERSION as HUMAN_REVIEW_SCHEMA_VERSION,
    TechnicalFulltextHumanReviewService,
)
from app.technical_fulltext_review_package import (
    SCHEMA_VERSION as REVIEW_PACKAGE_SCHEMA_VERSION,
    TechnicalFulltextReviewPackageAssembler,
)
from app.technical_review_projection import active_issue_keys, confirmation_groups


SCHEMA_VERSION = "technical_fulltext_human_review_finalization_v1"


def accepted_machine_revision_instruction(review_item: dict[str, Any]) -> str | None:
    """Return the existing executable machine instruction for an accepted issue."""
    if str(review_item.get("handoff_mode") or "") not in {
        "revision",
        "structural_normalization",
    }:
        return None
    action = review_item.get("machine_recommended_action") or {}
    if not isinstance(action, dict):
        return None
    machine_instruction = str(action.get("text") or "").strip()
    if not machine_instruction:
        return None
    instruction = str(
        review_item.get("default_handoff_instruction") or machine_instruction
    ).strip()
    return instruction or None


HANDOFF_SCHEMA_VERSION = "technical_fulltext_stage13_handoff_v1"
MARKDOWN_NOTICE = (
    "Stage12-C is a deterministic projection of Stage12-A/B; it does not alter "
    "machine findings, human decisions, scoring baselines, or response content."
)

EVIDENCE_TYPE_TERMS: dict[str, tuple[str, ...]] = {
    "product_certificate": ("产品认证", "节能产品", "环境标志"),
    "manufacturer_qualification": ("制造商能力", "制造商资质", "制造商体系"),
    "company_qualification": ("投标人主体资质", "投标人资质", "企业资质"),
    "company_performance": ("类似项目业绩", "采用的业绩", "业绩证明"),
    "product_parameter_proof": ("产品参数证明", "产品检测", "检验或测试报告"),
    "green_supply_chain_evidence": ("绿色供应链", "节能环保"),
}
REVISION_KINDS = {
    "revise_content",
    "modify_response",
    "add_content",
    "repair",
    "unify_placeholders",
}
REVISION_TERMS = ("补充", "增加", "修改", "统一", "替换", "精简", "删除", "合并", "回填")


class TechnicalFulltextHumanReviewFinalizationError(RuntimeError):
    """Raised when Stage12-C cannot produce a lineage-safe projection."""


def _unique(values: Iterable[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in values
            if str(value or "").strip()
        )
    )


def _stable_id(prefix: str, value: Any) -> str:
    return prefix + stable_json_sha256(value)[:16].upper()


def _score(value: Any) -> float:
    return float(value or 0)


def _clean_score(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 4)


class TechnicalFulltextHumanReviewFinalizer:
    """Finalize Stage12 review status and emit a gated Stage13 handoff."""

    def __init__(
        self,
        *,
        schema_path: str | Path | None = None,
        handoff_schema_path: str | Path | None = None,
    ):
        schema_root = Path(__file__).resolve().parent.parent / "schemas"
        self.schema_path = (
            Path(schema_path).expanduser().resolve()
            if schema_path is not None
            else schema_root / "technical_fulltext_human_review_finalization_v1.json"
        )
        self.handoff_schema_path = (
            Path(handoff_schema_path).expanduser().resolve()
            if handoff_schema_path is not None
            else schema_root / "technical_fulltext_stage13_handoff_v1.json"
        )

    def finalize(
        self,
        *,
        review_package_json_path: str | Path,
        human_review_json_path: str | Path,
        output_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        package_path = Path(review_package_json_path).expanduser().resolve()
        human_path = Path(human_review_json_path).expanduser().resolve()
        package = load_json_object(package_path, label="Stage12-A review package")
        human = load_json_object(human_path, label="Stage12-B human review")
        package_sha = sha256_file(package_path)
        human_sha = sha256_file(human_path)
        TechnicalFulltextReviewPackageAssembler()._validate_payload(package)
        human_service = TechnicalFulltextHumanReviewService()
        human_service._validate_payload(human)
        if package.get("schema_version") != REVIEW_PACKAGE_SCHEMA_VERSION:
            raise TechnicalFulltextHumanReviewFinalizationError(
                "Stage12-C requires technical_fulltext_review_package_v1"
            )
        if human.get("schema_version") != HUMAN_REVIEW_SCHEMA_VERSION:
            raise TechnicalFulltextHumanReviewFinalizationError(
                "Stage12-C requires technical_fulltext_human_review_v1"
            )
        try:
            human_service._validate_package_binding(
                human, package_path, package, package_sha
            )
        except Exception as exc:
            raise TechnicalFulltextHumanReviewFinalizationError(
                f"Stage12-A/B lineage mismatch or stale Human Review: {exc}"
            ) from exc
        self._validate_stage11_sources(package)

        target_dir = (
            Path(output_dir).expanduser().resolve()
            if output_dir is not None
            else package_path.parent
        )
        json_path = target_dir / "technical_fulltext.review_finalization.json"
        markdown_path = target_dir / "technical_fulltext.review_finalization.md"
        handoff_path = target_dir / "technical_fulltext.stage13_handoff.json"

        active_keys = active_issue_keys(package, package_path)
        review_human = deepcopy(human)
        review_human["issue_decisions"] = [
            item
            for item in human.get("issue_decisions") or []
            if (str(item.get("source_type") or ""), str(item.get("source_id") or ""))
            in active_keys
        ]
        review_summary = self._review_status_summary(
            review_human, package, package_path
        )
        package_items = {
            (item["source_type"], item["source_id"]): item
            for item in package.get("review_items") or []
        }
        material_types = self._material_evidence_types(package, review_human)
        approved_facts = self._approved_facts(review_human)
        approved_materials = self._approved_materials(review_human, material_types)
        prohibited_inferences = self._prohibited_inferences(review_human)
        accepted_risks = deepcopy(
            [item for item in human.get("risk_acceptances") or [] if item["accepted"]]
        )
        scoring = self._human_adjusted_scoring(
            package=package,
            human=review_human,
            package_items=package_items,
            material_types=material_types,
        )
        candidates = self._revision_candidates(
            package=package,
            human=review_human,
            package_items=package_items,
            material_types=material_types,
            approved_facts=approved_facts,
            approved_materials=approved_materials,
            prohibited_inferences=prohibited_inferences,
        )
        gate = self._stage13_gate(
            human=review_human, candidates=candidates, package_items=package_items
        )

        generated_at = utc_now()
        finalization_id = _stable_id(
            "THF-",
            {
                "review_package_sha256": package_sha,
                "human_review_sha256": human_sha,
                "audited_content_sha256": human["audited_content_sha256"],
            },
        )
        lineage = {
            "stage12a_review_package": {
                "path": str(package_path),
                "schema_version": package["schema_version"],
                "review_package_id": package["artifact_identity"]["review_package_id"],
                "sha256": package_sha,
            },
            "stage12b_human_review": {
                "path": str(human_path),
                "schema_version": human["schema_version"],
                "human_review_id": human["artifact_identity"]["human_review_id"],
                "sha256": human_sha,
            },
            "stage11_audit_sha256": human["stage11_audit_sha256"],
            "stage11_scoring_sha256": human["scoring_assessment_sha256"],
            "audited_content_sha256": human["audited_content_sha256"],
        }
        payload = {
            "schema_version": SCHEMA_VERSION,
            "artifact_identity": {
                "finalization_id": finalization_id,
                "document_id": package["artifact_identity"]["document_id"],
                "output_key": package["artifact_identity"]["output_key"],
                "stage": "Stage12-C",
                "status": "completed",
            },
            "source_lineage": lineage,
            "review_status_summary": review_summary,
            "accepted_risk_items": accepted_risks,
            "approved_facts": approved_facts,
            "approved_material_refs": approved_materials,
            "prohibited_inferences": prohibited_inferences,
            "human_adjusted_bid_assessment": scoring,
            "revision_candidates": candidates,
            "stage13_gate": gate,
            "pre_stage13_dependencies": deepcopy(
                human.get("pre_stage13_dependencies") or []
            ),
            "generated_at": generated_at,
        }
        self._validate_payload(payload)

        handoff: dict[str, Any] | None = None
        if gate["stage13_ready"]:
            handoff = {
                "schema_version": HANDOFF_SCHEMA_VERSION,
                "artifact_identity": {
                    "handoff_id": _stable_id(
                        "S13H-",
                        {
                            "finalization_id": finalization_id,
                            "human_review_sha256": human_sha,
                        },
                    ),
                    "document_id": package["artifact_identity"]["document_id"],
                    "output_key": package["artifact_identity"]["output_key"],
                    "status": "ready",
                },
                "source_lineage": lineage,
                "finalization_ref": {
                    "finalization_id": finalization_id,
                    "path": str(json_path),
                },
                "revision_candidates": deepcopy(candidates),
                "approved_facts": deepcopy(approved_facts),
                "approved_material_refs": deepcopy(approved_materials),
                "prohibited_inferences": deepcopy(prohibited_inferences),
                "accepted_risk_items": deepcopy(accepted_risks),
                "summary": {
                    "revision_candidate_count": len(candidates),
                    "release_blocking_candidate_count": sum(
                        item["release_blocking"] for item in candidates
                    ),
                    "accepted_risk_count": len(accepted_risks),
                },
                "pre_stage13_dependencies": deepcopy(
                    human.get("pre_stage13_dependencies") or []
                ),
                "generated_at": generated_at,
            }
            self._validate_handoff(handoff)

        self._verify_inputs_unchanged(
            package_path=package_path,
            package_sha=package_sha,
            human_path=human_path,
            human_sha=human_sha,
        )
        json_path.parent.mkdir(parents=True, exist_ok=True)
        finalization_sha = atomic_write_json(
            json_path,
            payload,
            required_fields=(
                "schema_version",
                "artifact_identity",
                "source_lineage",
                "review_status_summary",
                "human_adjusted_bid_assessment",
                "revision_candidates",
                "stage13_gate",
            ),
        )
        markdown_sha = atomic_write_markdown(
            markdown_path,
            self._render_markdown(payload),
            required_text=MARKDOWN_NOTICE,
        )
        handoff_sha = None
        if handoff is not None:
            handoff["finalization_ref"]["sha256"] = finalization_sha
            self._validate_handoff(handoff)
            handoff_sha = atomic_write_json(
                handoff_path,
                handoff,
                required_fields=(
                    "schema_version",
                    "artifact_identity",
                    "source_lineage",
                    "revision_candidates",
                    "approved_facts",
                    "approved_material_refs",
                    "prohibited_inferences",
                    "summary",
                ),
            )
        outputs = {
            "technical_fulltext_review_finalization_json": str(json_path),
            "technical_fulltext_review_finalization_json_sha256": finalization_sha,
            "technical_fulltext_review_finalization_markdown": str(markdown_path),
            "technical_fulltext_review_finalization_markdown_sha256": markdown_sha,
            "technical_fulltext_stage13_handoff_json": str(handoff_path)
            if handoff is not None
            else None,
            "technical_fulltext_stage13_handoff_json_sha256": handoff_sha,
        }
        return {
            "status": "completed",
            "finalization_id": finalization_id,
            "stage13_ready": gate["stage13_ready"],
            "stage13_handoff_generated": handoff is not None,
            "review_status_summary": review_summary,
            "score_summary": deepcopy(scoring["score_summary"]),
            "stage13_gate": deepcopy(gate),
            "outputs": outputs,
        }

    @staticmethod
    def _review_status_summary(
        human: dict[str, Any], package: dict[str, Any], package_path: Path
    ) -> dict[str, Any]:
        issue_counts: dict[str, int] = {
            value: sum(
                item.get("decision") == value
                for item in human.get("issue_decisions") or []
            )
            for value in ("pending", "accepted", "rejected", "modified")
        }
        fact_groups = confirmation_groups(package, human, package_path)
        fact_counts: dict[str, int] = {
            value: sum(item["status"] == value for item in fact_groups)
            for value in ("pending", "confirmed", "rejected")
        }
        materials = list(human.get("material_verifications") or [])
        material_counts: dict[str, int] = {
            value: sum(item.get("verification_status") == value for item in materials)
            for value in ("pending", "verified", "invalid", "expired", "not_provided")
        }
        return {
            "total_issue_decisions": len(human.get("issue_decisions") or []),
            "pending_issue_decisions": issue_counts["pending"],
            "accepted_issue_decisions": issue_counts["accepted"],
            "rejected_issue_decisions": issue_counts["rejected"],
            "modified_issue_decisions": issue_counts["modified"],
            "total_fact_confirmations": len(fact_groups),
            "pending_facts": fact_counts["pending"],
            "confirmed_facts": fact_counts["confirmed"],
            "rejected_facts": fact_counts["rejected"],
            "total_material_verifications": len(materials),
            "pending_materials": material_counts["pending"],
            "verified_materials": material_counts["verified"],
            "invalid_materials": material_counts["invalid"],
            "expired_materials": material_counts["expired"],
            "not_provided_materials": material_counts["not_provided"],
            "accepted_risk_count": sum(
                bool(item.get("accepted")) for item in human.get("risk_acceptances") or []
            ),
        }

    @staticmethod
    def _material_evidence_types(
        package: dict[str, Any], human: dict[str, Any]
    ) -> dict[str, list[str]]:
        titles = {
            item["source_id"]: str(item.get("display_title") or "")
            for item in package.get("fact_material_queue") or []
            if item.get("source_type") in {"EVIDENCE_QUERY", "EVIDENCE"}
        }
        result: dict[str, list[str]] = {}
        for item in human.get("material_verifications") or []:
            ref = item["material_or_evidence_ref"]
            title = titles.get(ref, "")
            result[ref] = [
                evidence_type
                for evidence_type, terms in EVIDENCE_TYPE_TERMS.items()
                if any(term in title for term in terms)
            ]
        return result

    @staticmethod
    def _approved_facts(human: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "source_type": item["source_type"],
                "source_id": item["source_id"],
                "fact_key": item.get("fact_key"),
                "confirmed_value": deepcopy(item["confirmed_value"]),
                "source_description": item["source_description"],
                "confirmation_basis": item["confirmation_basis"],
                "confirmation_note": item.get("confirmation_note"),
            }
            for item in human.get("fact_confirmations") or []
            if item["confirmation_status"] == "confirmed"
        ]

    @staticmethod
    def _approved_materials(
        human: dict[str, Any], material_types: dict[str, list[str]]
    ) -> list[dict[str, Any]]:
        return [
            {
                "source_type": item["source_type"],
                "material_or_evidence_ref": item["material_or_evidence_ref"],
                "evidence_types": deepcopy(
                    material_types.get(item["material_or_evidence_ref"], [])
                ),
                "material_name": item["material_name"],
                "subject_match": item["subject_match"],
                "validity_date": item.get("validity_date"),
                "candidate_reference": item.get("candidate_reference"),
                "file_reference": item.get("file_reference"),
                "verification_note": item["verification_note"],
            }
            for item in human.get("material_verifications") or []
            if item["verification_status"] == "verified"
        ]

    @staticmethod
    def _prohibited_inferences(human: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in human.get("fact_confirmations") or []:
            if item["confirmation_status"] != "confirmed":
                result.append(
                    {
                        "source_type": item["source_type"],
                        "source_id": item["source_id"],
                        "reason": f"fact_{item['confirmation_status']}_must_not_be_inferred",
                    }
                )
        for item in human.get("material_verifications") or []:
            if item["verification_status"] != "verified":
                result.append(
                    {
                        "source_type": item["source_type"],
                        "source_id": item["material_or_evidence_ref"],
                        "reason": (
                            f"material_{item['verification_status']}_must_not_be_used_as_verified_evidence"
                        ),
                    }
                )
        return result

    def _human_adjusted_scoring(
        self,
        *,
        package: dict[str, Any],
        human: dict[str, Any],
        package_items: dict[tuple[str, str], dict[str, Any]],
        material_types: dict[str, list[str]],
    ) -> dict[str, Any]:
        decisions = {
            (item["source_type"], item["source_id"]): item
            for item in human.get("issue_decisions") or []
        }
        facts = {
            item["source_id"]: item for item in human.get("fact_confirmations") or []
        }
        materials = {
            item["material_or_evidence_ref"]: item
            for item in human.get("material_verifications") or []
        }
        risks = {
            item["risk_acceptance_id"]: item
            for item in human.get("risk_acceptances") or []
            if item["accepted"]
        }
        fca_by_placeholder: dict[str, str] = {}
        for (source_type, source_id), review_item in package_items.items():
            if source_type != "FCA":
                continue
            text = " ".join(
                [
                    str(review_item.get("display_title") or ""),
                    str(review_item.get("machine_issue_summary") or ""),
                ]
            )
            for placeholder in re.findall(r"PH-FIELD-[A-Z0-9-]+", text):
                fca_by_placeholder[placeholder] = source_id

        adjusted_items: list[dict[str, Any]] = []
        adjusted_confirmed_total = 0.0
        estimated_min = 0.0
        estimated_max = 0.0
        unresolved_exposure = 0.0
        for score_item in package.get("scoring_matrix") or []:
            finding_ids = set(score_item.get("finding_refs") or [])
            related_decisions = []
            for key, decision in decisions.items():
                review_item = package_items.get(key) or {}
                machine_refs = set(review_item.get("finding_refs") or []) | {
                    decision["source_id"]
                }
                if machine_refs & finding_ids:
                    related_decisions.append(decision)
            rejected_refs = [
                item["source_id"]
                for item in related_decisions
                if item["decision"] == "rejected"
            ]
            risk_refs = _unique(
                item["risk_acceptance_ref"]
                for item in related_decisions
                if item.get("risk_acceptance_ref") in risks
            )
            pending_fact_keys = [
                value.split(":", 1)[1]
                for value in score_item.get("pending_conditions") or []
                if value.startswith("fact_not_confirmed:")
            ]
            confirmed_fact_refs = _unique(
                fca_by_placeholder.get(value)
                for value in pending_fact_keys
                if fca_by_placeholder.get(value)
                and facts.get(fca_by_placeholder[value], {}).get(
                    "confirmation_status"
                )
                == "confirmed"
            )
            evidence_requirements = set(score_item.get("evidence_requirements") or [])
            verified_material_refs = [
                ref
                for ref, material in materials.items()
                if material["verification_status"] == "verified"
                and evidence_requirements.intersection(material_types.get(ref, []))
            ]

            machine_confirmed = _score(score_item.get("confirmed_score"))
            component = self._deterministic_verified_component(
                score_item=score_item,
                verified_material_count=len(verified_material_refs),
            )
            adjusted_confirmed = min(
                _score(score_item["max_score"]), machine_confirmed + component
            )
            adjusted_confirmed_total += adjusted_confirmed
            machine_range = score_item["score_range"]
            adjusted_range = {
                "min": _clean_score(max(_score(machine_range["min"]), adjusted_confirmed)),
                "max": _clean_score(max(_score(machine_range["max"]), adjusted_confirmed)),
            }
            method = score_item["assessment_method"]
            if method == "semantic":
                adjusted_status = "estimated"
            elif adjusted_confirmed >= _score(score_item["max_score"]):
                adjusted_status = "confirmed"
            elif component > 0:
                adjusted_status = "partially_confirmed"
            else:
                adjusted_status = score_item["assessment_status"]
            if score_item["assessment_status"] == "estimated":
                estimated_min += _score(adjusted_range["min"])
                estimated_max += _score(adjusted_range["max"])
            elif adjusted_status in {"pending", "partially_confirmed"}:
                unresolved_exposure += max(
                    0.0, _score(score_item["max_score"]) - adjusted_confirmed
                )
            adjusted_items.append(
                {
                    "score_item_id": score_item["score_item_id"],
                    "machine_score_assessment": {
                        "assessment_method": method,
                        "assessment_status": score_item["assessment_status"],
                        "max_score": score_item["max_score"],
                        "confirmed_score": score_item.get("confirmed_score"),
                        "estimated_score": score_item.get("estimated_score"),
                        "score_range": deepcopy(machine_range),
                        "machine_rationale": score_item["machine_rationale"],
                    },
                    "human_confirmed_inputs": {
                        "confirmed_fact_refs": confirmed_fact_refs,
                        "verified_material_refs": verified_material_refs,
                        "rejected_machine_finding_refs": rejected_refs,
                        "accepted_risk_refs": risk_refs,
                    },
                    "human_adjusted_score_status": {
                        "assessment_status": adjusted_status,
                        "confirmed_score_component": _clean_score(adjusted_confirmed),
                        "score_range": adjusted_range,
                        "adjustment_policy": (
                            "explicit_per_material_unit_v1" if component > 0 else "baseline_preserved"
                        ),
                    },
                }
            )
        machine_summary = package["summary"]
        score_summary = {
            "technical_score_max": machine_summary["technical_score_max"],
            "machine_confirmed_score": machine_summary["confirmed_score"],
            "machine_estimated_range": deepcopy(
                machine_summary["estimated_score_range"]
            ),
            "machine_pending_exposure": machine_summary["pending_score_max"],
            "human_adjusted_confirmed_score": _clean_score(adjusted_confirmed_total),
            "human_adjusted_estimated_range": {
                "min": _clean_score(estimated_min),
                "max": _clean_score(estimated_max),
            },
            "unresolved_score_exposure": _clean_score(unresolved_exposure),
            "score_potential_gain": deepcopy(machine_summary["potential_gain_range"]),
            "single_total_score_permitted": (
                unresolved_exposure == 0 and estimated_min == estimated_max
            ),
        }
        return {
            "policy_version": "human_adjusted_score_projection_v1",
            "machine_baseline_immutable": True,
            "score_items": adjusted_items,
            "score_summary": score_summary,
        }

    @staticmethod
    def _deterministic_verified_component(
        *, score_item: dict[str, Any], verified_material_count: int
    ) -> float:
        if verified_material_count <= 0:
            return 0.0
        if score_item["assessment_method"] == "semantic":
            return 0.0
        criteria = str(score_item.get("criteria_text") or "")
        match = re.search(r"每(?:提供)?一份[^。；;]{0,80}?得\s*(\d+(?:\.\d+)?)\s*分", criteria)
        if not match:
            return 0.0
        return min(
            _score(score_item["max_score"]),
            verified_material_count * float(match.group(1)),
        )

    def _revision_candidates(
        self,
        *,
        package: dict[str, Any],
        human: dict[str, Any],
        package_items: dict[tuple[str, str], dict[str, Any]],
        material_types: dict[str, list[str]],
        approved_facts: list[dict[str, Any]],
        approved_materials: list[dict[str, Any]],
        prohibited_inferences: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        fact_by_id = {
            item["source_id"]: item for item in human.get("fact_confirmations") or []
        }
        material_by_id = {
            item["material_or_evidence_ref"]: item
            for item in human.get("material_verifications") or []
        }
        approved_fact_by_id = {item["source_id"]: item for item in approved_facts}
        approved_material_by_id = {
            item["material_or_evidence_ref"]: item for item in approved_materials
        }
        prohibited_by_id = {
            (item["source_type"], item["source_id"]): item
            for item in prohibited_inferences
        }
        accepted_risk_ids = {
            item["risk_acceptance_id"]
            for item in human.get("risk_acceptances") or []
            if item["accepted"]
        }
        scoring_items = package.get("scoring_matrix") or []
        candidates: list[dict[str, Any]] = []
        for decision in human.get("issue_decisions") or []:
            if decision["decision"] not in {"accepted", "modified"}:
                continue
            review_item = package_items.get(
                (decision["source_type"], decision["source_id"])
            )
            if review_item is None:
                raise TechnicalFulltextHumanReviewFinalizationError(
                    f"human decision source missing from Stage12-A: {decision['source_id']}"
                )
            has_accepted_risk = decision.get("risk_acceptance_ref") in accepted_risk_ids
            if has_accepted_risk and decision["decision"] != "modified":
                continue
            action = review_item.get("machine_recommended_action") or {}
            handoff_mode = str(review_item.get("handoff_mode") or "")
            if decision["decision"] == "accepted" and handoff_mode not in {
                "revision",
                "structural_normalization",
            }:
                continue
            if (
                decision["decision"] == "modified"
                and handoff_mode == "informational"
                and not decision.get("location_override")
            ):
                raise TechnicalFulltextHumanReviewFinalizationError(
                    "informational/context finding requires an explicit location_override "
                    "before a modified Stage13 candidate can be created"
                )

            machine_refs = set(review_item.get("finding_refs") or []) | {
                decision["source_id"]
            }
            related_score_items = [
                item
                for item in scoring_items
                if machine_refs.intersection(item.get("finding_refs") or [])
            ]
            score_item_ids = [item["score_item_id"] for item in related_score_items]
            # Dependencies are a Stage12-A contract, never inferred indirectly
            # from score associations or generic evidence-query keywords.
            required_fact_ids = _unique(review_item.get("required_fact_refs") or [])
            required_material_ids = _unique(
                review_item.get("required_material_refs") or []
            )
            required_confirmation_ids = _unique(
                review_item.get("required_confirmation_refs") or []
            )
            targeted_fact_ids = _unique(
                [*required_fact_ids, *required_confirmation_ids]
            )
            candidate_approved_facts = [
                deepcopy(approved_fact_by_id[value])
                for value in targeted_fact_ids
                if value in approved_fact_by_id
            ]
            candidate_approved_materials = [
                deepcopy(approved_material_by_id[value])
                for value in required_material_ids
                if value in approved_material_by_id
            ]
            pending_fact_refs = [
                value
                for value in required_fact_ids
                if fact_by_id.get(value, {}).get("confirmation_status") != "confirmed"
            ]
            pending_confirmation_refs = [
                value
                for value in required_confirmation_ids
                if fact_by_id.get(value, {}).get("confirmation_status") != "confirmed"
            ]
            pending_material_refs = [
                value
                for value in required_material_ids
                if material_by_id.get(value, {}).get("verification_status") != "verified"
            ]
            prohibited = [
                deepcopy(prohibited_by_id[(fact_by_id[value]["source_type"], value)])
                for value in targeted_fact_ids
                if value in fact_by_id
                and (fact_by_id[value]["source_type"], value) in prohibited_by_id
            ] + [
                deepcopy(
                    prohibited_by_id[(material_by_id[value]["source_type"], value)]
                )
                for value in required_material_ids
                if value in material_by_id
                and (material_by_id[value]["source_type"], value) in prohibited_by_id
            ]
            target_locations = (
                [deepcopy(decision["location_override"])]
                if decision.get("location_override")
                else deepcopy(
                    review_item.get("recommended_revision_locations")
                    or review_item.get("current_response_locations")
                    or []
                )
            )
            instruction = (
                decision["revision_instruction"]
                if decision["decision"] == "modified"
                else accepted_machine_revision_instruction(review_item)
            )
            if decision["decision"] == "accepted" and not instruction:
                raise TechnicalFulltextHumanReviewFinalizationError(
                    "accepted machine recommendation has no executable revision instruction"
                )
            candidates.append(
                {
                    "source_type": decision["source_type"],
                    "source_id": decision["source_id"],
                    "requirement_ids": deepcopy(review_item.get("requirement_ids") or []),
                    "score_item_ids": score_item_ids,
                    "machine_issue_summary": review_item["machine_issue_summary"],
                    "machine_recommended_action": deepcopy(action),
                    "human_decision": decision["decision"],
                    "human_revision_instruction": decision.get("revision_instruction"),
                    "effective_revision_instruction": instruction,
                    "target_locations": target_locations,
                    "target_excerpts": deepcopy(
                        review_item.get("current_response_excerpts") or []
                    ),
                    "actionability_classes": deepcopy(
                        review_item.get("actionability_classes") or []
                    ),
                    "handoff_mode": handoff_mode,
                    "shared_dependency": deepcopy(review_item.get("shared_dependency")),
                    "required_fact_refs": required_fact_ids,
                    "required_material_refs": required_material_ids,
                    "required_confirmation_refs": required_confirmation_ids,
                    "approved_fact_refs": _unique(
                        item["source_id"] for item in candidate_approved_facts
                    ),
                    "approved_material_ref_ids": _unique(
                        item["material_or_evidence_ref"]
                        for item in candidate_approved_materials
                    ),
                    "approved_facts": candidate_approved_facts,
                    "approved_material_refs": candidate_approved_materials,
                    "prohibited_inferences": prohibited,
                    "acceptance_criteria": [instruction] if instruction else [],
                    "release_blocking": bool(review_item.get("release_blocking")),
                    "priority": self._priority(review_item),
                    "unresolved_dependencies": {
                        "pending_fact_refs": pending_fact_refs,
                        "pending_material_refs": pending_material_refs,
                        "pending_confirmation_refs": pending_confirmation_refs,
                    },
                }
            )
        return candidates

    @staticmethod
    def _requires_revision(action: dict[str, Any]) -> bool:
        kind = str(action.get("resolution_kind") or "")
        text = str(action.get("text") or "")
        return kind in REVISION_KINDS or any(term in text for term in REVISION_TERMS)

    @staticmethod
    def _priority(review_item: dict[str, Any]) -> str:
        if review_item.get("release_blocking"):
            return "critical"
        severity = review_item.get("machine_severity")
        return "high" if severity == "blocking" else "medium" if severity == "warning" else "normal"

    @staticmethod
    def _stage13_gate(
        *,
        human: dict[str, Any],
        candidates: list[dict[str, Any]],
        package_items: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any]:
        pending_issue_ids = [
            item["source_id"]
            for item in human.get("issue_decisions") or []
            if item["decision"] == "pending"
            and bool(
                (package_items.get((item["source_type"], item["source_id"])) or {}).get(
                    "must_decide_before_stage13"
                )
            )
        ]
        non_blocking_pending_issue_ids = [
            item["source_id"]
            for item in human.get("issue_decisions") or []
            if item["decision"] == "pending"
            and not bool(
                (package_items.get((item["source_type"], item["source_id"])) or {}).get(
                    "must_decide_before_stage13"
                )
            )
        ]
        blocked_candidates = [
            {
                "source_type": item["source_type"],
                "source_id": item["source_id"],
                "pending_fact_refs": deepcopy(
                    item["unresolved_dependencies"]["pending_fact_refs"]
                ),
                "pending_material_refs": deepcopy(
                    item["unresolved_dependencies"]["pending_material_refs"]
                ),
                "pending_confirmation_refs": deepcopy(
                    item["unresolved_dependencies"]["pending_confirmation_refs"]
                ),
            }
            for item in candidates
            if item["unresolved_dependencies"]["pending_fact_refs"]
            or item["unresolved_dependencies"]["pending_material_refs"]
            or item["unresolved_dependencies"]["pending_confirmation_refs"]
        ]
        reasons = []
        if pending_issue_ids:
            reasons.append("human_review_incomplete_pending_issue_decisions")
        if blocked_candidates:
            reasons.append("revision_candidates_have_targeted_unresolved_dependencies")
        if not pending_issue_ids and not candidates:
            reasons.append("no_stage13_revision_candidates")
        ready = not pending_issue_ids and bool(candidates) and not blocked_candidates
        return {
            "stage13_ready": ready,
            "stage13_requirement_status": (
                "undetermined"
                if pending_issue_ids
                else "required"
                if candidates
                else "not_required"
            ),
            "reason_codes": reasons,
            "human_readable_reason": (
                "尚未完成人工审核。"
                if pending_issue_ids
                else "存在与具体修改任务直接关联的未确认事实或材料。"
                if blocked_candidates
                else "不存在需要交给 Stage13 的正文修改任务。"
                if not candidates
                else "Stage12 人工审核 gate 已通过。"
            ),
            "pending_issue_source_ids": pending_issue_ids,
            "non_blocking_pending_issue_source_ids": non_blocking_pending_issue_ids,
            "blocked_revision_candidates": blocked_candidates,
            "revision_candidate_count": len(candidates),
        }

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        self._validate_schema(payload, self.schema_path, "Stage12-C finalization")

    def _validate_handoff(self, payload: dict[str, Any]) -> None:
        self._validate_schema(payload, self.handoff_schema_path, "Stage13 handoff")

    @staticmethod
    def _validate_schema(payload: dict[str, Any], path: Path, label: str) -> None:
        schema = load_json_object(path, label=f"{label} schema")
        errors = sorted(
            Draft202012Validator(schema).iter_errors(payload),
            key=lambda item: list(item.path),
        )
        if errors:
            detail = "; ".join(
                f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
                for error in errors[:8]
            )
            raise TechnicalFulltextHumanReviewFinalizationError(
                f"{label} schema validation failed: {detail}"
            )

    @staticmethod
    def _verify_inputs_unchanged(
        *, package_path: Path, package_sha: str, human_path: Path, human_sha: str
    ) -> None:
        if sha256_file(package_path) != package_sha:
            raise TechnicalFulltextHumanReviewFinalizationError(
                "Stage12-A package changed during finalization"
            )
        if sha256_file(human_path) != human_sha:
            raise TechnicalFulltextHumanReviewFinalizationError(
                "Stage12-B human review changed during finalization"
            )

    @staticmethod
    def _validate_stage11_sources(package: dict[str, Any]) -> None:
        lineage = package.get("source_lineage") or {}
        for name in (
            "final_stage11_audit",
            "stage11_scoring_assessment",
            "audited_response_content",
        ):
            reference = lineage.get(name) or {}
            path = Path(str(reference.get("path") or "")).expanduser().resolve()
            expected_sha = reference.get("file_sha256")
            try:
                actual_sha = sha256_file(path)
            except (FileNotFoundError, OSError) as exc:
                raise TechnicalFulltextHumanReviewFinalizationError(
                    f"Stage12-C upstream is unavailable: {name}: {exc}"
                ) from exc
            if actual_sha != expected_sha:
                raise TechnicalFulltextHumanReviewFinalizationError(
                    f"Stage12-C upstream is stale: {name} SHA256 mismatch"
                )

    @staticmethod
    def _render_markdown(payload: dict[str, Any]) -> str:
        review = payload["review_status_summary"]
        score = payload["human_adjusted_bid_assessment"]["score_summary"]
        gate = payload["stage13_gate"]
        lines = [
            "# 全文人工审核汇总与 Stage13 交接评估",
            "",
            f"> {MARKDOWN_NOTICE}",
            f"> Stage13 ready: `{gate['stage13_ready']}` — {gate['human_readable_reason']}",
            "",
            "## Review Finalization",
            "",
            f"- Issue decisions：total {review['total_issue_decisions']} / pending {review['pending_issue_decisions']} / accepted {review['accepted_issue_decisions']} / rejected {review['rejected_issue_decisions']} / modified {review['modified_issue_decisions']}",
            f"- Facts：total {review['total_fact_confirmations']} / pending {review['pending_facts']} / confirmed {review['confirmed_facts']} / rejected {review['rejected_facts']}",
            f"- Materials：total {review['total_material_verifications']} / pending {review['pending_materials']} / verified {review['verified_materials']} / invalid {review['invalid_materials']} / expired {review['expired_materials']} / not_provided {review['not_provided_materials']}",
            f"- Accepted risks：{review['accepted_risk_count']}",
            "",
            "## Human-adjusted Bid Assessment",
            "",
            f"- Technical max：{score['technical_score_max']}",
            f"- Machine confirmed：{score['machine_confirmed_score']}",
            f"- Machine estimated：{score['machine_estimated_range']['min']}-{score['machine_estimated_range']['max']}",
            f"- Machine pending exposure：{score['machine_pending_exposure']}",
            f"- Human-adjusted confirmed：{score['human_adjusted_confirmed_score']}",
            f"- Human-adjusted estimated：{score['human_adjusted_estimated_range']['min']}-{score['human_adjusted_estimated_range']['max']}",
            f"- Unresolved score exposure：{score['unresolved_score_exposure']}",
            "",
            "## Stage13 Revision Candidates",
            "",
        ]
        if not payload["revision_candidates"]:
            lines.append("- 当前没有可交接的 revision candidate。")
        for item in payload["revision_candidates"]:
            lines.extend(
                [
                    f"### {item['source_type']}:{item['source_id']}",
                    "",
                    f"- Decision：{item['human_decision']}",
                    f"- Instruction：{item['effective_revision_instruction']}",
                    f"- Priority：{item['priority']} / release_blocking={item['release_blocking']}",
                    f"- Pending facts：{', '.join(item['unresolved_dependencies']['pending_fact_refs']) or '-'}",
                    f"- Pending materials：{', '.join(item['unresolved_dependencies']['pending_material_refs']) or '-'}",
                    "",
                ]
            )
        lines.extend(
            [
                "## Gate",
                "",
                f"- stage13_ready：{gate['stage13_ready']}",
                f"- reason：{gate['human_readable_reason']}",
                f"- reason_codes：{', '.join(gate['reason_codes']) or '-'}",
                "",
                "## Pre-Stage13 Dependency",
                "",
                "- 即使 human review gate 最终通过，也不能正式开始 Stage13 revision loop，直到 requirement_location provider missing/duplicate unit ID 的完整 Stage11.2 iterative replay dependency 被修复并完成回归验证。",
                "",
            ]
        )
        return "\n".join(lines)


def inspect_technical_fulltext_review_finalization(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    payload = load_json_object(target, label="Stage12-C finalization")
    finalizer = TechnicalFulltextHumanReviewFinalizer()
    finalizer._validate_payload(payload)
    package_ref = payload["source_lineage"]["stage12a_review_package"]
    human_ref = payload["source_lineage"]["stage12b_human_review"]
    if sha256_file(package_ref["path"]) != package_ref["sha256"]:
        raise TechnicalFulltextHumanReviewFinalizationError(
            "Stage12-C finalization is stale: Stage12-A package changed"
        )
    if sha256_file(human_ref["path"]) != human_ref["sha256"]:
        raise TechnicalFulltextHumanReviewFinalizationError(
            "Stage12-C finalization is stale: Stage12-B human review changed"
        )
    package = load_json_object(
        package_ref["path"], label="Stage12-A review package"
    )
    finalizer._validate_stage11_sources(package)
    return {
        "schema_version": payload["schema_version"],
        "artifact_identity": deepcopy(payload["artifact_identity"]),
        "source_lineage": deepcopy(payload["source_lineage"]),
        "review_status_summary": deepcopy(payload["review_status_summary"]),
        "score_summary": deepcopy(
            payload["human_adjusted_bid_assessment"]["score_summary"]
        ),
        "stage13_gate": deepcopy(payload["stage13_gate"]),
        "finalization_sha256": sha256_file(target),
        "stale": False,
    }
