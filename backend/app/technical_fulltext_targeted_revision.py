from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.stage9_input_handoff import (
    Stage9InputResolver,
    Stage9InputSource,
    scoped_task_contexts,
)
from app.technical_fulltext_composer import (
    atomic_write_json,
    atomic_write_markdown,
    load_json_object,
)
from app.technical_fulltext_revision_materializer import (
    materialize_single_section_revision,
)
from app.technical_writing_agent import (
    TechnicalWritingAgentInput,
    build_technical_writing_agent,
    render_technical_chapter_markdown,
)


class TechnicalFulltextTargetedRevisionError(RuntimeError):
    pass


def execute_targeted_section_revision(
    *,
    source_fulltext_path: str | Path,
    stage12_handoff_path: str | Path,
    candidate_source_type: str,
    candidate_source_id: str,
    stage9_source: Stage9InputSource,
    draft_units_path: str | Path | None,
    output_dir: str | Path,
    output_key: str,
    llm: Any = None,
    metadata_store: Any = None,
) -> dict[str, Any]:
    handoff_path = Path(stage12_handoff_path).expanduser().resolve()
    handoff = load_json_object(handoff_path, label="Stage12 handoff")
    identity = handoff.get("artifact_identity")
    if (
        handoff.get("schema_version") != "technical_fulltext_stage13_handoff_v1"
        or not isinstance(identity, dict)
        or identity.get("status") != "ready"
    ):
        raise TechnicalFulltextTargetedRevisionError(
            "Stage12 handoff is not ready for revision"
        )

    requested_key = (candidate_source_type, candidate_source_id)
    candidates = [
        item
        for item in handoff.get("revision_candidates") or []
        if isinstance(item, dict)
        and (item.get("source_type"), item.get("source_id")) == requested_key
    ]
    if len(candidates) != 1:
        raise TechnicalFulltextTargetedRevisionError(
            "requested Stage12 revision candidate must match exactly once; "
            f"found {len(candidates)}"
        )
    candidate = candidates[0]

    unresolved = candidate.get("unresolved_dependencies") or {}
    unresolved_ids = [
        str(value)
        for field in (
            "pending_fact_refs",
            "pending_material_refs",
            "pending_confirmation_refs",
        )
        for value in unresolved.get(field) or []
    ]
    if unresolved_ids:
        raise TechnicalFulltextTargetedRevisionError(
            "revision candidate has unresolved dependencies: "
            + ", ".join(unresolved_ids)
        )
    approved_fact_refs = set(candidate.get("approved_fact_refs") or [])
    required_fact_refs = set(candidate.get("required_fact_refs") or []) | set(
        candidate.get("required_confirmation_refs") or []
    )
    approved_material_refs = set(candidate.get("approved_material_ref_ids") or [])
    if not required_fact_refs <= approved_fact_refs or not set(
        candidate.get("required_material_refs") or []
    ) <= approved_material_refs:
        raise TechnicalFulltextTargetedRevisionError(
            "revision candidate required dependencies are not approved"
        )

    target_locations = [
        location
        for location in candidate.get("target_locations") or []
        if isinstance(location, dict)
    ]
    target_section_ids = {
        str(location.get("section_id") or "").strip()
        for location in target_locations
        if str(location.get("section_id") or "").strip()
    }
    if len(target_section_ids) != 1:
        raise TechnicalFulltextTargetedRevisionError(
            "Stage12 target_locations must identify exactly one section"
        )
    target_section_id = next(iter(target_section_ids))
    target_chapter_ids = {
        str(location.get("chapter_id") or "").strip()
        for location in target_locations
        if str(location.get("chapter_id") or "").strip()
    }
    if len(target_chapter_ids) > 1:
        raise TechnicalFulltextTargetedRevisionError(
            "Stage12 target_locations identify conflicting chapters"
        )
    target_chapter_id = next(iter(target_chapter_ids), None)

    source_path = Path(source_fulltext_path).expanduser().resolve()
    source_fulltext = load_json_object(source_path, label="source technical fulltext")
    source_matches = [
        (chapter, section)
        for chapter in source_fulltext["chapters"]
        for section in chapter["sections"]
        if section.get("section_id") == target_section_id
        and (
            target_chapter_id is None
            or chapter.get("chapter_id") == target_chapter_id
        )
    ]
    if len(source_matches) != 1:
        raise TechnicalFulltextTargetedRevisionError(
            "Stage12 section location must match exactly one source fulltext section; "
            f"found {len(source_matches)}"
        )
    current_section = source_matches[0][1]

    resolved_context = Stage9InputResolver().resolve(stage9_source)
    task_contexts = scoped_task_contexts(
        resolved_context,
        chapter_id=None,
        section_id=target_section_id,
    )
    if len(task_contexts) != 1:
        raise TechnicalFulltextTargetedRevisionError(
            "target section must map to exactly one existing Writing Task; "
            f"found {len(task_contexts)}"
        )
    task_context = task_contexts[0]

    effective_instruction = str(
        candidate.get("effective_revision_instruction") or ""
    ).strip()
    if not effective_instruction:
        raise TechnicalFulltextTargetedRevisionError(
            "revision candidate has no effective_revision_instruction"
        )

    final_output_root = Path(output_dir).expanduser().resolve()
    workspace_id = hashlib.sha256(output_key.encode("utf-8")).hexdigest()[:16]
    workspace_root = (
        final_output_root / "_targeted_revision_workspaces" / workspace_id
    )
    if workspace_root.exists():
        raise TechnicalFulltextTargetedRevisionError(
            f"targeted revision workspace already exists: {workspace_root}"
        )
    agent_output_key = "agent"
    agent_draft_path = (
        workspace_root / agent_output_key / "drafts" / f"{target_section_id}.md"
    )
    existing_markdown = render_technical_chapter_markdown(
        {
            "chapter_title": current_section["heading"],
            "sections": [current_section],
        },
        {"title": current_section["heading"]},
        {"valid": True, "issues": []},
    )
    atomic_write_markdown(
        agent_draft_path,
        existing_markdown,
        required_text=str(current_section.get("content") or ""),
    )
    fact_snapshot_path = workspace_root / "confirmed_project_facts.snapshot.json"
    atomic_write_json(
        fact_snapshot_path,
        stage9_source.confirmed_project_facts,
        required_fields=("facts",),
    )

    agent_input = TechnicalWritingAgentInput(
        response_outline_path=stage9_source.response_outline_path,
        writing_outline_path=stage9_source.writing_outline_path,
        task_information_path=stage9_source.task_information_path,
        draft_units_path=draft_units_path,
        confirmed_project_facts_path=fact_snapshot_path,
        writing_constraint_context=resolved_context,
        output_key=agent_output_key,
        target_section_id=target_section_id,
        stage12_approved_facts=tuple(candidate.get("approved_facts") or []),
        stage12_approved_material_refs=tuple(
            candidate.get("approved_material_refs") or []
        ),
        stage12_prohibited_inferences=tuple(
            candidate.get("prohibited_inferences") or []
        ),
        stage12_acceptance_criteria=tuple(
            str(value) for value in candidate.get("acceptance_criteria") or []
        ),
        user_instruction=effective_instruction,
        user_instruction_sources=(
            {
                "type": "stage12_revision_candidate",
                "source_type": candidate_source_type,
                "source_id": candidate_source_id,
                "handoff_id": identity.get("handoff_id"),
            },
        ),
        user_instruction_summary=effective_instruction,
        web_search_policy="disabled",
    )
    agent = build_technical_writing_agent(
        agent_input,
        llm=llm,
        output_dir=workspace_root,
        metadata_store=metadata_store,
        disable_web=True,
    )
    agent_result = agent.run(
        event_log_path=workspace_root / agent_output_key / "events.jsonl",
        stream_model=False,
    )
    validation = agent_result.get("validation") or {}
    if (
        agent_result.get("status") != "completed"
        or not validation.get("valid")
        or not agent_result.get("based_on_existing_draft")
    ):
        raise TechnicalFulltextTargetedRevisionError(
            "TechnicalWritingAgent did not produce a valid existing-draft revision: "
            f"status={agent_result.get('status')!r}, "
            f"based_on_existing_draft={agent_result.get('based_on_existing_draft')!r}, "
            f"validation={validation!r}"
        )
    candidate_path = (agent_result.get("outputs") or {}).get("candidate_json")
    replacement_document = load_json_object(
        candidate_path,
        label="TechnicalWritingAgent replacement candidate",
    )
    replacement_sections = replacement_document.get("sections") or []
    if (
        len(replacement_sections) != 1
        or replacement_sections[0].get("section_id") != target_section_id
    ):
        raise TechnicalFulltextTargetedRevisionError(
            "TechnicalWritingAgent replacement must contain exactly the target section"
        )
    replacement_section = replacement_sections[0]

    materialization = materialize_single_section_revision(
        source_fulltext_path=source_path,
        target_section_id=target_section_id,
        replacement_section=replacement_section,
        output_dir=final_output_root,
        output_key=output_key,
        stage12_handoff_path=handoff_path,
    )
    return {
        "status": "completed",
        "candidate_identity": {
            "source_type": candidate_source_type,
            "source_id": candidate_source_id,
        },
        "target_section_id": target_section_id,
        "task_id": task_context["task_id"],
        "stage9_input_fingerprint": resolved_context["fingerprint"],
        "task_fingerprint": task_context["task_fingerprint"],
        "agent_workspace": str(workspace_root.resolve()),
        "agent_output_key": agent_output_key,
        "agent_result": agent_result,
        "replacement_section": replacement_section,
        "materialization": materialization,
    }
