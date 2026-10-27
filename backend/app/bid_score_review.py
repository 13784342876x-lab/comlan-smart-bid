from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from app.technical_fulltext_composer import atomic_write_json, sha256_file, stable_json_sha256


DECISION_INPUT_SCHEMA_VERSION = "bid_score_review_decision_input_v1"
DECISION_STORE_SCHEMA_VERSION = "bid_score_review_store_v1"
SCORE_PARSE_SCHEMA_VERSION = "score_table_parse_v2"
TECHNICAL_TEMPLATE_SELECTION = "technical_template_selection"
COMPONENT_WEIGHTS_CONFIRMATION = "component_weights_confirmation"
DECISION_TYPES = {TECHNICAL_TEMPLATE_SELECTION, COMPONENT_WEIGHTS_CONFIRMATION}
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


class BidScoreReviewError(RuntimeError):
    """Raised when a run-scoped score review decision is invalid or stale."""


class BidScoreReviewService:
    """Append-only human-decision overlay for a bid run's immutable score parse."""

    def __init__(self, *, output_dir: str | Path = "outputs") -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()

    def inspect(self, run_id: str) -> dict[str, Any]:
        context = self._load_context(run_id)
        overlay = self._materialize_overlay(context)
        machine = context["machine_score_parse"]
        store = context["store"]
        return {
            "run_id": context["run_id"],
            "score_file_id": context["score_file_id"],
            "base_score_parse_sha256": context["base_score_parse_sha256"],
            "machine_score_parse_schema_version": machine.get("schema_version"),
            "current_score_review_gate": overlay["effective_score_parse"].get("review_gate"),
            "machine_score_review_gate": machine.get("review_gate"),
            "technical_candidates": [
                {
                    "candidate_id": item.get("candidate_id"),
                    "heading": item.get("heading"),
                    "table_index": item.get("table_index"),
                    "headers": deepcopy(item.get("headers") or []),
                    "evidence": {
                        "source_file_id": context["score_file_id"],
                        "source_location": f"table:{item.get('table_index')}",
                        "selection_signals": deepcopy(item.get("selection_signals") or []),
                        "confidence": item.get("confidence"),
                        "selection_score": item.get("selection_score"),
                    },
                }
                for item in machine.get("technical_score_table_candidates") or []
            ],
            "machine_weights": deepcopy(machine.get("weights") or {}),
            "unresolved_fields": overlay["unresolved_fields"],
            "human_decisions": deepcopy(overlay["active_decisions"]),
            "stale_decision_ids": overlay["stale_decision_ids"],
            "decision_artifact_path": str(context["store_path"]),
            "decision_artifact_exists": context["store_path"].is_file(),
            "decision_artifact_sha256": (
                sha256_file(context["store_path"]) if context["store_path"].is_file() else None
            ),
            "application_status": overlay["application_status"],
            "applied_decision_ids": overlay["applied_decision_ids"],
        }

    def apply(
        self,
        run_id: str,
        input_payload: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        context = self._load_context(run_id)
        incoming = self._validate_input_envelope(input_payload, context)
        store = deepcopy(context["store"])
        existing_ids = {str(item.get("decision_id") or "") for item in store["decisions"]}
        superseded_ids = list(store.get("superseded_decision_ids") or [])
        active = self._active_decisions(store)
        active_by_type = {item["decision_type"]: item for item in active}
        applied: list[str] = []

        for raw in incoming:
            decision = self._validate_decision(raw, context)
            decision_id = decision["decision_id"]
            if decision_id in existing_ids:
                raise BidScoreReviewError(f"decision_id already exists: {decision_id}")
            previous = active_by_type.get(decision["decision_type"])
            supersedes = decision.get("supersedes")
            if previous and not supersedes:
                raise BidScoreReviewError(
                    f"an active {decision['decision_type']} decision already exists; provide supersedes"
                )
            if supersedes:
                if supersedes not in {item.get("decision_id") for item in store["decisions"]}:
                    raise BidScoreReviewError(f"supersedes references unknown decision_id: {supersedes}")
                if supersedes in superseded_ids:
                    raise BidScoreReviewError(f"supersedes references an inactive decision: {supersedes}")
                previous_record = next(item for item in store["decisions"] if item.get("decision_id") == supersedes)
                if previous_record.get("decision_type") != decision["decision_type"]:
                    raise BidScoreReviewError("supersedes must reference the same decision_type")
                superseded_ids.append(supersedes)
            store["decisions"].append(decision)
            existing_ids.add(decision_id)
            active_by_type[decision["decision_type"]] = decision
            applied.append(decision_id)

        store["superseded_decision_ids"] = list(dict.fromkeys(superseded_ids))
        if not store.get("created_at"):
            store["created_at"] = _utc_now()
        store["updated_at"] = _utc_now()
        self._validate_store(store, context["run_id"])
        preview_context = {**context, "store": store}
        preview = self._materialize_overlay(preview_context)
        if dry_run:
            return {
                "run_id": context["run_id"],
                "dry_run": True,
                "validation_passed": True,
                "would_apply_decision_ids": applied,
                "decision_artifact_path": str(context["store_path"]),
                "application_status": preview["application_status"],
                "unresolved_fields": preview["unresolved_fields"],
            }

        artifact_sha = atomic_write_json(
            context["store_path"],
            store,
            required_fields=("schema_version", "run_id", "decisions", "superseded_decision_ids"),
        )
        return {
            "run_id": context["run_id"],
            "dry_run": False,
            "validation_passed": True,
            "applied_decision_ids": applied,
            "decision_artifact_path": str(context["store_path"]),
            "decision_artifact_sha256": artifact_sha,
            "application_status": preview["application_status"],
            "unresolved_fields": preview["unresolved_fields"],
        }

    def effective_score_parse(self, run_id: str) -> dict[str, Any]:
        context = self._load_context(run_id)
        overlay = self._materialize_overlay(context)
        return {
            "effective_score_parse": overlay["effective_score_parse"],
            "base_score_parse_sha256": context["base_score_parse_sha256"],
            "decision_artifact_path": str(context["store_path"]),
            "decision_artifact_sha256": (
                sha256_file(context["store_path"]) if context["store_path"].is_file() else None
            ),
            "applied_decision_ids": overlay["applied_decision_ids"],
            "application_status": overlay["application_status"],
            "unresolved_fields": overlay["unresolved_fields"],
            "stale_decision_ids": overlay["stale_decision_ids"],
        }

    def _load_context(self, run_id: str) -> dict[str, Any]:
        safe_run_id = self._validate_run_id(run_id)
        run_dir = (self.output_dir / "bid_runs" / safe_run_id).resolve()
        state_path = run_dir / "pipeline_state.json"
        if not state_path.is_file():
            raise FileNotFoundError(f"bid pipeline state not found: {state_path}")
        state = _load_json_object(state_path, "bid pipeline state")
        if state.get("run_id") != safe_run_id:
            raise BidScoreReviewError("run_id does not match pipeline state")
        metadata_path = Path(state.get("routing", {}).get("source_metadata_path") or "")
        if not metadata_path.is_file():
            raise FileNotFoundError(f"run source metadata not found: {metadata_path}")
        metadata = _load_json_object(metadata_path, "run source metadata")
        score_file_id = str((state.get("source_manifest") or {}).get("score_file_id") or "")
        if not score_file_id:
            raise BidScoreReviewError("score source has not been selected for this run")
        score_record = next(
            (item for item in metadata.get("files") or [] if str(item.get("id") or "") == score_file_id),
            None,
        )
        if not isinstance(score_record, dict):
            raise BidScoreReviewError(f"score_file_id is absent from run source metadata: {score_file_id}")
        machine = score_record.get("score_table_parse")
        if not isinstance(machine, dict) or machine.get("schema_version") != SCORE_PARSE_SCHEMA_VERSION:
            raise BidScoreReviewError(
                "score_table_parse_v2 is not available for this run; run resume-bid once to materialize the machine score parse"
            )
        store_path = run_dir / "score_review" / "score_review_decisions.json"
        store = self._load_store(store_path, safe_run_id)
        return {
            "run_id": safe_run_id,
            "state": state,
            "machine_score_parse": deepcopy(machine),
            "score_file_id": score_file_id,
            "base_score_parse_sha256": stable_json_sha256(machine),
            "store_path": store_path,
            "store": store,
        }

    def _load_store(self, path: Path, run_id: str) -> dict[str, Any]:
        if not path.is_file():
            return {
                "schema_version": DECISION_STORE_SCHEMA_VERSION,
                "run_id": run_id,
                "decisions": [],
                "superseded_decision_ids": [],
                "created_at": None,
                "updated_at": None,
            }
        store = _load_json_object(path, "bid score review decision store")
        self._validate_store(store, run_id)
        return store

    def _validate_input_envelope(self, payload: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise BidScoreReviewError("decision input must be a JSON object")
        if payload.get("schema_version") != DECISION_INPUT_SCHEMA_VERSION:
            raise BidScoreReviewError(
                f"unsupported decision input schema_version: {payload.get('schema_version')}"
            )
        if payload.get("run_id") != context["run_id"]:
            raise BidScoreReviewError("decision input run_id does not match current run")
        # The CLI accepts either an envelope containing a batch, or a single
        # decision JSON. The latter is the natural artifact to hand to a
        # reviewer and still carries the same version/run binding fields.
        decisions = payload.get("decisions")
        if decisions is None and payload.get("decision_type"):
            decisions = [payload]
        if not isinstance(decisions, list) or not decisions:
            raise BidScoreReviewError("decision input must contain at least one decision")
        if any(not isinstance(item, dict) for item in decisions):
            raise BidScoreReviewError("each decision input must be a JSON object")
        return [deepcopy(item) for item in decisions]

    def _validate_decision(self, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        decision = deepcopy(raw)
        required = (
            "decision_id",
            "run_id",
            "decision_type",
            "reviewer",
            "decided_at",
            "reason",
            "score_file_id",
            "base_score_parse_sha256",
        )
        for field in required:
            if not str(decision.get(field) or "").strip():
                raise BidScoreReviewError(f"decision missing required field: {field}")
        if decision["run_id"] != context["run_id"]:
            raise BidScoreReviewError("decision run_id does not match current run")
        if decision["score_file_id"] != context["score_file_id"]:
            raise BidScoreReviewError("decision score_file_id does not match current score source")
        if decision["base_score_parse_sha256"] != context["base_score_parse_sha256"]:
            raise BidScoreReviewError(
                "stale decision: base_score_parse_sha256 does not match the current score_table_parse_v2"
            )
        if decision["decision_type"] not in DECISION_TYPES:
            raise BidScoreReviewError(f"unsupported decision_type: {decision['decision_type']}")
        self._validate_timestamp(str(decision["decided_at"]))
        if not isinstance(decision.get("evidence") or [], list):
            raise BidScoreReviewError("decision evidence must be a list")
        if not isinstance(decision.get("candidate_refs") or [], list):
            raise BidScoreReviewError("decision candidate_refs must be a list")
        decision.setdefault("supersedes", None)
        if decision.get("supersedes") is not None and not str(decision["supersedes"]).strip():
            raise BidScoreReviewError("supersedes must be a decision_id or null")

        if decision["decision_type"] == TECHNICAL_TEMPLATE_SELECTION:
            self._validate_technical_selection(decision, context)
        else:
            self._validate_component_weights(decision)
        return decision

    def _validate_technical_selection(self, decision: dict[str, Any], context: dict[str, Any]) -> None:
        candidate_id = str(decision.get("selected_technical_candidate_id") or "")
        candidates = {
            str(item.get("candidate_id") or ""): item
            for item in context["machine_score_parse"].get("technical_score_table_candidates") or []
        }
        if candidate_id not in candidates:
            raise BidScoreReviewError(f"selected_technical_candidate_id is not a current technical candidate: {candidate_id}")
        candidate_refs = decision.get("candidate_refs") or []
        if not any(str(item.get("candidate_id") or "") == candidate_id for item in candidate_refs if isinstance(item, dict)):
            raise BidScoreReviewError("technical template selection must include a candidate_ref for the selected candidate")

    @staticmethod
    def _validate_component_weights(decision: dict[str, Any]) -> None:
        weights = decision.get("weights")
        if weights is not None and not isinstance(weights, dict):
            raise BidScoreReviewError("weights must be an object when provided")
        submitted_weight_fields = set(decision) | set(weights or {})
        if any(str(key).upper() in {"W1", "W2", "C"} for key in submitted_weight_fields):
            raise BidScoreReviewError("W1/W2/C are price-method parameters and cannot be submitted as component weights")
        values: dict[str, Any] = {}
        for component in ("business", "technical", "price"):
            direct = decision.get(f"{component}_weight")
            nested = (weights or {}).get(component)
            if direct is not None and nested is not None and direct != nested:
                raise BidScoreReviewError(f"conflicting {component} weight values")
            values[component] = direct if direct is not None else nested
        if any(value is None for value in values.values()):
            raise BidScoreReviewError("business_weight, technical_weight and price_weight are all required")
        parsed: dict[str, float] = {}
        for component, value in values.items():
            if isinstance(value, bool):
                raise BidScoreReviewError(f"{component}_weight must be numeric")
            try:
                parsed[component] = float(value)
            except (TypeError, ValueError) as exc:
                raise BidScoreReviewError(f"{component}_weight must be numeric") from exc
            if parsed[component] < 0:
                raise BidScoreReviewError(f"{component}_weight must be >= 0")
        if abs(sum(parsed.values()) - 100.0) > 1e-9:
            raise BidScoreReviewError("business_weight + technical_weight + price_weight must equal 100")
        if not decision.get("evidence"):
            raise BidScoreReviewError("component weight confirmation must include evidence")
        decision["business_weight"] = _compact_number(parsed["business"])
        decision["technical_weight"] = _compact_number(parsed["technical"])
        decision["price_weight"] = _compact_number(parsed["price"])

    def _materialize_overlay(self, context: dict[str, Any]) -> dict[str, Any]:
        machine = deepcopy(context["machine_score_parse"])
        current_sha = context["base_score_parse_sha256"]
        active = self._active_decisions(context["store"])
        applicable = [item for item in active if item.get("base_score_parse_sha256") == current_sha]
        stale = [item["decision_id"] for item in active if item.get("base_score_parse_sha256") != current_sha]
        by_type = {item["decision_type"]: item for item in applicable}
        applied: list[str] = []

        selection = by_type.get(TECHNICAL_TEMPLATE_SELECTION)
        if selection:
            candidate_id = selection["selected_technical_candidate_id"]
            candidates = deepcopy(machine.get("technical_score_table_candidates") or [])
            selected = next((item for item in candidates if item.get("candidate_id") == candidate_id), None)
            if selected is not None:
                for candidate in candidates:
                    candidate["selected"] = candidate.get("candidate_id") == candidate_id
                machine["technical_score_table_candidates"] = candidates
                machine["selected_technical_candidate_id"] = candidate_id
                machine["matched_technical_template"] = selected.get("heading")
                machine["technical_template_resolution_status"] = "human_confirmed"
                machine["technical_template_unresolved"] = False
                machine["technical_template_selection_reason"] = "human score review decision"
                machine["technical_template_evidence"] = deepcopy(selection.get("evidence") or [])
                machine["technical_score_items"] = deepcopy(selected.get("score_items") or [])
                applied.append(selection["decision_id"])

        weights_decision = by_type.get(COMPONENT_WEIGHTS_CONFIRMATION)
        if weights_decision:
            weights = {
                "business": weights_decision["business_weight"],
                "technical": weights_decision["technical_weight"],
                "price": weights_decision["price_weight"],
            }
            machine["weights"] = weights
            machine["component_weights_unresolved"] = False
            price_rule = deepcopy(machine.get("price_score_rule") or {})
            if price_rule:
                price_rule["score"] = weights["price"]
                price_rule["score_range"] = {"min": 0, "max": weights["price"]}
                machine["price_score_rule"] = price_rule
            scale = deepcopy(machine.get("score_scale") or {})
            scale["weights"] = weights
            if price_rule:
                scale["price_items_max_sum"] = weights["price"]
            machine["score_scale"] = scale
            applied.append(weights_decision["decision_id"])

        machine["score_items"] = _combine_score_items(machine)
        machine["generation_tasks"] = [
            item.get("generation_instruction", "")
            for item in machine["score_items"]
            if item.get("generation_instruction") and not item.get("is_aggregate")
        ]
        machine["disqualification_rules"] = [
            item.get("score_rule", "")
            for item in machine["score_items"]
            if item.get("is_disqualifying") and not item.get("is_aggregate")
        ]

        unresolved_fields: list[str] = []
        if machine.get("technical_template_unresolved"):
            unresolved_fields.append("technical_template_selection")
        if machine.get("component_weights_unresolved"):
            unresolved_fields.append("component_weights_confirmation")
        if machine.get("unsupported_score_format"):
            unresolved_fields.append("unsupported_score_format")
        if machine.get("unsupported_score_format"):
            machine["review_gate"] = "unsupported_score_format"
            machine["status"] = "needs_review"
        elif machine.get("technical_template_unresolved"):
            machine["review_gate"] = "technical_template_unresolved"
            machine["status"] = "needs_review"
        elif machine.get("component_weights_unresolved"):
            machine["review_gate"] = "component_weights_unresolved"
            machine["status"] = "needs_review"
        else:
            machine["review_gate"] = None
            machine["status"] = "success"
            machine["parsed"] = True
            machine["message"] = "评分规则、人工确认的技术模板和 component weights 已确认。"

        application_status = "resolved" if not unresolved_fields else "pending_human_review"
        if stale:
            application_status = "stale_decisions_ignored"
        return {
            "effective_score_parse": machine,
            "active_decisions": applicable,
            "applied_decision_ids": applied,
            "stale_decision_ids": stale,
            "unresolved_fields": unresolved_fields,
            "application_status": application_status,
        }

    @staticmethod
    def _active_decisions(store: dict[str, Any]) -> list[dict[str, Any]]:
        superseded = {str(item) for item in store.get("superseded_decision_ids") or []}
        active = [
            deepcopy(item)
            for item in store.get("decisions") or []
            if str(item.get("decision_id") or "") not in superseded
        ]
        return sorted(active, key=lambda item: str(item.get("decided_at") or ""))

    @staticmethod
    def _validate_store(store: dict[str, Any], run_id: str) -> None:
        if store.get("schema_version") != DECISION_STORE_SCHEMA_VERSION:
            raise BidScoreReviewError("unsupported bid score review decision store schema")
        if store.get("run_id") != run_id:
            raise BidScoreReviewError("decision store run_id mismatch")
        if not isinstance(store.get("decisions"), list):
            raise BidScoreReviewError("decision store decisions must be a list")
        if not isinstance(store.get("superseded_decision_ids"), list):
            raise BidScoreReviewError("decision store superseded_decision_ids must be a list")
        ids = [str(item.get("decision_id") or "") for item in store["decisions"] if isinstance(item, dict)]
        if len(ids) != len(set(ids)) or any(not item for item in ids):
            raise BidScoreReviewError("decision store contains missing or duplicate decision_id")

    @staticmethod
    def _validate_timestamp(value: str) -> None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BidScoreReviewError("decided_at must be an ISO 8601 datetime") from exc
        if parsed.tzinfo is None:
            raise BidScoreReviewError("decided_at must include a timezone")

    @staticmethod
    def _validate_run_id(run_id: str) -> str:
        value = str(run_id or "").strip()
        if not RUN_ID_PATTERN.fullmatch(value):
            raise BidScoreReviewError("invalid run_id")
        return value


def _combine_score_items(score_parse: dict[str, Any]) -> list[dict[str, Any]]:
    items = list(score_parse.get("business_score_items") or []) + list(score_parse.get("technical_score_items") or [])
    price_rule = score_parse.get("price_score_rule") or {}
    if price_rule:
        items.append(price_rule)
    return items


def _compact_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _utc_now() -> str:
    return datetime.now().astimezone().isoformat()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BidScoreReviewError(f"{label} is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BidScoreReviewError(f"{label} must be a JSON object")
    return payload
