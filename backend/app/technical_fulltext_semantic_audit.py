from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from app.llm_client import LLMClient, ModelCallResult, get_llm_client
from app.technical_fulltext_auditor import (
    AUDIT_MARKDOWN_NOTICE,
    TechnicalFulltextAuditError,
    TechnicalFulltextAuditor,
)
from app.technical_fulltext_composer import (
    atomic_write_json,
    atomic_write_markdown,
    load_json_object,
    sha256_file,
    stable_json_sha256,
    utc_now,
)
from app.technical_fulltext_stable_fact_consistency import (
    PASS_TYPE as STABLE_FACT_PASS_TYPE,
    extract_stable_fact_candidates,
)


SEMANTIC_RESULT_SCHEMA_VERSION = "technical_fulltext_semantic_audit_result_v1"
DEFAULT_MODEL_PROFILE = "deepseek_draft"
REQUIREMENT_OBSERVATION_CONTRACT_VERSION = "requirement_semantic_observation_v4"
REQUIREMENT_PROMPT_VERSION = "requirement_semantic_observation_prompt_v4"
REQUIREMENT_DECISION_POLICY_VERSION = "requirement_semantic_decision_policy_v1"
REQUIREMENT_SEMANTIC_UNIT_TYPE = "requirement_content_ownership"
NON_REQUIREMENT_OBSERVATION_CONTRACT_VERSION = "non_requirement_semantic_observation_v1"
NON_REQUIREMENT_PROMPT_VERSION = "non_requirement_semantic_observation_prompt_v1"
NON_REQUIREMENT_DECISION_POLICY_VERSION = "non_requirement_semantic_decision_policy_v1"
NON_REQUIREMENT_SEMANTIC_UNIT_TYPE = "non_requirement_semantic_observation"


class TechnicalFulltextSemanticAuditError(RuntimeError):
    """Raised when canonical Stage11.2 cannot produce one validated semantic pass."""


class RequirementObservationConsistencyError(TechnicalFulltextSemanticAuditError):
    """Raised when a requirement observation is structurally valid but contradictory."""


@dataclass(frozen=True)
class SemanticAuditUnit:
    audit_unit_id: str
    pass_type: str
    source_candidate_ids: list[str]
    payload: dict[str, Any]
    locations: list[dict[str, Any]]
    requirement_ids: list[str]
    score_item_ids: list[str]
    placeholder_ids: list[str]

    def model_payload(self) -> dict[str, Any]:
        return {
            "audit_unit_id": self.audit_unit_id,
            "source_candidate_ids": self.source_candidate_ids,
            "content": self.payload,
            "allowed_locations": self.locations,
            "allowed_requirement_ids": self.requirement_ids,
            "allowed_score_item_ids": self.score_item_ids,
            "allowed_placeholder_ids": self.placeholder_ids,
        }

    def semantic_fingerprint(self) -> str:
        if self.pass_type != "requirement_location":
            raise ValueError("semantic observation fingerprint is only defined for requirement units")
        return stable_json_sha256({
            "pass_type": self.pass_type,
            "semantic_unit_type": REQUIREMENT_SEMANTIC_UNIT_TYPE,
            "normalized_candidate_payload": self.model_payload(),
            "observation_contract_version": REQUIREMENT_OBSERVATION_CONTRACT_VERSION,
            "prompt_version": REQUIREMENT_PROMPT_VERSION,
            "result_schema_version": SEMANTIC_RESULT_SCHEMA_VERSION,
            "decision_policy_version": REQUIREMENT_DECISION_POLICY_VERSION,
        })


PASS_ORDER = (
    "placeholder_alias",
    "requirement_location",
    "numeric_consistency",
    STABLE_FACT_PASS_TYPE,
    "terminology_consistency",
    "near_duplicate",
    "score_distribution",
    "adjacent_chapter_coherence",
)

PASS_BATCH_SIZE = {
    "placeholder_alias": 6,
    # Requirement observations are replay-critical and carry a stable unit
    # identity into the runtime decision policy.  Keep provider calls atomic:
    # a generative provider cannot omit one unit or reuse another unit's ID
    # when exactly one identity is supplied.  This does not relax validation;
    # an omitted, changed, or duplicated identity is still rejected.
    "requirement_location": 1,
    "numeric_consistency": 6,
    STABLE_FACT_PASS_TYPE: 6,
    "terminology_consistency": 6,
    "near_duplicate": 4,
    "score_distribution": 4,
    # Keep boundary units in small batches so the model cannot accidentally
    # reuse a location anchor from another chapter pair in a large batch.
    "adjacent_chapter_coherence": 4,
}

DECISION_VERDICTS = {
    "placeholder_alias": {
        "same_fact": "issue",
        "different_fact": "acceptable",
        "uncertain": "uncertain",
    },
    "requirement_location": {
        "acceptable_reference": "acceptable",
        "misplaced_content": "issue",
        "redundant_response": "issue",
        "coverage_content_mismatch": "issue",
        "uncertain": "uncertain",
    },
    "numeric_consistency": {
        "acceptable": "acceptable",
        "different_attribute": "acceptable",
        "commitment_conflict": "issue",
        "value_conflict": "issue",
        "uncertain": "uncertain",
    },
    STABLE_FACT_PASS_TYPE: {
        "consistent": "acceptable",
        "context_different": "acceptable",
        "stable_fact_conflict": "issue",
        "uncertain": "uncertain",
    },
    "terminology_consistency": {
        "same_entity_variant": "issue",
        "different_entity": "acceptable",
        "acceptable_context": "acceptable",
        "uncertain": "uncertain",
    },
    "near_duplicate": {
        "meaningful_duplicate": "issue",
        "necessary_restatement": "acceptable",
        "different_content": "acceptable",
        "uncertain": "uncertain",
    },
    "score_distribution": {
        "appropriate_distribution": "acceptable",
        "redundant_score_response": "issue",
        "misplaced_score_response": "issue",
        "uncertain": "uncertain",
    },
    "adjacent_chapter_coherence": {
        "coherent": "acceptable",
        "repeated_reintroduction": "issue",
        "context_reset": "issue",
        "weak_transition": "issue",
        "logical_jump": "issue",
        "uncertain": "uncertain",
    },
}

DECISION_CODES = {
    "same_fact": ("confirmation_governance", "confirmation_alias_fragmentation"),
    "misplaced_content": ("content_ownership", "misplaced_requirement_content"),
    "redundant_response": ("content_ownership", "redundant_requirement_response"),
    "coverage_content_mismatch": ("content_ownership", "coverage_content_mismatch"),
    "commitment_conflict": ("global_consistency", "commitment_conflict"),
    "value_conflict": ("global_consistency", "numeric_value_conflict"),
    "stable_fact_conflict": ("global_consistency", "stable_fact_conflict"),
    "same_entity_variant": ("global_consistency", "term_alias_inconsistency"),
    "meaningful_duplicate": ("cross_content_duplication", "semantic_cross_chapter_duplicate"),
    "redundant_score_response": ("score_distribution", "redundant_score_response"),
    "misplaced_score_response": ("score_distribution", "misplaced_score_response"),
    "repeated_reintroduction": ("chapter_coherence", "repeated_reintroduction"),
    "context_reset": ("chapter_coherence", "context_reset"),
    "weak_transition": ("chapter_coherence", "weak_transition"),
    "logical_jump": ("chapter_coherence", "logical_jump"),
}


class RequirementSemanticDecisionPolicy:
    """Convert validated model observations into one replayable business decision."""

    version = REQUIREMENT_DECISION_POLICY_VERSION

    @classmethod
    def decide(
        cls,
        observation: dict[str, Any],
        *,
        planned_primary_assessment_ids: set[str] | None = None,
    ) -> tuple[str, str, str]:
        assessments = observation["actual_location_assessments"]
        if observation["evidence_sufficiency"] == "insufficient":
            return "uncertain", "uncertain", "evidence_insufficient"
        if observation["primary_response_status"] == "uncertain":
            return "uncertain", "uncertain", "primary_status_uncertain"
        if any(
            item["semantic_role"] == "uncertain"
            or item["substantive_level"] == "uncertain"
            or item["independent_complete_response"] == "uncertain"
            for item in assessments
        ):
            return "uncertain", "uncertain", "critical_location_observation_uncertain"

        primary_ids = planned_primary_assessment_ids or {
            item["assessment_id"]
            for item in assessments
            if item["semantic_role"] == "primary_response"
        }
        non_primary = [
            item for item in assessments if item["assessment_id"] not in primary_ids
        ]
        independent = [
            item
            for item in non_primary
            if item["semantic_role"] == "independent_response"
            and item["independent_complete_response"] == "yes"
        ]
        primary_status = observation["primary_response_status"]
        if primary_status == "adequate":
            if independent:
                return "redundant_response", "issue", "adequate_primary_with_independent_duplicate"
            return "acceptable_reference", "acceptable", "adequate_primary_without_independent_duplicate"
        if primary_status in {"missing", "inadequate"}:
            if independent:
                return "misplaced_content", "issue", "deficient_primary_with_complete_response_elsewhere"
            return "coverage_content_mismatch", "issue", "deficient_primary_without_complete_response"
        raise TechnicalFulltextSemanticAuditError(
            f"unsupported primary response status: {primary_status!r}"
        )


def _unique(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value or "").strip() for value in values if str(value or "").strip()))


def _stable_id(prefix: str, value: Any, length: int = 16) -> str:
    return prefix + stable_json_sha256(value)[:length].upper()


def _location_id(location: dict[str, Any]) -> str:
    return _stable_id("LOC-", {
        "chapter_id": location.get("chapter_id"),
        "section_id": location.get("section_id"),
        "paragraph_id": location.get("paragraph_id"),
        "text_sha256": location.get("text_sha256"),
    })


def _chunks(values: list[SemanticAuditUnit], size: int) -> Iterable[list[SemanticAuditUnit]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


class TechnicalFulltextSemanticAuditor:
    """Run Stage11.2 over Stage11.1; Stage11-C is calibration history, not a step."""

    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        model_profile: str = DEFAULT_MODEL_PROFILE,
        result_schema_path: str | Path | None = None,
        runtime_validation_retries: int = 1,
    ):
        self.llm_client = llm_client
        self.model_profile = model_profile
        self.result_schema_path = (
            Path(result_schema_path).expanduser().resolve()
            if result_schema_path is not None
            else Path(__file__).resolve().parent.parent
            / "schemas"
            / "technical_fulltext_semantic_audit_result_v1.json"
        )
        self.runtime_validation_retries = max(0, min(int(runtime_validation_retries), 1))

    def audit(self, *, audit_json_path: str | Path) -> dict[str, Any]:
        target = Path(audit_json_path).expanduser().resolve()
        try:
            audit = load_json_object(target, label="technical fulltext audit")
            original_audit_sha = sha256_file(target)
            TechnicalFulltextAuditor()._validate_audit_payload(audit)
        except (FileNotFoundError, ValueError, OSError, TechnicalFulltextAuditError) as exc:
            raise TechnicalFulltextSemanticAuditError(
                f"Stage11.1 audit artifact is unavailable or invalid: {exc}"
            ) from exc
        self._validate_stage_b_state(audit)
        self._revalidate_source_snapshot(audit)

        # Candidate generation belongs to Stage11.2 and reads only the already
        # normalized, SHA-bound audited fulltext. This also upgrades historical
        # Stage11.1 artifacts without rerunning Stage10 or reparsing the tender.
        audit = deepcopy(audit)
        audit.setdefault("semantic_candidates", {})[
            "stable_fact_consistency"
        ] = extract_stable_fact_candidates(audit["normalized_document"])

        context, trust = self._load_trusted_context(audit)
        units_by_pass, pass_notes = self._build_units(audit, context=context, trust=trust)
        self._validate_unit_identities(units_by_pass)
        result_schema = self._load_result_schema()
        requirement_cache = self._validated_requirement_cache(
            audit, units_by_pass["requirement_location"]
        )
        client = self.llm_client or get_llm_client(
            role="drafting", profile_name=self.model_profile
        )
        model_configuration = self._model_configuration(client)
        audited_content_sha256 = self._audited_content_sha(audit)
        source_context_sha256 = self._non_requirement_source_context_sha(audit)
        non_requirement_cache = self._validated_non_requirement_cache(
            audit,
            units_by_pass,
            audited_content_sha256=audited_content_sha256,
            source_context_sha256=source_context_sha256,
            model_configuration=model_configuration,
        )
        refreshed_cache: dict[str, dict[str, Any]] = {}

        findings: list[dict[str, Any]] = []
        calls: list[ModelCallResult] = []
        batch_count = 0
        per_pass: dict[str, dict[str, Any]] = {}
        total_reused_observations = 0
        requirement_reused_observations = 0
        non_requirement_reused_observations = 0
        total_model_observations = 0
        for pass_type in PASS_ORDER:
            units = units_by_pass[pass_type]
            decisions: Counter[str] = Counter()
            verdicts: Counter[str] = Counter()
            logical_batches = 0
            reused_observations = 0
            for batch_index, batch_units in enumerate(
                _chunks(units, PASS_BATCH_SIZE[pass_type]), start=1
            ):
                logical_batches += 1
                batch_id = _stable_id("FSB-", {
                    "document_id": audit["document_id"],
                    "pass_type": pass_type,
                    "batch_index": batch_index,
                    "unit_ids": [unit.audit_unit_id for unit in batch_units],
                })
                results: list[dict[str, Any]] = []
                model_units: list[SemanticAuditUnit] = []
                reusable_non_requirement: dict[str, dict[str, Any]] = {}
                if pass_type == "requirement_location":
                    for unit in batch_units:
                        fingerprint = unit.semantic_fingerprint()
                        cached = requirement_cache.get(fingerprint)
                        if cached is None:
                            model_units.append(unit)
                            continue
                        result = self._formalize_requirement_observation(
                            unit=unit,
                            observation=cached["observation"],
                            fingerprint=fingerprint,
                            reused=True,
                            reuse_source=cached,
                        )
                        results.append(result)
                        refreshed_cache[fingerprint] = deepcopy(cached)
                        reused_observations += 1
                        total_reused_observations += 1
                        requirement_reused_observations += 1
                else:
                    for unit in batch_units:
                        deterministic = (
                            self._deterministic_stable_fact_result(unit)
                            if pass_type == STABLE_FACT_PASS_TYPE
                            else None
                        )
                        if deterministic is not None:
                            results.append(deterministic)
                            continue
                        fingerprint = self._non_requirement_fingerprint(
                            unit=unit,
                            audited_content_sha256=audited_content_sha256,
                            source_context_sha256=source_context_sha256,
                            model_configuration_sha256=model_configuration[
                                "identity_sha256"
                            ],
                        )
                        cached = non_requirement_cache.get(fingerprint)
                        if cached is None:
                            model_units.append(unit)
                            continue
                        results.append(
                            self._formalize_non_requirement_observation(
                                unit=unit, observation=cached["observation"]
                            )
                        )
                        reusable_non_requirement[unit.audit_unit_id] = cached
                        reused_observations += 1
                        total_reused_observations += 1
                        non_requirement_reused_observations += 1
                batch_calls: list[ModelCallResult] = []
                if model_units:
                    batch_count += 1
                    model_batch_id = _stable_id("FSB-", {
                        "document_id": audit["document_id"],
                        "pass_type": pass_type,
                        "batch_index": batch_index,
                        "unit_ids": [unit.audit_unit_id for unit in model_units],
                    })
                    model_results, batch_calls = self._call_batch(
                        client=client,
                        audit=audit,
                        batch_id=model_batch_id,
                        pass_type=pass_type,
                        units=model_units,
                        response_schema=result_schema,
                    )
                    results.extend(model_results)
                    total_model_observations += len(model_results)
                calls.extend(batch_calls)
                unit_map = {unit.audit_unit_id: unit for unit in batch_units}
                model_unit_ids = {unit.audit_unit_id for unit in model_units}
                for result in results:
                    unit = unit_map[result["audit_unit_id"]]
                    finding = self._to_finding(
                        pass_type=pass_type,
                        unit=unit,
                        result=result,
                    )
                    findings.append(finding)
                    if pass_type == "requirement_location":
                        fingerprint = unit.semantic_fingerprint()
                        cache_entry = {
                            "semantic_fingerprint": fingerprint,
                            "pass_type": pass_type,
                            "semantic_unit_type": REQUIREMENT_SEMANTIC_UNIT_TYPE,
                            "observation_contract_version": REQUIREMENT_OBSERVATION_CONTRACT_VERSION,
                            "prompt_version": REQUIREMENT_PROMPT_VERSION,
                            "result_schema_version": SEMANTIC_RESULT_SCHEMA_VERSION,
                            "decision_policy_version": REQUIREMENT_DECISION_POLICY_VERSION,
                            "observation": deepcopy(result["semantic_observation"]),
                            "source_finding_id": finding["finding_id"],
                            "validation_status": result["requirement_decision_provenance"]["observation_validation_status"],
                        }
                        if cache_entry["validation_status"] == "validated":
                            refreshed_cache[fingerprint] = cache_entry
                    elif (
                        result["audit_unit_id"] in model_unit_ids
                        or result["audit_unit_id"] in reusable_non_requirement
                    ):
                        fingerprint = self._non_requirement_fingerprint(
                            unit=unit,
                            audited_content_sha256=audited_content_sha256,
                            source_context_sha256=source_context_sha256,
                            model_configuration_sha256=model_configuration[
                                "identity_sha256"
                            ],
                        )
                        prior = reusable_non_requirement.get(result["audit_unit_id"]) or {}
                        call = batch_calls[-1] if batch_calls else None
                        observation = self._non_requirement_observation(result)
                        cache_entry = {
                            "semantic_fingerprint": fingerprint,
                            "pass_type": pass_type,
                            "semantic_unit_type": NON_REQUIREMENT_SEMANTIC_UNIT_TYPE,
                            "audit_unit_id": unit.audit_unit_id,
                            "normalized_observation_payload_sha256": stable_json_sha256(
                                unit.model_payload()
                            ),
                            "audited_content_sha256": audited_content_sha256,
                            "source_context_sha256": source_context_sha256,
                            "observation_contract_version": NON_REQUIREMENT_OBSERVATION_CONTRACT_VERSION,
                            "prompt_version": NON_REQUIREMENT_PROMPT_VERSION,
                            "result_schema_version": SEMANTIC_RESULT_SCHEMA_VERSION,
                            "decision_policy_version": NON_REQUIREMENT_DECISION_POLICY_VERSION,
                            "model_configuration_sha256": model_configuration[
                                "identity_sha256"
                            ],
                            "model_profile": (
                                call.profile_name if call else prior.get("model_profile")
                            )
                            or model_configuration["profile_name"],
                            "model_provider": (
                                call.provider if call else prior.get("model_provider")
                            )
                            or model_configuration["provider"],
                            "model_name": (
                                call.model if call else prior.get("model_name")
                            )
                            or model_configuration["model"],
                            "observation": observation,
                            "observation_sha256": stable_json_sha256(observation),
                            "source_finding_id": finding["finding_id"],
                            "validation_status": "validated",
                        }
                        refreshed_cache[fingerprint] = cache_entry
                    decisions[finding["decision"]] += 1
                    verdicts[finding["verdict"]] += 1
            note = pass_notes.get(pass_type) or {}
            per_pass[pass_type] = {
                "status": note.get("status", "completed"),
                "reason": note.get("reason"),
                "audit_unit_count": len(units),
                "batch_count": logical_batches,
                "finding_count": sum(verdicts.values()),
                "verdict_counts": {
                    "issue": verdicts["issue"],
                    "acceptable": verdicts["acceptable"],
                    "uncertain": verdicts["uncertain"],
                },
                "decision_counts": dict(sorted(decisions.items())),
                "source_candidate_count": note.get("source_candidate_count", 0),
                "skipped_candidate_count": note.get("skipped_candidate_count", 0),
                "reused_observation_count": reused_observations,
            }

        # No artifact write has occurred above. Recheck every trusted boundary before commit.
        if sha256_file(target) != original_audit_sha:
            raise TechnicalFulltextSemanticAuditError(
                "Stage11.1 audit artifact changed during semantic execution"
            )
        self._revalidate_source_snapshot(audit)

        verdict_counts = Counter(item["verdict"] for item in findings)
        usage = self._aggregate_usage(calls)
        providers = _unique(call.provider for call in calls)
        models = _unique(call.model for call in calls)
        profiles = _unique(call.profile_name for call in calls)
        updated = deepcopy(audit)
        # A successful semantic rerun invalidates every downstream projection.
        self._invalidate_downstream(updated)
        updated["audit_result"] = "partial"
        updated["release_ready"] = False
        updated["semantic_findings"] = findings
        updated["semantic_observation_cache"] = [
            refreshed_cache[key] for key in sorted(refreshed_cache)
        ]
        updated["semantic_audit"] = {
            "status": "completed",
            "provider": providers[0] if len(providers) == 1 else ",".join(providers),
            "model": models[0] if len(models) == 1 else ",".join(models),
            "profile_name": profiles[0] if len(profiles) == 1 else self.model_profile,
            "batch_count": batch_count,
            "model_call_count": len(calls),
            "audit_unit_count": sum(len(values) for values in units_by_pass.values()),
            "finding_count": len(findings),
            "verdict_counts": {
                "issue": verdict_counts["issue"],
                "acceptable": verdict_counts["acceptable"],
                "uncertain": verdict_counts["uncertain"],
            },
            "token_usage": usage,
            "reuse": {
                # Keep the historical Requirement metric stable and expose the
                # new non-Requirement/total counts separately.
                "validated_observation_reuse_count": requirement_reused_observations,
                "non_requirement_validated_observation_reuse_count": non_requirement_reused_observations,
                "total_validated_observation_reuse_count": total_reused_observations,
                "model_observation_count": total_model_observations,
                "cache_entry_count": len(refreshed_cache),
            },
            "passes": per_pass,
            "completed_at": utc_now(),
        }
        updated["generated_at"] = utc_now()
        TechnicalFulltextAuditor()._validate_audit_payload(updated)
        markdown = TechnicalFulltextAuditor._render_markdown(updated)
        markdown_path = target.with_suffix(".md")
        self._persist_pair(
            audit_path=target,
            audit_payload=updated,
            markdown_path=markdown_path,
            markdown=markdown,
        )
        return {
            "status": "completed",
            "audit_result": "partial",
            "release_ready": False,
            "semantic_audit": deepcopy(updated["semantic_audit"]),
            "semantic_findings": deepcopy(findings),
            "outputs": {
                "technical_fulltext_audit_json": str(target),
                "technical_fulltext_audit_json_sha256": sha256_file(target),
                "technical_fulltext_audit_markdown": str(markdown_path),
                "technical_fulltext_audit_markdown_sha256": sha256_file(markdown_path),
            },
        }

    def audit_stable_fact_consistency(
        self, *, audit_json_path: str | Path
    ) -> dict[str, Any]:
        """Replay only the bounded Stage11.2 stable-fact pass on validated findings."""
        target = Path(audit_json_path).expanduser().resolve()
        try:
            audit = load_json_object(target, label="technical fulltext audit")
            original_audit_sha = sha256_file(target)
            TechnicalFulltextAuditor()._validate_audit_payload(audit)
        except (FileNotFoundError, ValueError, OSError, TechnicalFulltextAuditError) as exc:
            raise TechnicalFulltextSemanticAuditError(
                f"Stage11.2 audit artifact is unavailable or invalid: {exc}"
            ) from exc
        self._validate_stage_b_state(audit)
        if (audit.get("audit_passes") or {}).get("semantic", {}).get("status") != "completed":
            raise TechnicalFulltextSemanticAuditError(
                "bounded stable-fact replay requires an existing completed Stage11.2 audit"
            )
        semantic = deepcopy(audit.get("semantic_audit") or {})
        if semantic.get("status") != "completed" or not isinstance(
            audit.get("semantic_findings"), list
        ):
            raise TechnicalFulltextSemanticAuditError(
                "bounded stable-fact replay requires validated existing semantic findings"
            )
        self._revalidate_source_snapshot(audit)

        working = deepcopy(audit)
        candidates = extract_stable_fact_candidates(working["normalized_document"])
        working.setdefault("semantic_candidates", {})[
            "stable_fact_consistency"
        ] = candidates
        context, trust = self._load_trusted_context(working)
        units_by_pass, pass_notes = self._build_units(
            working, context=context, trust=trust
        )
        self._validate_unit_identities(units_by_pass)
        units = units_by_pass[STABLE_FACT_PASS_TYPE]
        result_schema = self._load_result_schema()
        findings: list[dict[str, Any]] = []
        calls: list[ModelCallResult] = []
        decisions: Counter[str] = Counter()
        verdicts: Counter[str] = Counter()
        logical_batches = 0
        client: LLMClient | None = self.llm_client
        for batch_index, batch_units in enumerate(
            _chunks(units, PASS_BATCH_SIZE[STABLE_FACT_PASS_TYPE]), start=1
        ):
            logical_batches += 1
            results: list[dict[str, Any]] = []
            model_units: list[SemanticAuditUnit] = []
            for unit in batch_units:
                deterministic = self._deterministic_stable_fact_result(unit)
                if deterministic is None:
                    model_units.append(unit)
                else:
                    results.append(deterministic)
            if model_units:
                if client is None:
                    client = get_llm_client(
                        role="drafting", profile_name=self.model_profile
                    )
                batch_id = _stable_id("FSB-", {
                    "document_id": working["document_id"],
                    "pass_type": STABLE_FACT_PASS_TYPE,
                    "batch_index": batch_index,
                    "unit_ids": [unit.audit_unit_id for unit in model_units],
                })
                model_results, batch_calls = self._call_batch(
                    client=client,
                    audit=working,
                    batch_id=batch_id,
                    pass_type=STABLE_FACT_PASS_TYPE,
                    units=model_units,
                    response_schema=result_schema,
                )
                results.extend(model_results)
                calls.extend(batch_calls)
            unit_map = {unit.audit_unit_id: unit for unit in batch_units}
            for result in results:
                finding = self._to_finding(
                    pass_type=STABLE_FACT_PASS_TYPE,
                    unit=unit_map[result["audit_unit_id"]],
                    result=result,
                )
                findings.append(finding)
                decisions[finding["decision"]] += 1
                verdicts[finding["verdict"]] += 1

        note = pass_notes.get(STABLE_FACT_PASS_TYPE) or {}
        pass_summary = {
            "status": note.get("status", "completed"),
            "reason": note.get("reason"),
            "audit_unit_count": len(units),
            "batch_count": logical_batches,
            "finding_count": len(findings),
            "verdict_counts": {
                "issue": verdicts["issue"],
                "acceptable": verdicts["acceptable"],
                "uncertain": verdicts["uncertain"],
            },
            "decision_counts": dict(sorted(decisions.items())),
            "source_candidate_count": note.get("source_candidate_count", 0),
            "skipped_candidate_count": note.get("skipped_candidate_count", 0),
            "reused_observation_count": 0,
        }

        if sha256_file(target) != original_audit_sha:
            raise TechnicalFulltextSemanticAuditError(
                "Stage11 audit artifact changed during bounded stable-fact replay"
            )
        self._revalidate_source_snapshot(working)

        updated = deepcopy(working)
        prior_findings = [
            item
            for item in updated.get("semantic_findings") or []
            if item.get("pass_type") != STABLE_FACT_PASS_TYPE
        ]
        all_findings = prior_findings + findings
        all_verdicts = Counter(item["verdict"] for item in all_findings)
        semantic.setdefault("passes", {})[STABLE_FACT_PASS_TYPE] = pass_summary
        semantic["audit_unit_count"] = sum(
            int(item.get("audit_unit_count") or 0)
            for item in semantic["passes"].values()
        )
        semantic["finding_count"] = len(all_findings)
        semantic["verdict_counts"] = {
            "issue": all_verdicts["issue"],
            "acceptable": all_verdicts["acceptable"],
            "uncertain": all_verdicts["uncertain"],
        }
        semantic["batch_count"] = int(semantic.get("batch_count") or 0) + len(calls)
        semantic["model_call_count"] = int(semantic.get("model_call_count") or 0) + len(calls)
        usage = Counter(semantic.get("token_usage") or {})
        usage.update(self._aggregate_usage(calls))
        semantic["token_usage"] = dict(sorted(usage.items()))
        if calls:
            semantic["provider"] = ",".join(
                _unique([semantic.get("provider"), *(call.provider for call in calls)])
            )
            semantic["model"] = ",".join(
                _unique([semantic.get("model"), *(call.model for call in calls)])
            )
        semantic["completed_at"] = utc_now()

        self._invalidate_downstream(updated)
        updated["audit_result"] = "partial"
        updated["release_ready"] = False
        updated["semantic_findings"] = all_findings
        updated["semantic_audit"] = semantic
        updated["generated_at"] = utc_now()
        TechnicalFulltextAuditor()._validate_audit_payload(updated)
        markdown = TechnicalFulltextAuditor._render_markdown(updated)
        markdown_path = target.with_suffix(".md")
        self._persist_pair(
            audit_path=target,
            audit_payload=updated,
            markdown_path=markdown_path,
            markdown=markdown,
        )
        return {
            "status": "completed",
            "audit_result": "partial",
            "release_ready": False,
            "stable_fact_candidates": len(candidates),
            "stable_fact_findings": deepcopy(findings),
            "pass_summary": deepcopy(pass_summary),
            "outputs": {
                "technical_fulltext_audit_json": str(target),
                "technical_fulltext_audit_json_sha256": sha256_file(target),
                "technical_fulltext_audit_markdown": str(markdown_path),
                "technical_fulltext_audit_markdown_sha256": sha256_file(markdown_path),
            },
        }

    @staticmethod
    def _invalidate_downstream(updated: dict[str, Any]) -> None:
        """Reset Stage11.3/11.4 while preserving the Stage11.1 source issues."""
        if (updated.get("audit_passes") or {}).get("finalization", {}).get("status") == "completed":
            updated["issues"] = deepcopy(updated.get("deterministic_source_issues") or [])
            reset_counts = {"fatal": 0, "blocking": 0, "warning": 0, "info": 0}
            for issue in updated["issues"]:
                severity = str(issue.get("severity") or "")
                if severity in reset_counts:
                    reset_counts[severity] += 1
            updated["issue_counts"] = reset_counts
            updated["audit_passes"].pop("finalization", None)
            for field in (
                "deterministic_source_issues",
                "semantic_finding_dispositions",
                "confirmation_relationships",
                "finalization",
            ):
                updated.pop(field, None)
        updated["audit_passes"]["semantic"] = {"status": "completed"}
        updated["audit_passes"]["scoring"] = {"status": "not_run"}
        updated.pop("scoring_assessment_ref", None)

    @staticmethod
    def _validate_stage_b_state(audit: dict[str, Any]) -> None:
        if (audit.get("audit_passes") or {}).get("deterministic", {}).get("status") != "completed":
            raise TechnicalFulltextSemanticAuditError(
                "Stage11.1 deterministic audit must be completed before Stage11.2"
            )
        if audit.get("release_ready") is not False:
            raise TechnicalFulltextSemanticAuditError("Stage11.2 input must remain release_ready=false")
        semantic_status = (audit.get("audit_passes") or {}).get("semantic", {}).get("status")
        if semantic_status not in {"not_run", "completed"}:
            raise TechnicalFulltextSemanticAuditError(
                f"semantic audit pass has unsupported state: {semantic_status}"
            )

    @staticmethod
    def _revalidate_source_snapshot(audit: dict[str, Any]) -> None:
        try:
            resolved = TechnicalFulltextAuditor.revalidate_audit_source(audit)
        except TechnicalFulltextAuditError as exc:
            raise TechnicalFulltextSemanticAuditError(
                f"audit source snapshot is no longer trusted: {exc}"
            ) from exc
        source_run = audit.get("source_run") or {}
        run_path = Path(str(source_run.get("path") or "")).expanduser().resolve()
        try:
            if sha256_file(run_path) != source_run.get("sha256"):
                raise TechnicalFulltextSemanticAuditError("audit source_run SHA256 mismatch")
        except (FileNotFoundError, OSError) as exc:
            raise TechnicalFulltextSemanticAuditError(
                f"audit source_run is unavailable: {exc}"
            ) from exc
        audited_content_sha = (
            (audit.get("source_lineage") or {}).get("audited_fulltext") or {}
        ).get("content_sha256")
        if audited_content_sha != stable_json_sha256(resolved["fulltext"]):
            raise TechnicalFulltextSemanticAuditError(
                "normalized audit source content identity mismatch"
            )

    @staticmethod
    def _load_trusted_context(
        audit: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, bool]]:
        documents: dict[str, dict[str, Any]] = {}
        trust: dict[str, bool] = {}
        for name, metadata in (audit.get("input_context") or {}).items():
            trusted = isinstance(metadata, dict) and metadata.get("sha256_matches_run") is True
            if trusted:
                path = Path(str(metadata.get("path") or "")).expanduser().resolve()
                try:
                    trusted = sha256_file(path) == metadata.get("sha256")
                    if trusted:
                        documents[name] = load_json_object(path, label=name)
                except (FileNotFoundError, OSError, ValueError):
                    trusted = False
            trust[name] = trusted
        return documents, trust

    def _build_units(
        self,
        audit: dict[str, Any],
        *,
        context: dict[str, dict[str, Any]],
        trust: dict[str, bool],
    ) -> tuple[dict[str, list[SemanticAuditUnit]], dict[str, dict[str, Any]]]:
        normalized = audit["normalized_document"]
        paragraphs = normalized["paragraphs"]
        sections = normalized["sections"]
        chapters = normalized["chapters"]
        paragraph_by_id = {item["paragraph_id"]: item for item in paragraphs}
        paragraphs_by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for paragraph in paragraphs:
            paragraphs_by_section[paragraph["section_id"]].append(paragraph)
        section_by_id = {item["section_id"]: item for item in sections}
        chapter_by_id = {item["chapter_id"]: item for item in chapters}

        def locations(raw_locations: Iterable[dict[str, Any]], *, per_section: int = 2, limit: int = 16) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            seen: set[str] = set()
            for raw in raw_locations:
                candidates: list[dict[str, Any]] = []
                paragraph_id = str(raw.get("paragraph_id") or "")
                if paragraph_id and paragraph_id in paragraph_by_id:
                    candidates = [paragraph_by_id[paragraph_id]]
                else:
                    candidates = paragraphs_by_section.get(str(raw.get("section_id") or ""), [])[:per_section]
                for paragraph in candidates:
                    location = self._semantic_location(paragraph, section_by_id, chapter_by_id)
                    if location["location_id"] not in seen:
                        seen.add(location["location_id"])
                        result.append(location)
                    if len(result) >= limit:
                        return result
            return result

        candidates = audit["semantic_candidates"]
        units: dict[str, list[SemanticAuditUnit]] = {name: [] for name in PASS_ORDER}
        notes: dict[str, dict[str, Any]] = {}

        alias_candidates = candidates["confirmation_aliases"]
        for candidate in alias_candidates:
            locs = locations(candidate.get("locations") or [], limit=12)
            if not locs:
                continue
            ids = _unique(candidate.get("candidate_placeholder_ids") or [])
            payload = {
                "candidate_type": "placeholder_alias",
                "placeholder_ids": ids,
                "candidate_canonical_fact_type": candidate.get("candidate_canonical_fact_type") if trust.get("draft_units") else None,
                "entity_scope": candidate.get("entity_scope"),
                "reason_codes": candidate.get("reason_codes") or [],
                "planning_context_trusted": bool(trust.get("draft_units")),
            }
            units["placeholder_alias"].append(self._unit("placeholder_alias", [candidate["candidate_id"]], payload, locs, placeholder_ids=ids))
        notes["placeholder_alias"] = self._note(alias_candidates, units["placeholder_alias"])

        requirement_candidates = candidates["requirement_location_mismatches"]
        if not trust.get("response_outline") or not trust.get("draft_units"):
            notes["requirement_location"] = self._note(requirement_candidates, [], status="skipped", reason="untrusted_planning_context")
        else:
            coverage_by_id = {item["requirement_id"]: item for item in audit["coverage_analysis"]["requirements"]}
            task_metadata = self._draft_task_metadata(context["draft_units"])
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for candidate in requirement_candidates:
                grouped[candidate["requirement_id"]].append(candidate)
            for requirement_id, group in grouped.items():
                requirement_metadata = self._lookup_item(
                    context["response_outline"], requirement_id
                ) or {}
                requirement_text = self._lookup_text(context["response_outline"], requirement_id)
                coverage = coverage_by_id.get(requirement_id) or {}
                actual_locations = coverage.get("actual_locations") or [
                    loc
                    for item in group
                    for loc in item.get("outside_planned_locations") or []
                ]
                planned_primary = coverage.get("planned_primary_locations") or []
                planned_supporting = coverage.get("planned_supporting_locations") or []
                planned_table = coverage.get("planned_table_locations") or []
                planned_roles_by_section: dict[str, set[str]] = defaultdict(set)
                for role, planned_locations in (
                    ("primary", planned_primary),
                    ("supporting_reference", planned_supporting),
                    ("table_response", planned_table),
                ):
                    for item in planned_locations:
                        section_id = str(item.get("section_id") or "")
                        if section_id:
                            planned_roles_by_section[section_id].add(role)

                actual_contexts: list[dict[str, Any]] = []
                response_contexts: list[dict[str, Any]] = []
                context_locations: list[dict[str, Any]] = []
                seen_context_sections: set[str] = set()

                def add_context(raw: dict[str, Any], *, actual: bool) -> dict[str, Any] | None:
                    section_id = str(raw.get("section_id") or "")
                    section = section_by_id.get(section_id)
                    if not section:
                        return None
                    relevant = self._relevant_section_paragraphs(
                        paragraphs_by_section.get(section_id) or [], limit=2
                    )
                    semantic_locations = [
                        self._semantic_location(item, section_by_id, chapter_by_id)
                        for item in relevant
                    ]
                    context_locations.extend(semantic_locations)
                    roles = sorted(planned_roles_by_section.get(section_id) or [])
                    planned_tasks = [
                        task_metadata.get(str(item.get("task_id") or ""))
                        for item in planned_primary + planned_supporting + planned_table
                        if str(item.get("section_id") or "") == section_id
                        and task_metadata.get(str(item.get("task_id") or ""))
                    ]
                    all_paragraphs = paragraphs_by_section.get(section_id) or []
                    is_planned_primary = bool({"primary", "table_response"} & set(roles))
                    return {
                        "assessment_id": _stable_id("RLA-", {
                            "requirement_id": requirement_id,
                            "chapter_id": section["chapter_id"],
                            "section_id": section_id,
                        }),
                        "chapter_id": section["chapter_id"],
                        "chapter_title": (chapter_by_id.get(section["chapter_id"]) or {}).get("chapter_title") or "",
                        "section_id": section_id,
                        "section_heading": section.get("heading") or "",
                        "block_type": section.get("block_type") or "",
                        "section_semantic_role": self._section_semantic_role(section),
                        "planning_roles": roles,
                        "is_recorded_primary_or_table_response": is_planned_primary,
                        "is_planned_primary": is_planned_primary,
                        "is_outside_recorded_plan": actual and not bool(roles),
                        "section_declared_requirement_ids": section.get("covered_requirement_ids") or [],
                        "requirement_declared_for_section": requirement_id in (section.get("covered_requirement_ids") or []),
                        "content_state": "empty" if not all_paragraphs else "non_empty",
                        "content_character_count": sum(len(str(item.get("normalized_text") or "")) for item in all_paragraphs),
                        "relevant_excerpts": semantic_locations,
                        "planned_task_metadata": planned_tasks,
                    }

                for raw in actual_locations:
                    item = add_context(raw, actual=True)
                    if item is not None and item["section_id"] not in seen_context_sections:
                        seen_context_sections.add(item["section_id"])
                        actual_contexts.append(item)
                for raw in planned_primary + planned_table:
                    item = add_context(raw, actual=False)
                    if item is not None:
                        response_contexts.append(item)
                location_by_id = {
                    item["location_id"]: item for item in context_locations
                }
                locs = list(location_by_id.values())
                if not requirement_text or not locs:
                    continue
                requirement_planning = self._requirement_planning_metadata(
                    context["draft_units"], requirement_id
                )
                response_form_context = self._expected_requirement_response_form(
                    requirement_metadata=requirement_metadata,
                    requirement_text=requirement_text,
                    requirement_planning=requirement_planning,
                    planned_primary_contexts=response_contexts,
                    planned_table_locations=planned_table,
                )
                specialized_evidence_contexts = [
                    {
                        "assessment_id": item["assessment_id"],
                        "chapter_id": item["chapter_id"],
                        "section_id": item["section_id"],
                        "section_heading": item["section_heading"],
                        "requirement_declared_for_section": item[
                            "requirement_declared_for_section"
                        ],
                    }
                    for item in actual_contexts
                    if item["section_semantic_role"] == "evidence_or_material_index"
                    and item["requirement_declared_for_section"]
                ]
                source_ids = [item["candidate_id"] for item in group]
                payload = {
                    "requirement_id": requirement_id,
                    "requirement_text": requirement_text,
                    "requirement_source_metadata": {
                        "source_category": requirement_metadata.get("source_category"),
                        "original_heading": requirement_metadata.get("original_heading"),
                        "node_type": requirement_metadata.get("node_type"),
                        "content_type": requirement_metadata.get("content_type"),
                        "source_table": self._source_requirement_table_context(
                            requirement_metadata
                        ),
                    },
                    "expected_response_form_context": response_form_context,
                    "requirement_planning_metadata": requirement_planning,
                    "planned_response_responsibility_resolution": {
                        "recorded_primary_locations_are_exclusive": False,
                        "specialized_actual_evidence_sections": specialized_evidence_contexts,
                        "interpretation": (
                            "For evidence/index response forms, a dedicated actual evidence subsection "
                            "that declares and substantively indexes this Requirement may implement the "
                            "broader recorded primary index responsibility. Judge the overall response "
                            "responsibility, not literal duplication inside the parent section."
                        ),
                    },
                    "recorded_planning_context": {
                        "planned_primary_locations": planned_primary,
                        "planned_supporting_locations": planned_supporting,
                        "planned_table_locations": planned_table,
                        "is_exclusive_location_contract": False,
                        "interpretation": "Recorded planning locations identify intended response ownership, not all locations where the requirement may be mentioned or evidenced.",
                    },
                    "candidate_trigger": {
                        "type": "actual_location_outside_recorded_plan",
                        "locations": coverage.get("outside_planned_locations") or [],
                        "is_issue_by_itself": False,
                    },
                    "all_actual_locations": actual_contexts,
                    "required_assessment_ids": [
                        item["assessment_id"] for item in actual_contexts
                    ],
                    "planned_response_location_contexts": response_contexts,
                    "fact_permission_context": {
                        "meaning": "section_declared_requirement_ids may permit the requirement as source context; declaration does not make the section primary and absence does not make mention forbidden"
                    },
                    "instruction": "First assess adequacy using the supplied expected response form (not a universal narrative-prose standard), then classify every actual location. Related content, repeated values, or repeated locations do not establish an independent complete response without direct response intent and standalone satisfaction. Outside-plan presence is neutral and is not itself evidence of an issue.",
                }
                units["requirement_location"].append(self._unit("requirement_location", source_ids, payload, locs, requirement_ids=[requirement_id]))
            notes["requirement_location"] = self._note(requirement_candidates, units["requirement_location"])

        numeric_candidates = candidates["numeric_facts"]
        numeric_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for candidate in numeric_candidates:
            section = section_by_id.get(candidate["section_id"]) or {}
            requirement_ids = _unique(section.get("covered_requirement_ids") or [])
            topic = "requirements:" + ",".join(requirement_ids) if requirement_ids else "chapter:" + candidate["chapter_id"]
            numeric_groups[(self._numeric_family(candidate), topic)].append(candidate)
        for (family, topic), group in numeric_groups.items():
            if len(group) < 2:
                continue
            locs = locations(group, per_section=4, limit=16)
            source_ids = [item["candidate_id"] for item in group]
            requirement_ids = _unique(
                requirement_id
                for item in group
                for requirement_id in (section_by_id.get(item["section_id"]) or {}).get("covered_requirement_ids") or []
            )
            payload = {
                "unit_family": family,
                "topic_context": topic,
                "numeric_facts": [{key: item.get(key) for key in ("candidate_id", "raw_value", "normalized_value", "unit", "chapter_id", "section_id", "excerpt")} for item in group],
                "instruction": "Only report a conflict after establishing the same entity and the same attribute.",
            }
            units["numeric_consistency"].append(self._unit("numeric_consistency", source_ids, payload, locs, requirement_ids=requirement_ids))
        notes["numeric_consistency"] = self._note(numeric_candidates, units["numeric_consistency"], skipped=len(numeric_candidates) - sum(len(unit.source_candidate_ids) for unit in units["numeric_consistency"]))

        stable_fact_candidates = candidates.get("stable_fact_consistency") or []
        for candidate in stable_fact_candidates:
            facts = candidate.get("facts") or []
            locs = locations(facts, per_section=8, limit=32)
            if len(locs) < 2:
                continue
            payload = {
                "candidate_type": candidate.get("candidate_type"),
                "fact_type": candidate.get("fact_type"),
                "entity": candidate.get("entity"),
                "attribute": candidate.get("attribute"),
                "comparison_mode": candidate.get("comparison_mode"),
                "deterministic_decision": candidate.get("deterministic_decision"),
                "normalized_values": candidate.get("normalized_values") or [],
                "facts": [
                    {
                        key: item.get(key)
                        for key in (
                            "fact_id", "fact_type", "entity", "attribute",
                            "raw_value", "normalized_value", "unit",
                            "comparison_family", "scope", "comparator",
                            "chapter_id", "section_id", "paragraph_id",
                            "requirement_ids",
                        )
                    }
                    for item in facts
                ],
                "reason_codes": candidate.get("reason_codes") or [],
                "instruction": (
                    "Compare only the same entity and attribute. Different explicit phases, "
                    "fault levels, units, or conditional scopes are not conflicts."
                ),
            }
            units[STABLE_FACT_PASS_TYPE].append(
                self._unit(
                    STABLE_FACT_PASS_TYPE,
                    [candidate["candidate_id"]],
                    payload,
                    locs,
                    requirement_ids=candidate.get("requirement_ids") or [],
                )
            )
        notes[STABLE_FACT_PASS_TYPE] = self._note(
            stable_fact_candidates,
            units[STABLE_FACT_PASS_TYPE],
            skipped=len(stable_fact_candidates) - len(units[STABLE_FACT_PASS_TYPE]),
        )

        terminology_candidates = candidates["terminology"]
        known_terms = _unique(item.get("preferred_term") for item in terminology_candidates)
        for candidate in terminology_candidates:
            locs = locations(candidate.get("locations") or [], limit=12)
            if not locs:
                continue
            payload = {
                "preferred_term": candidate.get("preferred_term"),
                "detected_forms": candidate.get("suspected_variants") or [candidate.get("preferred_term")],
                "known_preferred_terms": known_terms,
                "terminology_constraint": candidate.get("terminology_constraint") if trust.get("draft_units") else None,
                "instruction": "Related object names such as system, node, and switch may denote different entities.",
            }
            units["terminology_consistency"].append(self._unit("terminology_consistency", [candidate["candidate_id"]], payload, locs))
        notes["terminology_consistency"] = self._note(terminology_candidates, units["terminology_consistency"])

        duplicate_candidates = candidates["duplicate_pairs"]
        for candidate in duplicate_candidates:
            locs = locations([candidate.get("location_a") or {}, candidate.get("location_b") or {}], per_section=1, limit=2)
            if len(locs) != 2:
                continue
            payload = {
                "similarity": candidate.get("similarity"),
                "text_a": candidate.get("text_a"),
                "text_b": candidate.get("text_b"),
                "instruction": "Distinguish necessary restatement or summary from meaningless duplication.",
            }
            units["near_duplicate"].append(self._unit("near_duplicate", [candidate["candidate_id"]], payload, locs))
        notes["near_duplicate"] = self._note(duplicate_candidates, units["near_duplicate"])

        score_candidates = candidates["score_distribution"]
        score_texts = self._score_texts(context.get("writing_outline") or {}) if trust.get("writing_outline") else {}
        for candidate in score_candidates:
            score_id = candidate.get("score_item_id")
            locs = locations(candidate.get("actual_locations") or candidate.get("locations") or [], limit=16)
            if not score_id or not locs or score_id not in score_texts:
                continue
            payload = {
                "score_item_id": score_id,
                "score_item_text": score_texts[score_id],
                "occurrence_count": candidate.get("occurrence_count"),
                "chapter_ids": candidate.get("chapter_ids") or [],
            }
            units["score_distribution"].append(self._unit("score_distribution", [candidate["candidate_id"]], payload, locs, score_item_ids=[score_id]))
        score_status = "completed" if trust.get("writing_outline") or not score_candidates else "skipped"
        score_reason = None if score_status == "completed" else "untrusted_or_insufficient_score_context"
        notes["score_distribution"] = self._note(score_candidates, units["score_distribution"], status=score_status, reason=score_reason)

        if trust.get("writing_outline"):
            responsibilities = self._chapter_responsibilities(context["writing_outline"])
            ordered = sorted(chapters, key=lambda item: item["chapter_order"])
            for previous, following in zip(ordered, ordered[1:]):
                previous_sections = [item for item in sections if item["chapter_id"] == previous["chapter_id"]][-2:]
                following_sections = [item for item in sections if item["chapter_id"] == following["chapter_id"]][:2]
                raw_locations: list[dict[str, Any]] = []
                for section in previous_sections:
                    raw_locations.extend(paragraphs_by_section[section["section_id"]][-2:])
                for section in following_sections:
                    raw_locations.extend(paragraphs_by_section[section["section_id"]][:2])
                locs = locations(raw_locations, per_section=2, limit=8)
                if not locs:
                    continue
                payload = {
                    "previous_chapter": previous,
                    "following_chapter": following,
                    "previous_responsibility": responsibilities.get(previous["chapter_id"]),
                    "following_responsibility": responsibilities.get(following["chapter_id"]),
                    "instruction": "Absence of an explicit transition sentence is not itself an issue; focus on reset, reintroduction, or logical discontinuity.",
                }
                units["adjacent_chapter_coherence"].append(self._unit("adjacent_chapter_coherence", [], payload, locs))
            notes["adjacent_chapter_coherence"] = self._note([], units["adjacent_chapter_coherence"], source_count=max(0, len(chapters) - 1))
        else:
            notes["adjacent_chapter_coherence"] = self._note([], [], status="skipped", reason="untrusted_writing_outline", source_count=max(0, len(chapters) - 1))
        return units, notes

    @staticmethod
    def _semantic_location(paragraph: dict[str, Any], section_by_id: dict[str, dict[str, Any]], chapter_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
        section = section_by_id.get(paragraph["section_id"]) or {}
        chapter = chapter_by_id.get(paragraph["chapter_id"]) or {}
        location = {
            "chapter_id": paragraph["chapter_id"],
            "chapter_title": chapter.get("chapter_title") or "",
            "section_id": paragraph["section_id"],
            "section_heading": section.get("heading") or "",
            "paragraph_id": paragraph["paragraph_id"],
            "paragraph_index": paragraph["paragraph_index"],
            "excerpt": str(paragraph.get("text") or "")[:800],
            "text_sha256": paragraph["text_sha256"],
        }
        return {"location_id": _location_id(location), **location}

    @staticmethod
    def _relevant_section_paragraphs(
        paragraphs: list[dict[str, Any]], *, limit: int
    ) -> list[dict[str, Any]]:
        """Prefer reviewable prose over Markdown-only headings without changing anchors."""
        substantive = [
            item
            for item in paragraphs
            if str(item.get("normalized_text") or "").strip()
            and not re.match(
                r"^\s{0,3}#{1,6}\s+\S+\s*$",
                str(item.get("text") or "").strip(),
            )
        ]
        return (substantive or paragraphs)[:limit]

    @staticmethod
    def _section_semantic_role(section: dict[str, Any]) -> str:
        heading = str(section.get("heading") or "").lower()
        block_type = str(section.get("block_type") or "").lower()
        if block_type == "required_response_table" or "应答表" in heading:
            return "formal_response_table"
        if block_type == "evidence_explanation" or any(
            token in heading for token in ("索引", "证明材料", "认证材料", "支撑材料", "附件")
        ):
            return "evidence_or_material_index"
        if block_type == "implementation_plan" and any(
            token in heading for token in ("周期", "进度", "实施", "交付安排")
        ):
            return "implementation_or_delivery_context"
        if block_type == "service_commitment":
            return "formal_commitment_section"
        if block_type == "requirement_response":
            return "substantive_requirement_response"
        if block_type == "organization_plan":
            return "organization_and_staffing_response"
        return block_type or "general_section"

    @staticmethod
    def _draft_task_metadata(draft_units: dict[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for unit in draft_units.get("draft_units") or []:
            if not isinstance(unit, dict):
                continue
            constraints = (
                unit.get("generation_constraints")
                if isinstance(unit.get("generation_constraints"), dict)
                else {}
            )
            metadata = {
                "draft_unit_id": unit.get("draft_unit_id"),
                "title": unit.get("title"),
                "chapter_id": unit.get("chapter_id"),
                "content_mode": unit.get("content_mode"),
                "response_point_ids": unit.get("response_point_ids") or [],
                "primary_content_locations": constraints.get("primary_content_locations") or [],
                "supporting_reference_locations": constraints.get("supporting_reference_locations") or [],
                "table_response_locations": constraints.get("table_response_locations") or [],
                "avoid_repeating_topics": constraints.get("avoid_repeating_topics") or [],
                "transition_required": constraints.get("transition_required"),
            }
            for task_id in unit.get("task_ids") or []:
                result[str(task_id)] = metadata
        return result

    @staticmethod
    def _requirement_planning_metadata(
        draft_units: dict[str, Any], requirement_id: str
    ) -> dict[str, Any]:
        """Collect existing planning signals without inventing a second planner."""
        matches: list[dict[str, Any]] = []
        for unit in draft_units.get("draft_units") or []:
            if not isinstance(unit, dict) or requirement_id not in (
                unit.get("response_point_ids") or []
            ):
                continue
            constraints = (
                unit.get("generation_constraints")
                if isinstance(unit.get("generation_constraints"), dict)
                else {}
            )
            matches.append({
                "draft_unit_id": unit.get("draft_unit_id"),
                "title": unit.get("title"),
                "chapter_id": unit.get("chapter_id"),
                "content_mode": unit.get("content_mode"),
                "primary_content_locations": constraints.get("primary_content_locations") or [],
                "supporting_reference_locations": constraints.get("supporting_reference_locations") or [],
                "table_response_locations": constraints.get("table_response_locations") or [],
                "shared_local_evidence_types": unit.get("shared_local_evidence_types") or [],
                "draftability": deepcopy(unit.get("draftability")),
            })
        return {
            "matched_draft_units": matches,
            "content_modes": sorted(_unique(item.get("content_mode") for item in matches)),
            "planning_source": "trusted_draft_units",
        }

    @classmethod
    def _expected_requirement_response_form(
        cls,
        *,
        requirement_metadata: dict[str, Any],
        requirement_text: str,
        requirement_planning: dict[str, Any],
        planned_primary_contexts: list[dict[str, Any]],
        planned_table_locations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Derive a bounded response-form hint from already trusted artifacts."""
        content_modes = set(requirement_planning.get("content_modes") or [])
        primary_roles = {
            str(item.get("section_semantic_role") or "")
            for item in planned_primary_contexts
        }
        primary_block_types = {
            str(item.get("block_type") or "") for item in planned_primary_contexts
        }
        text = requirement_text.lower()
        evidence_or_material = bool(re.search(
            r"证明材料|检测报告|检验报告|试验报告|测试报告|认证证书|资质证明|证书扫描件|附件",
            text,
        ))
        table_or_form = bool(planned_table_locations) or bool(
            {"table", "form"}
            & {
                str(requirement_metadata.get("node_type") or "").lower(),
                str(requirement_metadata.get("content_type") or "").lower(),
            }
        ) or "table" in content_modes or "formal_response_table" in primary_roles
        index_or_appendix = "appendix" in content_modes or bool(
            {"evidence_explanation"} & primary_block_types
        ) or "evidence_or_material_index" in primary_roles

        if table_or_form and index_or_appendix:
            expected_form = "mixed"
        elif table_or_form:
            expected_form = "table_response"
        elif evidence_or_material and index_or_appendix:
            expected_form = "index_response"
        elif evidence_or_material:
            expected_form = "evidence_response"
        elif content_modes == {"narrative"}:
            expected_form = "narrative_response"
        elif index_or_appendix:
            expected_form = "attachment_reference"
        elif len(content_modes) > 1 or "mixed" in content_modes:
            expected_form = "mixed"
        else:
            expected_form = "narrative_response"

        table_context_visibility = "not_applicable"
        if expected_form in {"table_response", "mixed"}:
            # Normalized excerpts expose prose paragraphs, not the source table's
            # complete cells. The model must use the safe insufficient exit when
            # those cells are needed to judge adequacy.
            table_context_visibility = "summary_only"

        return {
            "expected_response_form": expected_form,
            "allowed_values": [
                "narrative_response", "table_response", "evidence_response",
                "attachment_reference", "index_response", "mixed",
            ],
            "derivation": {
                "response_outline": {
                    "node_type": requirement_metadata.get("node_type"),
                    "content_type": requirement_metadata.get("content_type"),
                },
                "draft_unit_content_modes": sorted(content_modes),
                "planned_primary_section_roles": sorted(primary_roles),
                "planned_primary_block_types": sorted(primary_block_types),
                "has_planned_table_location": bool(planned_table_locations),
                "requirement_is_evidence_or_material_oriented": evidence_or_material,
            },
            "table_content_visibility": table_context_visibility,
            "adequacy_boundary": (
                "Judge whether the planned response responsibility is substantively present in its expected form. "
                "Evidence/index adequacy does not confirm that a pending document is authentic, valid, uploaded, or human-confirmed."
            ),
            "material_truth_status_is_separate": True,
        }

    @staticmethod
    def _unit(pass_type: str, source_ids: list[str], payload: dict[str, Any], locations: list[dict[str, Any]], *, requirement_ids: list[str] | None = None, score_item_ids: list[str] | None = None, placeholder_ids: list[str] | None = None) -> SemanticAuditUnit:
        identity = {
            "pass_type": pass_type,
            "source_candidate_ids": sorted(source_ids),
            "location_ids": sorted(item["location_id"] for item in locations),
            "subject": payload.get("requirement_id") or payload.get("score_item_id") or payload.get("preferred_term") or payload.get("topic_context") or [payload.get("previous_chapter", {}).get("chapter_id"), payload.get("following_chapter", {}).get("chapter_id")],
        }
        return SemanticAuditUnit(
            audit_unit_id=_stable_id("FSU-", identity),
            pass_type=pass_type,
            source_candidate_ids=sorted(source_ids),
            payload=payload,
            locations=locations,
            requirement_ids=sorted(_unique(requirement_ids or [])),
            score_item_ids=sorted(_unique(score_item_ids or [])),
            placeholder_ids=sorted(_unique(placeholder_ids or [])),
        )

    @staticmethod
    def _note(source: list[Any], units: list[SemanticAuditUnit], *, status: str = "completed", reason: str | None = None, skipped: int | None = None, source_count: int | None = None) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "source_candidate_count": len(source) if source_count is None else source_count,
            "skipped_candidate_count": max(0, len(source) - len(units)) if skipped is None else max(0, skipped),
        }

    @staticmethod
    def _lookup_text(document: dict[str, Any], item_id: str) -> str | None:
        for item in _walk_dicts(document):
            identities = {str(item.get(key) or "") for key in ("requirement_id", "response_point_id", "point_id", "id")}
            if item_id not in identities:
                continue
            for key in ("original_text", "requirement_text", "criteria_summary", "title", "text", "summary"):
                value = str(item.get(key) or "").strip()
                if value:
                    return value[:3000]
        return None

    @staticmethod
    def _lookup_item(document: dict[str, Any], item_id: str) -> dict[str, Any] | None:
        for item in _walk_dicts(document):
            identities = {
                str(item.get(key) or "")
                for key in ("requirement_id", "response_point_id", "point_id", "id")
            }
            if item_id in identities:
                return item
        return None

    @staticmethod
    def _source_requirement_table_context(
        requirement_metadata: dict[str, Any],
        *,
        cell_limit: int = 48,
    ) -> dict[str, Any] | None:
        """Expose already parsed tender cells without treating them as bid response cells."""
        table = requirement_metadata.get("table")
        if not isinstance(table, dict):
            return None
        rows = [item for item in table.get("rows") or [] if isinstance(item, dict)]
        cells: list[dict[str, Any]] = []
        total_cell_count = 0
        column_count = 0
        for row in rows:
            for cell in row.get("cells") or []:
                if not isinstance(cell, dict):
                    continue
                total_cell_count += 1
                column = int(cell.get("column") or 0)
                column_count = max(column_count, column)
                text = str(cell.get("text") or "").strip()
                if len(cells) >= cell_limit or not text:
                    continue
                cells.append({
                    "row": int(cell.get("row") or row.get("row") or 0),
                    "column": column,
                    "rowspan": int(cell.get("rowspan") or 1),
                    "colspan": int(cell.get("colspan") or 1),
                    "text": text[:500],
                })
        if not rows or total_cell_count == 0:
            return None
        source_refs = [
            item
            for item in requirement_metadata.get("source_refs") or []
            if isinstance(item, dict)
        ]
        source_ref = source_refs[0] if source_refs else {}
        return {
            "visibility": "structured_cells",
            "source_role": "tender_requirement_source_only",
            "is_bid_response_evidence": False,
            "row_count": len(rows),
            "column_count": column_count,
            "total_cell_count": total_cell_count,
            "visible_cell_count": len(cells),
            "is_bounded_projection": len(cells) < total_cell_count,
            "table_name": source_ref.get("table_name") or "",
            "table_range": source_ref.get("table_range") or "",
            "cells": cells,
        }

    @staticmethod
    def _score_texts(writing_outline: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in writing_outline.get("score_items") or []:
            if isinstance(item, dict) and item.get("score_item_id"):
                text = " | ".join(_unique([item.get("title"), item.get("criteria_summary"), item.get("score")]))
                if text:
                    result[str(item["score_item_id"])] = text[:3000]
        return result

    @staticmethod
    def _chapter_responsibilities(writing_outline: dict[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in _walk_dicts(writing_outline.get("chapters") or []):
            chapter_id = str(item.get("chapter_id") or "")
            if chapter_id:
                writing_requirements = item.get("writing_requirements")
                if isinstance(writing_requirements, list):
                    bounded_requirements: Any = writing_requirements[:8]
                elif isinstance(writing_requirements, dict):
                    bounded_requirements = {
                        key: writing_requirements[key]
                        for key in list(writing_requirements)[:8]
                    }
                else:
                    bounded_requirements = writing_requirements
                result[chapter_id] = {
                    "title": item.get("title") or item.get("chapter_title"),
                    "chapter_type": item.get("chapter_type"),
                    "writing_requirements": bounded_requirements,
                    "adaptation_reason": item.get("adaptation_reason"),
                }
        return result

    @staticmethod
    def _numeric_family(candidate: dict[str, Any]) -> str:
        raw = str(candidate.get("raw_value") or "").lower()
        unit = str(candidate.get("unit") or "").lower()
        if any(token in raw + unit for token in ("日", "天", "小时", "年", "分钟", "秒")):
            return "duration"
        if "%" in raw or "％" in raw:
            return "availability_percentage"
        if any(token in raw + unit for token in ("gbps", "mbps", "带宽", "速率")):
            return "bandwidth_rate"
        if any(token in raw + unit for token in ("gb", "tb", "mb", "容量")):
            return "capacity_storage"
        if "gb/t" in raw or "iso" in raw:
            return "standard_reference"
        if "版本" in raw or "version" in raw:
            return "version"
        return "quantity_count"

    def _load_result_schema(self) -> dict[str, Any]:
        try:
            return load_json_object(self.result_schema_path, label="semantic audit result schema")
        except (FileNotFoundError, ValueError) as exc:
            raise TechnicalFulltextSemanticAuditError(f"semantic result schema is unavailable: {exc}") from exc

    def _model_configuration(self, client: LLMClient) -> dict[str, str]:
        """Return a secret-free, replay-stable model/provider configuration identity."""
        profile = getattr(client, "profile", None)
        if profile is not None:
            identity_payload = {
                "profile_name": str(getattr(profile, "name", self.model_profile) or self.model_profile),
                "provider": str(getattr(profile, "provider", "") or ""),
                "protocol": str(getattr(profile, "protocol", "") or ""),
                "model": str(getattr(profile, "model", "") or ""),
                "api_mode": str(getattr(profile, "api_mode", "") or ""),
                "structured_output_mode": str(
                    getattr(profile, "structured_output_mode", "") or ""
                ),
                "temperature": getattr(profile, "temperature", None),
                "client_type": f"{type(client).__module__}.{type(client).__qualname__}",
            }
        else:
            # Injected test/provider adapters do not necessarily expose a profile.
            # Their concrete class plus the selected profile is the strongest
            # configuration identity available before making a model call.
            identity_payload = {
                "profile_name": self.model_profile,
                "provider": str(getattr(client, "provider", "") or ""),
                "protocol": str(getattr(client, "protocol", "") or ""),
                "model": str(getattr(client, "model", "") or ""),
                "api_mode": str(getattr(client, "api_mode", "") or ""),
                "structured_output_mode": "",
                "temperature": None,
                "client_type": f"{type(client).__module__}.{type(client).__qualname__}",
            }
        return {
            "identity_sha256": stable_json_sha256(identity_payload),
            "profile_name": identity_payload["profile_name"],
            "provider": identity_payload["provider"],
            "model": identity_payload["model"],
        }

    @staticmethod
    def _non_requirement_source_context_sha(audit: dict[str, Any]) -> str:
        return stable_json_sha256({
            "source_lineage": audit.get("source_lineage"),
            "source_commit": audit.get("source_commit"),
            "source_run": audit.get("source_run"),
            "input_context": audit.get("input_context"),
        })

    @staticmethod
    def _audited_content_sha(audit: dict[str, Any]) -> str:
        value = str(
            ((audit.get("source_lineage") or {}).get("audited_fulltext") or {}).get(
                "content_sha256"
            )
            or ""
        )
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise TechnicalFulltextSemanticAuditError(
                "semantic observation reuse requires a valid audited content SHA"
            )
        return value

    @classmethod
    def _non_requirement_fingerprint(
        cls,
        *,
        unit: SemanticAuditUnit,
        audited_content_sha256: str,
        source_context_sha256: str,
        model_configuration_sha256: str,
    ) -> str:
        if unit.pass_type == "requirement_location":
            raise ValueError("non-Requirement fingerprint cannot bind a Requirement unit")
        return stable_json_sha256({
            "pass_type": unit.pass_type,
            "semantic_unit_type": NON_REQUIREMENT_SEMANTIC_UNIT_TYPE,
            "audit_unit_id": unit.audit_unit_id,
            "normalized_observation_payload_sha256": stable_json_sha256(
                unit.model_payload()
            ),
            "audited_content_sha256": audited_content_sha256,
            "source_context_sha256": source_context_sha256,
            "observation_contract_version": NON_REQUIREMENT_OBSERVATION_CONTRACT_VERSION,
            "prompt_version": NON_REQUIREMENT_PROMPT_VERSION,
            "result_schema_version": SEMANTIC_RESULT_SCHEMA_VERSION,
            "model_configuration_sha256": model_configuration_sha256,
        })

    @staticmethod
    def _non_requirement_observation(result: dict[str, Any]) -> dict[str, Any]:
        observation = deepcopy(result)
        # Verdict is a runtime policy output. It is deliberately not cached.
        observation.pop("verdict", None)
        return observation

    @classmethod
    def _formalize_non_requirement_observation(
        cls, *, unit: SemanticAuditUnit, observation: dict[str, Any]
    ) -> dict[str, Any]:
        result = deepcopy(observation)
        decision = result.get("decision")
        verdict = DECISION_VERDICTS.get(unit.pass_type, {}).get(decision)
        if verdict is None:
            raise TechnicalFulltextSemanticAuditError(
                f"cached non-Requirement decision {decision!r} is invalid for pass {unit.pass_type}"
            )
        result["verdict"] = verdict
        cls._validate_non_requirement_result(result, unit=unit)
        return result

    @classmethod
    def _validated_non_requirement_cache(
        cls,
        audit: dict[str, Any],
        units_by_pass: dict[str, list[SemanticAuditUnit]],
        *,
        audited_content_sha256: str,
        source_context_sha256: str,
        model_configuration: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        units = {
            unit.audit_unit_id: unit
            for pass_type, pass_units in units_by_pass.items()
            if pass_type != "requirement_location"
            for unit in pass_units
        }
        expected = {
            unit.audit_unit_id: cls._non_requirement_fingerprint(
                unit=unit,
                audited_content_sha256=audited_content_sha256,
                source_context_sha256=source_context_sha256,
                model_configuration_sha256=model_configuration["identity_sha256"],
            )
            for unit in units.values()
        }
        valid: dict[str, dict[str, Any]] = {}
        for entry in audit.get("semantic_observation_cache") or []:
            if not isinstance(entry, dict) or entry.get("pass_type") == "requirement_location":
                continue
            unit_id = str(entry.get("audit_unit_id") or "")
            unit = units.get(unit_id)
            if unit is None or entry.get("pass_type") != unit.pass_type:
                continue
            fingerprint = expected[unit_id]
            # A compatible miss is not corruption; it simply requires a new
            # observation for this unit. Policy version is intentionally absent
            # from the compatibility fingerprint and is replayed below.
            if entry.get("semantic_fingerprint") != fingerprint:
                continue
            required_identity = {
                "semantic_unit_type": NON_REQUIREMENT_SEMANTIC_UNIT_TYPE,
                "normalized_observation_payload_sha256": stable_json_sha256(
                    unit.model_payload()
                ),
                "audited_content_sha256": audited_content_sha256,
                "source_context_sha256": source_context_sha256,
                "observation_contract_version": NON_REQUIREMENT_OBSERVATION_CONTRACT_VERSION,
                "prompt_version": NON_REQUIREMENT_PROMPT_VERSION,
                "result_schema_version": SEMANTIC_RESULT_SCHEMA_VERSION,
                "model_configuration_sha256": model_configuration["identity_sha256"],
                "validation_status": "validated",
            }
            mismatched = [
                key for key, value in required_identity.items() if entry.get(key) != value
            ]
            if mismatched:
                raise TechnicalFulltextSemanticAuditError(
                    "compatible non-Requirement observation cache identity is corrupt: "
                    f"unit={unit_id}, fields={mismatched}"
                )
            observation = entry.get("observation")
            if not isinstance(observation, dict):
                raise TechnicalFulltextSemanticAuditError(
                    f"compatible non-Requirement observation cache is missing payload: {unit_id}"
                )
            if entry.get("observation_sha256") != stable_json_sha256(observation):
                raise TechnicalFulltextSemanticAuditError(
                    f"compatible non-Requirement observation cache checksum mismatch: {unit_id}"
                )
            cls._formalize_non_requirement_observation(unit=unit, observation=observation)
            if fingerprint in valid:
                raise TechnicalFulltextSemanticAuditError(
                    f"duplicate compatible non-Requirement observation cache entry: {unit_id}"
                )
            valid[fingerprint] = deepcopy(entry)
        return valid

    @classmethod
    def _validated_requirement_cache(
        cls, audit: dict[str, Any], units: list[SemanticAuditUnit]
    ) -> dict[str, dict[str, Any]]:
        unit_by_fingerprint = {unit.semantic_fingerprint(): unit for unit in units}
        valid: dict[str, dict[str, Any]] = {}
        for entry in audit.get("semantic_observation_cache") or []:
            if not isinstance(entry, dict):
                continue
            fingerprint = str(entry.get("semantic_fingerprint") or "")
            unit = unit_by_fingerprint.get(fingerprint)
            if unit is None:
                continue
            expected_versions = {
                "pass_type": "requirement_location",
                "semantic_unit_type": REQUIREMENT_SEMANTIC_UNIT_TYPE,
                "observation_contract_version": REQUIREMENT_OBSERVATION_CONTRACT_VERSION,
                "prompt_version": REQUIREMENT_PROMPT_VERSION,
                "result_schema_version": SEMANTIC_RESULT_SCHEMA_VERSION,
                "decision_policy_version": REQUIREMENT_DECISION_POLICY_VERSION,
            }
            if any(entry.get(key) != value for key, value in expected_versions.items()):
                continue
            if entry.get("validation_status", "validated") != "validated":
                continue
            observation = entry.get("observation")
            if not isinstance(observation, dict):
                continue
            try:
                cls._validate_requirement_observation(observation, unit)
                contexts = cls._requirement_assessment_context(unit)
                RequirementSemanticDecisionPolicy.decide(
                    observation,
                    planned_primary_assessment_ids={
                        assessment_id
                        for assessment_id, context in contexts.items()
                        if context.get("is_planned_primary")
                    },
                )
            except (KeyError, TypeError, TechnicalFulltextSemanticAuditError):
                continue
            valid[fingerprint] = deepcopy(entry)
        return valid

    @staticmethod
    def _requirement_assessment_context(unit: SemanticAuditUnit) -> dict[str, dict[str, Any]]:
        return {
            item["assessment_id"]: item
            for item in unit.payload.get("all_actual_locations") or []
        }

    @classmethod
    def _validate_requirement_observation(
        cls, result: dict[str, Any], unit: SemanticAuditUnit
    ) -> None:
        contexts = cls._requirement_assessment_context(unit)
        assessments = result["actual_location_assessments"]
        ids = [item["assessment_id"] for item in assessments]
        if len(ids) != len(set(ids)) or set(ids) != set(contexts):
            raise RequirementObservationConsistencyError(
                "requirement observation must assess every supplied actual location exactly once"
            )
        if set(result["requirement_ids"]) != set(unit.requirement_ids):
            raise TechnicalFulltextSemanticAuditError(
                "requirement observation must bind its supplied requirement"
            )
        for assessment in assessments:
            role = assessment["semantic_role"]
            level = assessment["substantive_level"]
            complete = assessment["independent_complete_response"]
            if role in {"supporting_reference", "evidence_reference", "contextual_mention", "generic_promise"} and complete == "yes":
                raise RequirementObservationConsistencyError(
                    f"{role} cannot be an independent complete response"
                )
            if role == "generic_promise" and (level == "substantive" or complete == "yes"):
                raise RequirementObservationConsistencyError(
                    "generic promise cannot be substantive or independently complete"
                )
            if role == "independent_response" and complete == "no":
                raise RequirementObservationConsistencyError(
                    "independent response cannot explicitly be incomplete"
                )
            if role == "primary_response" and not contexts[assessment["assessment_id"]].get("is_planned_primary"):
                raise RequirementObservationConsistencyError(
                    "model assigned primary_response to a non-primary assessment target"
                )

    @classmethod
    def _formalize_requirement_observation(
        cls,
        *,
        unit: SemanticAuditUnit,
        observation: dict[str, Any],
        fingerprint: str,
        reused: bool,
        reuse_source: dict[str, Any] | None = None,
        validation_status: str = "validated",
    ) -> dict[str, Any]:
        cls._validate_requirement_observation(observation, unit)
        contexts = cls._requirement_assessment_context(unit)
        decision, verdict, rule = RequirementSemanticDecisionPolicy.decide(
            observation,
            planned_primary_assessment_ids={
                assessment_id
                for assessment_id, context in contexts.items()
                if context.get("is_planned_primary")
            },
        )
        is_issue = verdict == "issue"
        return {
            "audit_unit_id": unit.audit_unit_id,
            "decision": decision,
            "verdict": verdict,
            "confidence": observation["confidence"] if verdict != "uncertain" else "low",
            "subject": deepcopy(observation["subject"]),
            "location_ids": [item["location_id"] for item in unit.locations],
            "requirement_ids": list(unit.requirement_ids),
            "score_item_ids": [],
            "placeholder_ids": [],
            "summary": observation["summary"],
            "rationale": observation["rationale"],
            "details": deepcopy(observation["details"]),
            "suggested_severity": (
                "blocking" if decision == "coverage_content_mismatch"
                else "warning" if is_issue else None
            ),
            "suggested_release_blocking": decision == "coverage_content_mismatch",
            "recommended_action": deepcopy(observation["recommended_action"]),
            "requires_human_review": verdict != "acceptable",
            "semantic_observation": deepcopy(observation),
            "requirement_decision_provenance": {
                "semantic_fingerprint": fingerprint,
                "observation_contract_version": REQUIREMENT_OBSERVATION_CONTRACT_VERSION,
                "prompt_version": REQUIREMENT_PROMPT_VERSION,
                "result_schema_version": SEMANTIC_RESULT_SCHEMA_VERSION,
                "decision_policy_version": REQUIREMENT_DECISION_POLICY_VERSION,
                "decision_rule": rule,
                "observation_validation_status": validation_status,
                "observation_reused": reused,
                "reuse_source_finding_id": (reuse_source or {}).get("source_finding_id"),
            },
        }

    @staticmethod
    def _deterministic_stable_fact_result(
        unit: SemanticAuditUnit,
    ) -> dict[str, Any] | None:
        decision = unit.payload.get("deterministic_decision")
        if decision not in {"consistent", "context_different", "stable_fact_conflict"}:
            return None
        facts = unit.payload.get("facts") or []
        values = _unique(
            f"{item.get('chapter_id')}/{item.get('section_id')}: "
            f"{item.get('raw_value')} -> {item.get('normalized_value')} {item.get('unit')}"
            for item in facts
        )
        entity = str(unit.payload.get("entity") or "stable_fact")
        attribute = str(unit.payload.get("attribute") or "value")
        subject = {
            "subject_type": "stable_fact",
            "canonical_key": f"{entity}.{attribute}",
            "display_name": f"{entity} / {attribute}",
        }
        if decision == "stable_fact_conflict":
            verdict = "issue"
            summary = "跨章节稳定事实存在明显冲突：" + "；".join(values)
            rationale = (
                "各位置指向同一实体和同一稳定属性，归一化后值不同，且未发现可区分的阶段或条件范围。"
            )
            action = {
                "resolution_kind": "resolve_stable_fact_conflict",
                "text": "核对权威事实来源，统一所有列示位置，并保留一处可追溯的正式承诺。",
            }
        elif decision == "context_different":
            verdict = "acceptable"
            summary = "跨章节数值属于不同明确上下文，不构成稳定事实冲突。"
            rationale = "位置具有可区分的阶段、故障级别或条件范围，运行时未将数值差异误判为冲突。"
            action = {"resolution_kind": "none", "text": "无需处理。"}
        else:
            verdict = "acceptable"
            summary = "同一跨章节稳定事实归一化值一致。"
            rationale = "各位置指向同一实体和属性，归一化后的值及可比单位一致。"
            action = {"resolution_kind": "none", "text": "无需处理。"}
        return {
            "audit_unit_id": unit.audit_unit_id,
            "decision": decision,
            "verdict": verdict,
            "confidence": "high",
            "subject": subject,
            "location_ids": [item["location_id"] for item in unit.locations],
            "requirement_ids": list(unit.requirement_ids),
            "score_item_ids": [],
            "placeholder_ids": [],
            "summary": summary,
            "rationale": rationale,
            "details": {
                "values": values,
                "comparison": decision,
                "evidence_notes": _unique(unit.payload.get("reason_codes") or []),
            },
            "suggested_severity": "blocking" if verdict == "issue" else None,
            "suggested_release_blocking": verdict == "issue",
            "recommended_action": action,
            "requires_human_review": verdict == "issue",
        }

    @classmethod
    def _uncertain_requirement_observation(
        cls, unit: SemanticAuditUnit, *, reason: str
    ) -> dict[str, Any]:
        return {
            "audit_unit_id": unit.audit_unit_id,
            "evidence_sufficiency": "insufficient",
            "primary_response_status": "uncertain",
            "actual_location_assessments": [
                {
                    "assessment_id": item["assessment_id"],
                    "semantic_role": "uncertain",
                    "substantive_level": "uncertain",
                    "independent_complete_response": "uncertain",
                }
                for item in unit.payload.get("all_actual_locations") or []
            ],
            "confidence": "low",
            "subject": {
                "subject_type": "requirement",
                "canonical_key": unit.requirement_ids[0],
                "display_name": unit.requirement_ids[0],
            },
            "requirement_ids": list(unit.requirement_ids),
            "summary": "现有语义观察无法形成一致且充分的机器判断。",
            "rationale": reason[:1000],
            "details": {"values": [], "comparison": "uncertain", "evidence_notes": [reason[:1000]]},
            "recommended_action": {"resolution_kind": "human_review", "text": "请人工核验主响应充分性及各位置的内容职责。"},
        }

    def _call_batch(self, *, client: LLMClient, audit: dict[str, Any], batch_id: str, pass_type: str, units: list[SemanticAuditUnit], response_schema: dict[str, Any]) -> tuple[list[dict[str, Any]], list[ModelCallResult]]:
        self._validate_unit_identity_list(units, pass_type=pass_type)
        batch_schema = deepcopy(response_schema)
        batch_schema["properties"]["batch_id"] = {"const": batch_id}
        batch_schema["properties"]["pass_type"] = {"const": pass_type}
        expected_unit_ids = [unit.audit_unit_id for unit in units]
        if pass_type == "requirement_location":
            if len(units) != 1:
                raise TechnicalFulltextSemanticAuditError(
                    "requirement_location provider calls must contain exactly one audit unit"
                )
            expected_requirement_ids = list(units[0].requirement_ids)
            batch_schema["properties"]["results"] = {
                "type": "array",
                "minItems": len(units),
                "maxItems": len(units),
                "items": {
                    "allOf": [
                        {"$ref": "#/$defs/requirementObservationResult"},
                        {
                            "type": "object",
                            "properties": {
                                "audit_unit_id": {"const": expected_unit_ids[0]},
                                "requirement_ids": {
                                    "const": expected_requirement_ids
                                },
                            },
                        },
                    ]
                },
            }
        else:
            result_definition = batch_schema["$defs"]["result"]
            result_definition["allOf"] = [{
                "oneOf": [
                    {
                        "type": "object",
                        "required": ["decision", "verdict"],
                        "properties": {
                            "decision": {"const": decision},
                            "verdict": {"const": verdict},
                        },
                    }
                    for decision, verdict in DECISION_VERDICTS[pass_type].items()
                ]
            }]
        batch_payload = {
            "batch_id": batch_id,
            "pass_type": pass_type,
            "document_id": audit["document_id"],
            "allowed_decision_verdict_pairs": (
                None if pass_type == "requirement_location" else DECISION_VERDICTS[pass_type]
            ),
            "units": [unit.model_payload() for unit in units],
        }
        calls: list[ModelCallResult] = []
        validation_feedback = ""
        for attempt in range(self.runtime_validation_retries + 1):
            prompt = self._system_prompt(pass_type)
            user_prompt = json.dumps(batch_payload, ensure_ascii=False, separators=(",", ":"))
            if validation_feedback:
                user_prompt += "\nRUNTIME_VALIDATION_ERROR_FROM_PREVIOUS_ATTEMPT:\n" + validation_feedback
            try:
                call = client.generate_structured(
                    system_prompt=prompt,
                    user_prompt=user_prompt,
                    response_schema=batch_schema,
                    request_metadata={
                        "stage": "11-C",
                        "batch_id": batch_id,
                        "pass_type": pass_type,
                        "audit_unit_ids": [unit.audit_unit_id for unit in units],
                    },
                    profile_name=self.model_profile,
                )
                calls.append(call)
                self._validate_batch_output(call.parsed_output, batch_id=batch_id, pass_type=pass_type, units=units, schema=batch_schema)
                if pass_type == "requirement_location":
                    unit_map = {unit.audit_unit_id: unit for unit in units}
                    formal = []
                    for observation in call.parsed_output["results"]:
                        unit = unit_map[observation["audit_unit_id"]]
                        formal.append(self._formalize_requirement_observation(
                            unit=unit,
                            observation=observation,
                            fingerprint=unit.semantic_fingerprint(),
                            reused=False,
                        ))
                    return formal, calls
                return call.parsed_output["results"], calls
            except Exception as exc:
                validation_feedback = f"{type(exc).__name__}: {exc}"[:2000]
                if attempt >= self.runtime_validation_retries and isinstance(
                    exc, RequirementObservationConsistencyError
                ):
                    unit_map = {unit.audit_unit_id: unit for unit in units}
                    formal: list[dict[str, Any]] = []
                    for observation in calls[-1].parsed_output["results"]:
                        unit = unit_map[observation["audit_unit_id"]]
                        try:
                            formal.append(self._formalize_requirement_observation(
                                unit=unit,
                                observation=observation,
                                fingerprint=unit.semantic_fingerprint(),
                                reused=False,
                                validation_status="runtime_fallback",
                            ))
                        except RequirementObservationConsistencyError as unit_exc:
                            formal.append(self._formalize_requirement_observation(
                                unit=unit,
                                observation=self._uncertain_requirement_observation(
                                    unit,
                                    reason=f"{type(unit_exc).__name__}: {unit_exc}",
                                ),
                                fingerprint=unit.semantic_fingerprint(),
                                reused=False,
                            ))
                    return formal, calls
                if attempt >= self.runtime_validation_retries:
                    raise TechnicalFulltextSemanticAuditError(
                        f"semantic batch {batch_id} failed after {attempt + 1} attempt(s): {validation_feedback}"
                    ) from exc
        raise AssertionError("semantic retry loop exhausted unexpectedly")

    @staticmethod
    def _system_prompt(pass_type: str) -> str:
        decision_contract = json.dumps(DECISION_VERDICTS[pass_type], ensure_ascii=False)
        requirement_calibration = ""
        if pass_type == "requirement_location":
            requirement_calibration = """
REQUIREMENT SEMANTIC OBSERVATION CONTRACT:
- You do NOT decide acceptable_reference, redundant_response, misplaced_content, coverage_content_mismatch, verdict, severity, or release blocking. Runtime owns all formal decisions.
- Return only evidence_sufficiency, primary_response_status, and one assessment for every supplied actual location assessment_id.
- Copy required_assessment_ids exactly: output one and only one actual_location_assessments entry for every ID, including planned primary and outside locations. Do not omit a location because it is merely evidence/index support.
- evidence_sufficiency=sufficient only when the visible requirement, planning context, excerpts, table/evidence context are enough for a reliable ownership decision; otherwise use insufficient.
- primary_response_status: adequate means substantive, complete and independently reviewable; inadequate means present but materially incomplete; missing means no substantive response; uncertain means context cannot establish sufficiency.
- semantic_role: primary_response, supporting_reference, evidence_reference, contextual_mention, independent_response, generic_promise, or uncertain.
- substantive_level: none, partial, substantive, or uncertain.
- substantive_level and independent_complete_response are separate dimensions. A supporting reference may contain substantive information and still be non-independent.
- independent_complete_response=yes is a strict gate and is allowed only when BOTH are clearly true: (1) direct response intent -- the location itself is deliberately answering this Requirement; and (2) standalone satisfaction -- if every planned primary response were removed, this location alone would still completely and independently satisfy the Requirement in a reviewable form.
- Repeated keywords, locations, numbers, commitments, or technically substantive content do NOT by themselves prove direct response intent or standalone satisfaction.
- A labelled field embedded inside a broader implementation schedule, workflow, acceptance arrangement, or delivery-plan paragraph is not automatically a standalone answer. Evaluate the dominant section/paragraph purpose, not the presence of a colon-labelled value alone.
- References, evidence indexes, contextual mentions and generic promises must use independent_complete_response=no. An independent_response must use yes; if completeness cannot be established, use uncertain rather than independent_response.

REQUIREMENT LOCATION / CONTENT OWNERSHIP CALIBRATION:
- A recorded primary location is the intended location for a complete, independently reviewable response. It is NOT an exclusive allowed location.
- An actual location outside the recorded plan is only a candidate trigger. It is neutral evidence and MUST NOT create a negative prior.
- Supporting references, incidental/contextual mentions, and evidence/material indexes are acceptable when they do not act as another complete independent response.
- contextual_mention means the Requirement naturally appears as an implementation prerequisite, process condition, acceptance/delivery condition, scenario description, or technical context; even an identical value or location remains contextual when the section is not independently answering the Requirement.
- supporting_reference means the primary response exists and another location summarizes, cross-references, briefly confirms, or repeats a key conclusion for continuity without becoming a standalone answer. It may be substantive_level=partial or substantive, but independent_complete_response must be no.
- A dedicated evidence or index subsection whose responsibility matches the requirement is not a wrong subsection merely because a broader primary section was recorded.
- Section-declared requirement IDs are auxiliary permission/coverage metadata. "Allowed" does not mean "must be primary"; "not primary" does not mean "forbidden".

MANDATORY DECISION ORDER:
1. Locate the recorded primary/table response context and decide whether a substantive primary response actually exists and is sufficient.
2. Classify each other actual location as one of: complete independent response, supporting reference, incidental context, evidence/index support, or generic/empty statement.
3. Check whether the section responsibility is semantically appropriate for that use.
4. Only then decide content ownership. Never use `outside recorded plan -> misplaced`.

OBSERVATION GUIDANCE:
- Use expected_response_form_context. Do not apply a universal narrative-prose adequacy test.
- narrative_response normally needs substantive prose; a heading, generic compliance statement, or unplanned bare attachment pointer is not adequate.
- evidence_response, attachment_reference, or index_response is assessed by whether a relevant evidence/material response responsibility and usable index/reference structure exists. It does not require long narrative prose.
- For evidence/material Requirements, a relevant planned index that identifies required certificate/report/material categories may be structurally adequate even while filenames, certificates, placeholders, or actual uploads remain pending. Adequate here NEVER confirms authenticity, validity, ownership, availability, or upload; those are FCA/Material/Human Review concerns.
- Planning may record a broader parent index as primary while the actual document implements that responsibility through a dedicated child/sibling evidence subsection (for example product-test evidence or manufacturer-qualification evidence). When that dedicated subsection directly matches the evidence/material Requirement and contains a usable material index/placeholder structure, treat the planned evidence/index response responsibility as present and primary_response_status=adequate; classify that actual subsection as evidence_reference/no, not as a substitute narrative response. Do not require the broader parent section itself to repeat every child index.
- Apply planned_response_responsibility_resolution literally for index/evidence forms: when specialized_actual_evidence_sections identifies a relevant declared subsection and its visible excerpts establish a usable index response, set primary_response_status=adequate even if the broader recorded parent excerpt does not repeat it. The specialized subsection remains evidence_reference/substantive/no because it realizes the same planned evidence responsibility; it is not misplaced_content and not an independent duplicate.
- For table_response, judge the supplied table content when it is visible. When actual cells needed for adequacy are absent and only a prose summary is visible, use evidence_sufficiency=insufficient and primary_response_status=uncertain, never missing merely because Markdown omitted the cells.
- primary sufficient + outside is reference/context/evidence index -> describe the primary as adequate and the outside role precisely; Runtime will decide.
- primary sufficient + outside repeats a complete, independently reviewable response without necessary new information -> mark outside independent_response/substantive/yes.
- A formal service-commitment or commitment-summary section is NOT automatically a harmless reference. If it restates the same operative promise, value, scope, and obligation in independently binding language (for example the same 7x24 response and 24-hour arrival commitments), classify redundant_response even when the prose is shorter or presented as a numbered summary.
- Use acceptable_reference for a summary only when it is genuinely navigational, expressly points to the primary response, or merely gives non-operative context. Do not use acceptable_reference merely because the second response is shorter.
- primary missing or non-substantive + outside contains a complete substantive response -> mark primary missing/inadequate and outside independent_response/substantive/yes.
- primary missing or non-substantive + outside only says "provide as required", contains a placeholder, or makes a generic promise -> mark generic_promise, none/partial, no.
- insufficient context -> evidence_sufficiency=insufficient and use uncertain observation fields where needed.

INDEPENDENCE SELF-CHECK FOR EVERY NON-PRIMARY LOCATION:
1. What is this location's dominant intent: answer the Requirement, provide process/context, cross-reference, or index evidence?
2. If the planned primary response vanished, would this location alone fully satisfy the Requirement in its expected response form?
Only a clear answer_response + yes combination may be independent_response/substantive/yes. Otherwise choose contextual_mention, supporting_reference, evidence_reference, generic_promise, or uncertain.

Do not recommend moving evidence-index material merely because its requirement was planned under a broader technical-material section. Do not require an explicit transition sentence or exact heading match. Base the decision on the supplied excerpts and section responsibilities.
"""
        elif pass_type == STABLE_FACT_PASS_TYPE:
            requirement_calibration = """
CROSS-SECTION STABLE FACT RULES:
- The runtime has already resolved high-confidence equal values, explicit context differences, and directly comparable conflicts.
- You receive only an ambiguous entity/attribute group. Decide stable_fact_conflict only when the locations make the same entity, attribute, scope, phase, condition, and unit clearly comparable.
- Use context_different when different phases, fault levels, product entities, conditional scopes, or compatible thresholds explain the values.
- Do not infer a product, company, material, certificate, parameter, or commitment hidden behind a placeholder.
- For an issue, cite every supplied location that participates in the conflict and preserve every supplied Requirement ID.
"""
        elif pass_type == "adjacent_chapter_coherence":
            requirement_calibration = """
ADJACENT CHAPTER LOCATION RULES:
- For decision=coherent / verdict=acceptable, return location_ids=[]; no evidence anchor is required for an acceptable boundary.
- For an issue or uncertain boundary, copy only exact location_id values from allowed_locations of that same audit_unit.
- Never construct a LOC-* value from a paragraph_id, never replace a PAR-* prefix with LOC-*, and never reuse a location from another audit_unit in the batch.
"""
        authority = (
            "MODEL OUTPUT IS OBSERVATION ONLY; RUNTIME DETERMINISTIC POLICY OWNS THE FORMAL REQUIREMENT DECISION."
            if pass_type == "requirement_location"
            else f"MANDATORY DECISION TO VERDICT MAPPING: {decision_contract}"
        )
        return f"""You are a fulltext audit adjudicator, not a bid-writing model.
PASS_TYPE: {pass_type}
{authority}
{requirement_calibration}
Return only the supplied JSON schema. Return exactly one result for every input audit_unit_id.
You may compare, classify, identify conflict/repetition/location/coherence concerns, or request human review.
Never add facts, rewrite prose, fill placeholders, select brands/models/manufacturers/personnel, infer offered values from tender minima, or treat candidates as confirmed.
Use only supplied audit units and allowed IDs. location_ids, requirement_ids, score_item_ids and placeholder_ids must be subsets of each unit's allowed lists. Never invent an ID or excerpt.
First determine whether the supplied context is sufficient. For Requirement observations use evidence_sufficiency=insufficient when it is not.
Candidates are not issues. A verdict=issue is still only a semantic finding and not a formal FAI.
OUTPUT AUTHORITY RULES:
- verdict=issue MUST set requires_human_review=true and must cite at least one allowed location_id.
- verdict=acceptable MUST set suggested_severity=null and suggested_release_blocking=false.
- verdict=uncertain MUST set suggested_release_blocking=false; uncertainty never confirms a fact or an issue.
Do not output chain-of-thought; provide a concise rationale based only on visible evidence."""

    @staticmethod
    def _validate_non_requirement_result(
        result: dict[str, Any], *, unit: SemanticAuditUnit
    ) -> None:
        required = {
            "audit_unit_id",
            "decision",
            "verdict",
            "confidence",
            "subject",
            "location_ids",
            "requirement_ids",
            "score_item_ids",
            "placeholder_ids",
            "summary",
            "rationale",
            "details",
            "suggested_severity",
            "suggested_release_blocking",
            "recommended_action",
            "requires_human_review",
        }
        missing = sorted(required - set(result))
        if missing:
            raise TechnicalFulltextSemanticAuditError(
                f"non-Requirement observation is missing fields: {missing}"
            )
        if result["audit_unit_id"] != unit.audit_unit_id:
            raise TechnicalFulltextSemanticAuditError(
                "non-Requirement observation audit_unit_id mismatch"
            )
        decision = result["decision"]
        allowed_decisions = DECISION_VERDICTS[unit.pass_type]
        if decision not in allowed_decisions:
            raise TechnicalFulltextSemanticAuditError(
                f"decision {decision!r} is invalid for pass {unit.pass_type}"
            )
        if result["verdict"] != allowed_decisions[decision]:
            raise TechnicalFulltextSemanticAuditError(
                "runtime verdict does not match the pass decision policy: "
                f"decision={decision}, expected={allowed_decisions[decision]}, "
                f"actual={result['verdict']}"
            )
        allowed = {
            "location_ids": {item["location_id"] for item in unit.locations},
            "requirement_ids": set(unit.requirement_ids),
            "score_item_ids": set(unit.score_item_ids),
            "placeholder_ids": set(unit.placeholder_ids),
        }
        for field, values in allowed.items():
            returned_values = result.get(field)
            if not isinstance(returned_values, list):
                raise TechnicalFulltextSemanticAuditError(
                    f"non-Requirement observation {field} must be an array"
                )
            returned = set(returned_values)
            if not returned.issubset(values):
                raise TechnicalFulltextSemanticAuditError(
                    f"model returned unknown {field}: {sorted(returned - values)}"
                )
        if result["verdict"] == "issue" and not result["location_ids"]:
            raise TechnicalFulltextSemanticAuditError(
                "issue verdict must identify at least one allowed location"
            )
        if result["verdict"] == "acceptable" and (
            result["suggested_severity"] is not None
            or result["suggested_release_blocking"]
        ):
            raise TechnicalFulltextSemanticAuditError(
                "acceptable verdict cannot suggest severity or release blocking"
            )
        if result["verdict"] == "uncertain" and result["suggested_release_blocking"]:
            raise TechnicalFulltextSemanticAuditError(
                "uncertain verdict cannot independently suggest release blocking"
            )
        if result["verdict"] == "issue" and not result["requires_human_review"]:
            raise TechnicalFulltextSemanticAuditError(
                "semantic issue verdict must remain subject to human review"
            )
        if unit.pass_type == "placeholder_alias" and set(
            result["placeholder_ids"]
        ) != set(unit.placeholder_ids):
            raise TechnicalFulltextSemanticAuditError(
                "placeholder alias result must bind all candidate placeholders"
            )
        if unit.pass_type == "score_distribution" and set(
            result["score_item_ids"]
        ) != set(unit.score_item_ids):
            raise TechnicalFulltextSemanticAuditError(
                "score result must bind its supplied score item"
            )

    @staticmethod
    def _validate_batch_output(payload: dict[str, Any], *, batch_id: str, pass_type: str, units: list[SemanticAuditUnit], schema: dict[str, Any]) -> None:
        TechnicalFulltextSemanticAuditor._validate_unit_identity_list(
            units, pass_type=pass_type
        )
        errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path))
        if errors:
            detail = "; ".join(f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}" for error in errors[:5])
            raise TechnicalFulltextSemanticAuditError(f"model schema validation failed: {detail}")
        if payload.get("batch_id") != batch_id or payload.get("pass_type") != pass_type:
            raise TechnicalFulltextSemanticAuditError("model batch identity mismatch")
        unit_map = {unit.audit_unit_id: unit for unit in units}
        results = payload.get("results") or []
        result_ids = [item.get("audit_unit_id") for item in results]
        result_counts = Counter(result_ids)
        duplicate_ids = sorted(
            str(unit_id) for unit_id, count in result_counts.items() if count > 1
        )
        missing_ids = sorted(set(unit_map) - set(result_ids))
        unexpected_ids = sorted(
            str(unit_id) for unit_id in set(result_ids) - set(unit_map)
        )
        if (
            len(result_ids) != len(units)
            or duplicate_ids
            or missing_ids
            or unexpected_ids
        ):
            raise TechnicalFulltextSemanticAuditError(
                "model must return each supplied audit_unit_id exactly once: "
                f"missing={missing_ids}, duplicate={duplicate_ids}, "
                f"unexpected={unexpected_ids}"
            )
        for result in results:
            unit = unit_map[result["audit_unit_id"]]
            if pass_type == "requirement_location":
                if set(result["requirement_ids"]) != set(unit.requirement_ids):
                    raise TechnicalFulltextSemanticAuditError(
                        "requirement observation must bind its supplied requirement"
                    )
                continue
            TechnicalFulltextSemanticAuditor._validate_non_requirement_result(
                result, unit=unit
            )

    @staticmethod
    def _validate_unit_identity_list(
        units: list[SemanticAuditUnit], *, pass_type: str
    ) -> None:
        unit_ids = [unit.audit_unit_id for unit in units]
        missing_indexes = [
            index
            for index, unit_id in enumerate(unit_ids)
            if not isinstance(unit_id, str) or not unit_id.strip()
        ]
        duplicate_ids = sorted(
            unit_id
            for unit_id, count in Counter(unit_ids).items()
            if isinstance(unit_id, str) and unit_id and count > 1
        )
        mismatched_pass_indexes = [
            index
            for index, unit in enumerate(units)
            if unit.pass_type != pass_type
        ]
        if missing_indexes or duplicate_ids or mismatched_pass_indexes:
            raise TechnicalFulltextSemanticAuditError(
                "semantic audit unit identity preflight failed: "
                f"pass_type={pass_type}, missing_indexes={missing_indexes}, "
                f"duplicate={duplicate_ids}, "
                f"pass_mismatch_indexes={mismatched_pass_indexes}"
            )

    @classmethod
    def _validate_unit_identities(
        cls, units_by_pass: dict[str, list[SemanticAuditUnit]]
    ) -> None:
        for pass_type in PASS_ORDER:
            cls._validate_unit_identity_list(
                units_by_pass.get(pass_type) or [], pass_type=pass_type
            )

    @staticmethod
    def _to_finding(*, pass_type: str, unit: SemanticAuditUnit, result: dict[str, Any]) -> dict[str, Any]:
        location_map = {item["location_id"]: item for item in unit.locations}
        locations = []
        for location_id in result["location_ids"]:
            source = location_map[location_id]
            locations.append({key: source[key] for key in ("chapter_id", "section_id", "paragraph_id", "paragraph_index", "excerpt", "text_sha256")})
        audit_type, code = DECISION_CODES.get(result["decision"], (pass_type, "semantic_adjudication"))
        identity = {
            "pass_type": pass_type,
            "source_candidate_ids": unit.source_candidate_ids,
            "audit_unit_id": unit.audit_unit_id,
            "verdict": result["verdict"],
            "subject": result["subject"]["canonical_key"],
            "locations": sorted(
                (
                    item["chapter_id"],
                    item["section_id"],
                    item["paragraph_id"],
                    item["text_sha256"],
                )
                for item in locations
            ),
        }
        finding = {
            "finding_id": _stable_id("FSF-", identity),
            "source_candidate_ids": unit.source_candidate_ids,
            "audit_unit_id": unit.audit_unit_id,
            "pass_type": pass_type,
            "audit_type": audit_type,
            "code": code,
            "decision": result["decision"],
            "verdict": result["verdict"],
            "confidence": result["confidence"],
            "subject": deepcopy(result["subject"]),
            "locations": locations,
            "requirement_ids": result["requirement_ids"],
            "score_item_ids": result["score_item_ids"],
            "placeholder_ids": result["placeholder_ids"],
            "summary": result["summary"],
            "rationale": result["rationale"],
            "details": deepcopy(result["details"]),
            "suggested_severity": result["suggested_severity"],
            "suggested_release_blocking": result["suggested_release_blocking"],
            "recommended_action": deepcopy(result["recommended_action"]),
            "requires_human_review": result["requires_human_review"],
        }
        if pass_type == "requirement_location":
            finding["semantic_observation"] = deepcopy(result["semantic_observation"])
            finding["requirement_decision_provenance"] = deepcopy(
                result["requirement_decision_provenance"]
            )
        return finding

    @staticmethod
    def _aggregate_usage(calls: list[ModelCallResult]) -> dict[str, int]:
        totals: Counter[str] = Counter()
        for call in calls:
            for key, value in (call.usage or {}).items():
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[key] += value
        return dict(sorted(totals.items()))

    @staticmethod
    def _persist_pair(*, audit_path: Path, audit_payload: dict[str, Any], markdown_path: Path, markdown: str) -> None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        temp_json = audit_path.with_name(audit_path.name + ".semantic.tmp")
        temp_markdown = markdown_path.with_name(markdown_path.name + ".semantic.tmp")
        original_json = audit_path.read_bytes() if audit_path.exists() else None
        original_markdown = markdown_path.read_bytes() if markdown_path.exists() else None
        try:
            atomic_write_json(temp_json, audit_payload, required_fields=("schema_version", "semantic_audit", "semantic_findings"))
            atomic_write_markdown(temp_markdown, markdown, required_text=AUDIT_MARKDOWN_NOTICE)
            os.replace(temp_json, audit_path)
            os.replace(temp_markdown, markdown_path)
        except Exception as exc:
            if original_json is not None:
                audit_path.write_bytes(original_json)
            if original_markdown is not None:
                markdown_path.write_bytes(original_markdown)
            temp_json.unlink(missing_ok=True)
            temp_markdown.unlink(missing_ok=True)
            raise TechnicalFulltextSemanticAuditError(f"semantic artifact persistence failed: {type(exc).__name__}: {exc}") from exc
