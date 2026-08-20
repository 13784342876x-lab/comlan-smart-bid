import json
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from app.agent_draft_generator import generate_agent_drafts as generate_agent_drafts_for_project
from app.agent_draft_generator import save_agent_drafts_to_project
from app.agent_runner import load_agent_config, run_agent as run_agent_service
from app.chapter_drafting_agent import (
    build_technical_draft as build_technical_draft_service,
    inspect_technical_draft as inspect_technical_draft_service,
)
from app.chapter_drafting_pilot import (
    build_technical_draft_pilot as build_technical_draft_pilot_service,
    inspect_technical_draft_pilot as inspect_technical_draft_pilot_service,
)
from app.actionable_requirement_index import (
    build_actionable_requirement_index as build_actionable_requirement_index_service,
    inspect_actionable_requirement_index as inspect_actionable_requirement_index_service,
)
from app.department_profiles import (
    build_project_requirement_profile,
    inspect_department_profile as inspect_department_profile_service,
    load_department_profiles,
    match_project_to_departments,
)
from app.evidence_coverage_builder import (
    build_evidence_coverage as build_evidence_coverage_service,
    inspect_evidence_coverage as inspect_evidence_coverage_service,
)
from app.evidence_resolution import (
    build_resolution_outputs_from_existing as build_evidence_resolution_queue_service,
    inspect_resolution_outputs as inspect_evidence_resolution_queue_service,
    merge_decision_input as apply_evidence_review_decisions_service,
)
from app.llm_profiles import list_profile_summaries
from app.llm_smoke_test import (
    check_drafting_model as check_drafting_model_service,
    inspect_drafting_request as inspect_drafting_request_service,
)
from app.writing_context_compiler import (
    build_writing_context as build_writing_context_service,
    inspect_writing_context as inspect_writing_context_service,
)
from app.kb_service import KnowledgeBaseService
from app.material_gap_analyzer import analyze_material_gaps as analyze_material_gaps_for_project
from app.material_matcher import (
    analyze_project_materials,
    find_project_by_lot_name,
    load_tender_projects,
    save_material_analysis_to_project,
)
from app.markdown_draft_writer import export_markdown_drafts, save_markdown_drafts_index
from app.material_taxonomy import (
    classify_all_files as classify_all_kb_material_files,
    classify_file_in_metadata as classify_kb_material_file,
    inspect_material_profile,
    list_material_profiles,
)
from app.embedded_attachment_inspector import (
    extract_embedded_attachments as extract_embedded_attachments_service,
    inspect_embedded_attachments as inspect_embedded_attachments_service,
)
from app.metadata_store import MetadataStore
from app.ocr_service import get_ocr_diagnostics, is_image_asset, is_ocr_available, ocr_assets
from app.personnel_taxonomy import (
    classify_all_personnel as classify_all_personnel_files,
    classify_personnel_file,
    inspect_personnel as inspect_personnel_profile,
    list_personnel as list_personnel_profiles,
    set_person_department as set_person_department_service,
)
from app.review_service import ReviewService
from app.source_cleanup import (
    audit_case_sources as audit_case_sources_service,
    inspect_case_readiness as inspect_case_readiness_service,
    purge_mock_materials as purge_mock_materials_service,
)
from app.response_outline_builder import (
    build_technical_outline_from_directory,
    build_response_outline,
    find_file_by_id as find_outline_file_by_id,
    inspect_parsed_text as inspect_parsed_text_service,
    inspect_project_text_sources as inspect_project_text_sources_service,
    load_metadata as load_outline_metadata,
    save_response_outline_index,
    select_outline_source_files,
)
from app.response_matrix_builder import build_response_matrix as build_response_matrix_for_project
from app.resume_parser import parse_resume as parse_structured_resume
from app.scoring_action_planner import inspect_scoring_action_plan as inspect_scoring_action_plan_service
from app.score_maximization_matrix import (
    build_score_material_outputs as build_score_material_outputs_service,
    build_score_maximization_matrix as build_score_maximization_matrix_service,
    inspect_missing_material_requests as inspect_missing_material_requests_service,
    inspect_score_maximization_matrix as inspect_score_maximization_matrix_service,
)
from app.section_service import SectionService
from app.simple_special_table_filler import (
    fill_special_tables as fill_special_tables_service,
    parse_company_performance as parse_company_performance_text,
)
from app.task_material_matcher import match_materials_for_project
from app.technical_doc_composer import compose_technical_doc as compose_technical_doc_service
from app.technical_doc_source import (
    record_docx_source as record_docx_source_service,
    verify_docx_source as verify_docx_source_service,
)
from app.tender_service import TenderService
from app.tender_metadata_extractor import (
    extract_tender_metadata as extract_tender_metadata_service,
    inspect_tender_metadata as inspect_tender_metadata_service,
    inspect_tender_source_files as inspect_tender_source_files_service,
)
from app.technical_writing_outline_adapter import (
    build_technical_writing_outline_files,
    render_outline_markdown,
)
from app.technical_writing_outline_enricher import enrich_writing_outline_files
from app.technical_agent_context_loader import build_technical_agent_context_files
from app.technical_agent_console import TechnicalAgentConsoleRenderer
from app.technical_user_instruction import collect_user_instructions
from app.technical_writing_rules import persist_confirmed_constraint
from app.technical_writing_agent import (
    TechnicalWritingAgentInput,
    build_technical_writing_agent,
)
from app.technical_fulltext_composer import (
    TechnicalFulltextComposer,
    inspect_technical_fulltext as inspect_technical_fulltext_service,
)
from app.technical_fulltext_auditor import (
    TechnicalFulltextAuditor,
    inspect_technical_fulltext_audit as inspect_technical_fulltext_audit_service,
)
from app.technical_fulltext_semantic_audit import (
    DEFAULT_MODEL_PROFILE as DEFAULT_FULLTEXT_SEMANTIC_MODEL_PROFILE,
    TechnicalFulltextSemanticAuditor,
)
from app.technical_fulltext_audit_finalizer import TechnicalFulltextAuditFinalizer
from app.technical_fulltext_hygiene import (
    TechnicalFulltextHygiene,
    inspect_technical_fulltext_hygiene as inspect_technical_fulltext_hygiene_service,
)
from app.technical_fulltext_runner import TechnicalFulltextRunner
from app.technical_section_task_planner import build_section_task_plan_files
from app.technical_task_information_planner import build_task_information_files
from app.draft_unit_planner import build_draft_unit_files
from app.tender_requirement_matrix import (
    build_tender_requirement_matrix as build_tender_requirement_matrix_service,
    inspect_tender_requirement_matrix as inspect_tender_requirement_matrix_service,
)
from app.vector_store import VectorStore
from app.writing_orchestrator import build_writing_plan, save_writing_plan_to_project
from config import DATA_DIR, ensure_directories


cli = typer.Typer(help="comlan backend CLI")
console = Console()


def parse_tags(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def print_json(data) -> None:
    console.print_json(json.dumps(data, ensure_ascii=False, indent=2))


def load_project(project_index: int) -> dict:
    path = DATA_DIR / "tender_projects.json"
    if not path.exists():
        raise FileNotFoundError(f"project file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8") or '{"projects": []}')
    projects = data.get("projects") or []
    if not projects:
        raise ValueError("no tender projects found; run parse-tender-project first")
    try:
        return projects[project_index]
    except IndexError as exc:
        raise IndexError(f"project index out of range: {project_index}") from exc


def load_json_file(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"json file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8") or "{}")


def save_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_agent_input_json(value: str) -> dict[str, Any]:
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        data = parse_loose_agent_input(value)
    if not isinstance(data, dict):
        raise ValueError("agent input must be a JSON object")
    return data


def parse_loose_agent_input(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    result: dict[str, Any] = {}
    current = []
    depth = 0
    parts = []
    for char in text:
        if char == "[":
            depth += 1
        elif char == "]" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    for part in parts:
        if ":" not in part:
            continue
        key, raw_value = part.split(":", 1)
        key = key.strip().strip("\"'")
        raw_value = raw_value.strip().strip("\"'")
        if raw_value.startswith("[") and raw_value.endswith("]"):
            items = [item.strip().strip("\"'") for item in raw_value[1:-1].split(",") if item.strip()]
            result[key] = items
        else:
            result[key] = raw_value
    if not result:
        raise ValueError("input-json is not valid JSON")
    return result


def compact_list(value: Any, empty: str = "-") -> str:
    if not value:
        return empty
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(cleaned) if cleaned else empty
    text = str(value).strip()
    return text or empty


def short_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def format_top_materials(matched_materials: list[dict[str, Any]], top_n: int = 3) -> str:
    lines: list[str] = []
    for index, material in enumerate(matched_materials[:top_n], start=1):
        name = str(material.get("original_name") or material.get("file_id") or "-")
        relevance = str(material.get("relevance") or "-")
        matched_evidence = compact_list(material.get("matched_evidence"), empty="-").replace("\n", ", ")
        reason = short_text(material.get("match_reason"), limit=140)
        lines.append(f"{index}. {name} | {relevance} | {matched_evidence} | {reason}")
    return "\n".join(lines) if lines else "-"


def save_resume_parse(file_id: str, structured_resume: dict[str, Any]) -> dict[str, Any]:
    store = MetadataStore()
    data = store.load()
    data.setdefault("resumes", {})[file_id] = structured_resume
    files = data.setdefault("files", [])
    for index, item in enumerate(files):
        if item.get("id") == file_id:
            files[index] = {**item, "resume_parse": structured_resume}
            store.save(data)
            return files[index]
    raise KeyError(f"file_id not found: {file_id}")


def is_metadata_file_id(value: str) -> bool:
    path = Path(value)
    if path.exists() and path.is_file():
        return False
    try:
        MetadataStore().get_file(value)
    except KeyError:
        return False
    return True


def get_saved_resume_parse(file_id: str) -> dict[str, Any]:
    store = MetadataStore()
    data = store.load()
    if file_id in data.get("resumes", {}):
        return data["resumes"][file_id]
    for item in data.get("files", []):
        if item.get("id") == file_id and item.get("resume_parse"):
            return item["resume_parse"]
    raise KeyError(f"resume parse not found for file_id: {file_id}; run parse-resume first")


def first_text_value(record: dict[str, Any], paths: list[tuple[str, ...]]) -> str:
    for path in paths:
        value: Any = record
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def first_list_value(record: dict[str, Any], paths: list[tuple[str, ...]]) -> list[Any]:
    for path in paths:
        value: Any = record
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, list):
            return value
    return []


def available_record_fields(record: dict[str, Any]) -> list[str]:
    fields = sorted(record.keys())
    parse_result = record.get("parse_result")
    if isinstance(parse_result, dict):
        fields.extend(f"parse_result.{key}" for key in sorted(parse_result.keys()))
    return fields


def resume_parse_missing_text_error(file_id: str, record: dict[str, Any]) -> str:
    return (
        "parsed_text is empty for resume parsing. "
        f"file_id={file_id}; "
        f"original_name={record.get('original_name') or '-'}; "
        f"available_fields={', '.join(available_record_fields(record))}; "
        f"suggestion: run `python cli.py parse-file {file_id}` first."
    )


def get_file_record_from_data(data: dict[str, Any], file_id: str) -> dict[str, Any]:
    for record in data.get("files", []):
        if record.get("id") == file_id:
            return record
    raise KeyError(f"file_id not found: {file_id}")


def update_file_record_in_data(data: dict[str, Any], file_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    files = data.setdefault("files", [])
    for index, record in enumerate(files):
        if record.get("id") == file_id:
            files[index] = {**record, **patch}
            return files[index]
    raise KeyError(f"file_id not found: {file_id}")


def certificate_status_counts(certificates: list[dict[str, Any]]) -> dict[str, int]:
    canonical_count = sum(1 for item in certificates if item.get("canonical_id"))
    return {
        "certificates_total": len(certificates),
        "canonical_certificates_count": canonical_count,
        "usable_certificates_count": sum(1 for item in certificates if item.get("canonical_id") or item.get("usable")),
        "normalized_certificates_count": sum(1 for item in certificates if item.get("standardization_status") == "normalized"),
        "original_kept_certificates_count": sum(1 for item in certificates if item.get("standardization_status") == "original_kept"),
        "needs_ocr_count": sum(1 for item in certificates if item.get("needs_ocr")),
        "review_required_count": sum(1 for item in certificates if item.get("review_required")),
    }


def format_certificate_line(item: dict[str, Any]) -> str:
    if item.get("canonical_id"):
        persons = list(item.get("persons") or [])
        sources = list(item.get("sources") or [])
        confidence = item.get("confidence")
        confidence_text = f"{float(confidence):.2f}" if isinstance(confidence, (int, float)) else "-"
        return (
            f"{item.get('display_name') or item.get('standard_name') or item.get('canonical_id')} | "
            f"canonical_id: {item.get('canonical_id')} | "
            f"standard_name: {item.get('standard_name') or '-'} | "
            f"family: {item.get('family') or '-'} | "
            f"sources: {', '.join(str(source) for source in sources) or '-'} | "
            f"person_count: {len(persons)} | "
            f"persons: {', '.join(str(person) for person in persons) or '-'} | "
            f"confidence: {confidence_text}"
        )
    status = "可用" if item.get("usable") else "不可用"
    standardization = {
        "normalized": "已标准化",
        "original_kept": "原样保留",
        "image_pending_ocr": "待OCR",
        "name_not_extracted": "OCR未提取证书名",
    }.get(str(item.get("standardization_status") or ""), str(item.get("standardization_status") or "-"))
    display_name = item.get("display_name") or item.get("normalized_name") or item.get("name") or f"asset_id={item.get('asset_id') or '-'}"
    source = "/".join(str(part) for part in [item.get("source_type"), item.get("evidence_level")] if part)
    family = item.get("family") or "-"
    family_confidence = item.get("family_confidence")
    family_confidence_text = f"{float(family_confidence):.2f}" if isinstance(family_confidence, (int, float)) else "-"
    standard_name = item.get("standard_name") or "-"
    ownership_confidence = item.get("ownership_confidence")
    ownership_confidence_text = f"{float(ownership_confidence):.2f}" if isinstance(ownership_confidence, (int, float)) else "-"
    return (
        f"[{status}/{standardization}] {display_name} | "
        f"原文: {short_text(item.get('name') or item.get('evidence_text'), 80) or '-'} | "
        f"standard_name: {standard_name} | "
        f"厂商: {item.get('vendor') or '-'} | family: {family} | "
        f"family_confidence: {family_confidence_text} | "
        f"owner: {item.get('person_name') or '-'} | "
        f"ownership_confidence: {ownership_confidence_text} | "
        f"ownership_source: {item.get('ownership_source') or '-'} | 来源: {source or '-'}"
    )


@cli.command("ingest-file")
def ingest_file(
    file_path: Path,
    category: str = typer.Option(..., "--category"),
    tags: str = typer.Option("", "--tags"),
) -> None:
    try:
        ensure_directories()
        record = KnowledgeBaseService().ingest_file(file_path, category, parse_tags(tags))
        print_json({"status": "success", "file_id": record["id"], "result": record})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("parse-file")
def parse_file(file_id: str) -> None:
    try:
        result = KnowledgeBaseService().parse_file(file_id)
        print_json(
            {
                "ok": result.get("ok", False),
                "file_id": file_id,
                "file_type": result.get("file_type", ""),
                "parsed_text": result.get("parsed_text", ""),
                "tables": result.get("tables", []),
                "assets": result.get("assets", []),
                "warnings": result.get("warnings", []),
                "message": result.get("message", ""),
            }
        )
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("parse-resume")
def parse_resume_command(
    file_id: str,
    department: str | None = typer.Option(None, "--department"),
    source_folder: str | None = typer.Option(None, "--source-folder"),
) -> None:
    try:
        store = MetadataStore()
        record = store.get_file(file_id)
        parsed_text = first_text_value(
            record,
            [
                ("parsed_text",),
                ("text",),
                ("content",),
                ("parse_result", "parsed_text"),
                ("parse_result", "text"),
            ],
        )
        if not parsed_text.strip():
            raise ValueError(resume_parse_missing_text_error(file_id, record))
        assets = first_list_value(record, [("assets",), ("parse_result", "assets")])
        structured_resume = parse_structured_resume(
            parsed_text=parsed_text,
            original_name=record.get("original_name", ""),
            source_path=record.get("source_path"),
            source_folder=source_folder or record.get("source_folder"),
            department=department,
            assets=assets,
            file_id=file_id,
        )
        save_resume_parse(file_id, structured_resume)
        print_json({"ok": True, "file_id": file_id, "resume": structured_resume})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("ocr-assets")
def ocr_assets_command(
    file_id: str,
    engine: str = typer.Option("paddleocr", "--engine"),
    force: bool = typer.Option(False, "--force"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    try:
        store = MetadataStore()
        data = store.load()
        record = get_file_record_from_data(data, file_id)
        diagnostics = get_ocr_diagnostics(engine)
        assets = list(record.get("assets") or [])
        image_assets = [asset for asset in assets if is_image_asset(asset)]
        updated_assets = ocr_assets(assets, engine=engine, force=force, limit=limit)

        rows = []
        success_count = skipped_count = failed_count = already_done_count = 0
        for asset in updated_assets:
            if not is_image_asset(asset):
                continue
            status = asset.get("ocr_status") or "not_run"
            if asset.get("_ocr_already_done"):
                already_done_count += 1
            elif status == "success":
                success_count += 1
            elif status == "skipped":
                skipped_count += 1
            elif status == "failed":
                failed_count += 1
            rows.append(
                {
                    "asset_id": asset.get("id") or asset.get("asset_id"),
                    "ocr_status": status,
                    "ocr_confidence": asset.get("ocr_confidence", 0.0),
                    "preview": short_text(asset.get("ocr_text") or asset.get("ocr_error"), 100),
                }
            )
        cleaned_assets = []
        for asset in updated_assets:
            cleaned = dict(asset)
            cleaned.pop("_ocr_already_done", None)
            cleaned_assets.append(cleaned)
        updated_record = update_file_record_in_data(data, file_id, {"assets": cleaned_assets})
        store.save(data)

        print_json(
            {
                "file_id": file_id,
                "original_name": updated_record.get("original_name"),
                "ocr_available": is_ocr_available(engine),
                **diagnostics,
                "asset_count": len(assets),
                "image_asset_count": len(image_assets),
                "success_count": success_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
                "already_done_count": already_done_count,
                "assets": rows,
            }
        )
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-ocr")
def inspect_ocr(file_id: str) -> None:
    try:
        store = MetadataStore()
        data = store.load()
        record = get_file_record_from_data(data, file_id)
        diagnostics = get_ocr_diagnostics()
        assets = list(record.get("assets") or [])
        image_assets = [asset for asset in assets if is_image_asset(asset)]
        parsed_text = first_text_value(
            record,
            [("parsed_text",), ("text",), ("content",), ("parse_result", "parsed_text"), ("parse_result", "text")],
        )
        resume_preview = parse_structured_resume(
            parsed_text=parsed_text,
            original_name=record.get("original_name", ""),
            source_path=record.get("source_path"),
            source_folder=record.get("source_folder"),
            department=record.get("department"),
            assets=assets,
        )
        embedded_certificates = [
            item for item in resume_preview.get("certificates", []) if item.get("source_type") == "embedded_image"
        ]

        table = Table(title=f"OCR Inspection: {file_id}", show_lines=True)
        for column in [
            "asset_id",
            "source_location",
            "path",
            "ocr_status",
            "engine",
            "confidence",
            "ocr_text",
            "ocr_error",
        ]:
            table.add_column(column)
        for asset in image_assets:
            table.add_row(
                str(asset.get("id") or asset.get("asset_id") or ""),
                str(asset.get("source_location") or ""),
                str(asset.get("path") or asset.get("file_path") or asset.get("local_path") or asset.get("asset_key") or ""),
                str(asset.get("ocr_status") or "not_run"),
                str(asset.get("ocr_engine") or ""),
                str(asset.get("ocr_confidence") if asset.get("ocr_confidence") is not None else ""),
                short_text(asset.get("ocr_text"), 300),
                short_text(asset.get("ocr_error"), 160),
            )
        console.print(table)
        print_json(
            {
                "file_id": file_id,
                "original_name": record.get("original_name"),
                "asset_count": len(assets),
                "image_asset_count": len(image_assets),
                "ocr_available": is_ocr_available(),
                **diagnostics,
                "embedded_certificate_candidates": [
                    {
                        "asset_id": item.get("asset_id"),
                        "name": item.get("name"),
                        "normalized_name": item.get("normalized_name"),
                        "vendor": item.get("vendor"),
                        "level": item.get("level"),
                        "evidence_level": item.get("evidence_level"),
                        "needs_ocr": item.get("needs_ocr"),
                        "usable": item.get("usable"),
                        "review_required": item.get("review_required"),
                        "review_reason": item.get("review_reason"),
                    }
                    for item in embedded_certificates
                ],
            }
        )
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-resume")
def inspect_resume(file_id: str) -> None:
    try:
        resume = get_saved_resume_parse(file_id)
        work_history = list(resume.get("work_history") or [])
        skills = list(resume.get("skills") or [])
        projects = list(resume.get("projects") or [])
        certificates = list(resume.get("certificates") or [])
        cert_counts = certificate_status_counts(certificates)
        usable_certificates = [item for item in certificates if item.get("canonical_id") or item.get("usable")]
        needs_processing = [
            item
            for item in certificates
            if not item.get("canonical_id") and (item.get("review_required") or item.get("needs_ocr"))
        ]
        summary = {
            "person_name": resume.get("person_name"),
            "gender": resume.get("gender"),
            "department": resume.get("department"),
            "source_folder": resume.get("source_folder"),
            "work_years": resume.get("work_years"),
            "work_history_count": len(work_history),
            "work_history": work_history,
            "skill_count": len(skills),
            "skills_preview": skills[:20],
            "project_count": len(projects),
            "projects_preview": projects[:5],
            **cert_counts,
            "canonical_certificates": certificates,
            "usable_certificates": usable_certificates,
            "needs_processing": needs_processing,
            "certificates": certificates,
            "capability_profile": resume.get("capability_profile", {}),
            "parse_warnings": resume.get("parse_warnings", []),
        }
        table = Table(title=f"Resume Inspection: {file_id}", show_lines=True)
        table.add_column("field")
        table.add_column("value")
        table.add_row(
            "basic_info",
            "\n".join(
                [
                    f"person_name: {summary['person_name'] or '-'}",
                    f"gender: {summary['gender'] or '-'}",
                    f"work_years: {summary['work_years'] if summary['work_years'] is not None else '-'}",
                    f"department: {summary['department'] or '-'}",
                    f"source_folder: {summary['source_folder'] or '-'}",
                ]
            ),
        )
        table.add_row(
            f"work_history ({summary['work_history_count']})",
            compact_list(
                [
                    " | ".join(str(part or "-") for part in [item.get("time"), item.get("company"), item.get("position")])
                    for item in work_history
                ],
                empty="-",
            ),
        )
        table.add_row(
            f"skills ({summary['skill_count']})",
            compact_list(
                [
                    f"{item.get('name') or '-'}" + (f" ({item.get('years')})" if item.get("years") else "")
                    for item in skills[:20]
                ],
                empty="-",
            ),
        )
        table.add_row(
            f"projects ({summary['project_count']})",
            compact_list(
                [
                    "\n".join(
                        [
                            f"name: {item.get('name') or '-'}",
                            f"date: {item.get('date') or '-'}",
                            f"products_or_technologies: {item.get('products_or_technologies') or '-'}",
                            f"role_or_work_content: {short_text(item.get('role_or_work_content'), 100) or '-'}",
                        ]
                    )
                    for item in projects[:5]
                ],
                empty="-",
            ),
        )
        table.add_row(
            "certificate_stats",
            "\n".join(f"{key}: {value}" for key, value in cert_counts.items()),
        )
        table.add_row(
            "capability_profile",
            "\n".join(f"{key}: {value}" for key, value in summary["capability_profile"].items()) or "-",
        )
        table.add_row(
            f"canonical_certificates ({len(usable_certificates)})",
            compact_list([format_certificate_line(item) for item in usable_certificates], empty="-"),
        )
        table.add_row(
            f"needs_processing ({len(needs_processing)})",
            compact_list([format_certificate_line(item) + f" | reason: {item.get('review_reason') or '-'}" for item in needs_processing], empty="-"),
        )
        table.add_row("parse_warnings", compact_list(summary["parse_warnings"], empty="-"))
        console.print(table)
        print_json(summary)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command()
def classify_file(file_id: str):
    try:
        from app.kb_service import classify_file

        result = classify_file(file_id)
        print_json({"status": "success", "file_id": file_id, "result": result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command()
def ingest_auto(path: str):
    try:
        from app.kb_service import ingest_auto

        result = ingest_auto(path)
        print_json(
            {
                "status": "success",
                "file_id": result.get("file_id"),
                "classification": result.get("classification"),
                "is_bundle": result.get("is_bundle"),
                "detected_items": result.get("detected_items", []),
                "pending_items": result.get("pending_items", []),
                "result": result,
                "message": "DONE",
            }
        )
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command()
def split_file(file_id: str):
    try:
        from app.kb_service import split_file

        result = split_file(file_id)
        print_json({"status": "success", "file_id": file_id, "result": result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("list-split-items")
def list_split_items(file_id: str) -> None:
    items = KnowledgeBaseService().list_split_items(file_id)
    if not items:
        console.print("[yellow]No detected items. Run split-file first.[/yellow]")
        return
    table = Table(title=f"Detected Items: {file_id}")
    for col in [
        "id",
        "name",
        "category",
        "document_type",
        "evidence_level",
        "confidence",
        "status",
        "related_asset_ids",
    ]:
        table.add_column(col)
    for item in items:
        table.add_row(
            item["id"],
            item["name"],
            item["category"],
            item["document_type"],
            str(item.get("evidence_level", "")),
            f"{item.get('confidence', 0):.2f}",
            item["status"],
            ",".join(item.get("related_asset_ids", [])),
        )
    console.print(table)


@cli.command("confirm-split-item")
def confirm_split_item(file_id: str, item_id: str) -> None:
    item = KnowledgeBaseService().confirm_split_item(file_id, item_id)
    print_json({"ok": True, "file_id": file_id, "item": item})


@cli.command()
def list_pending_items():
    try:
        from app.kb_service import list_pending_items

        result = list_pending_items()
        print_json({"status": "success", "result": result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command()
def confirm_item(item_id: str):
    try:
        from app.kb_service import confirm_item

        result = confirm_item(item_id)
        print_json({"status": "success", "item_id": item_id, "result": result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command()
def ignore_item(item_id: str):
    try:
        from app.kb_service import ignore_item

        result = ignore_item(item_id)
        print_json({"status": "success", "item_id": item_id, "result": result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("index-file")
def index_file(file_id: str) -> None:
    result = KnowledgeBaseService().index_file(file_id)
    print_json({"ok": True, **result})


@cli.command("inspect-index-file")
def inspect_index_file(
    file_id: str,
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    try:
        result = VectorStore().inspect_by_file_id(file_id, limit=limit)
        table = Table(title=f"Chroma Index Metadata: {file_id}")
        for col in [
            "file_id",
            "split_item_id",
            "name",
            "category",
            "document_type",
            "source_name",
            "vector_id",
        ]:
            table.add_column(col)
        for item in result.get("items", []):
            table.add_row(
                str(item.get("file_id") or ""),
                str(item.get("split_item_id") or ""),
                str(item.get("name") or ""),
                str(item.get("category") or ""),
                str(item.get("document_type") or ""),
                str(item.get("source_name") or ""),
                str(item.get("vector_id") or ""),
            )
        console.print(table)
        print_json(
            {
                "file_id": result.get("file_id"),
                "collection_count": result.get("collection_count"),
                "matched_count": result.get("matched_count"),
                "limit": limit,
            }
        )
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("delete-index-file")
def delete_index_file(file_id: str) -> None:
    try:
        print(f"[delete-index-file] target_file_id={file_id}")
        result = VectorStore().delete_by_file_id(file_id)
        print(f"[delete-index-file] deleted_count={result.get('deleted_count', 0)}")
        print(f"[delete-index-file] collection_count={result.get('after_count', 0)}")
        print_json({"ok": True, **result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("search-kb")
def search_kb(query: str, top_k: int = typer.Option(5, "--top-k")) -> None:
    rows = KnowledgeBaseService().search(query, top_k=top_k)
    if not rows:
        console.print("[yellow]No results. Run parse-file and index-file first.[/yellow]")
        return
    table = Table(title=f"KB Search: {query}")
    for col in ["file_id", "split_item_id", "original_name", "category", "tags", "matched_text", "distance"]:
        table.add_column(col)
    for row in rows:
        table.add_row(
            str(row.get("file_id", "")),
            str(row.get("split_item_id", "")),
            str(row.get("original_name", "")),
            str(row.get("category", "")),
            str(row.get("tags", "")),
            str(row.get("matched_text", ""))[:80],
            f"{row.get('distance', 0):.4f}",
        )
    console.print(table)


@cli.command("query-knowledge")
def query_knowledge(query: str) -> None:
    try:
        from app.enterprise_knowledge_query import query_enterprise_knowledge

        metadata = MetadataStore().load()
        result = query_enterprise_knowledge(query, metadata)
        hits = result.get("hits", {})
        console.print(
            {
                "certificates": len(hits.get("certificates", [])),
                "personnel": len(hits.get("personnel", [])),
                "qualifications": len(hits.get("qualifications", [])),
                "files": len(hits.get("files", [])),
            }
        )
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("parse-tender")
def parse_tender(tender_ref: str) -> None:
    try:
        from app.kb_service import parse_tender as parse_tender_service

        print_json(parse_tender_service(tender_ref))
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("parse-company-qualification")
def parse_company_qualification(tender_file: str) -> None:
    try:
        from app.company_qualification import parse_company_qualification_file

        result = parse_company_qualification_file(tender_file)
        output = dict(result)
        if is_metadata_file_id(tender_file):
            stored = MetadataStore().upsert_company_qualification(
                tender_file,
                {
                    "requirements": result.get("requirements", []),
                    "source": result.get("source"),
                    "parsed_at": datetime.now().isoformat(timespec="seconds"),
                    "parser_version": "company_qualification.v1",
                },
            )
            output = stored
        print_json(output)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("parse-company-performance")
def parse_company_performance(file_id: str) -> None:
    try:
        store = MetadataStore()
        record = store.get_file(file_id)
        parsed_text = first_text_value(
            record,
            [
                ("parsed_text",),
                ("text",),
                ("content",),
                ("parse_result", "parsed_text"),
                ("parse_result", "text"),
            ],
        )
        if not parsed_text.strip():
            raise ValueError(f"parsed_text is empty for company performance parsing; run `python cli.py parse-file {file_id}` first.")
        performance = parse_company_performance_text(parsed_text)
        stored = store.upsert_company_performance(file_id, performance)
        print_json({"ok": True, "file_id": file_id, "company_performance": stored})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("fill-special-tables")
def fill_special_tables(
    template: Path = typer.Option(..., "--template"),
    output: Path = typer.Option(..., "--output"),
    debug_output: Path | None = typer.Option(None, "--debug-output"),
    extract_only: bool = typer.Option(False, "--extract-only"),
    missing_report: bool = typer.Option(True, "--missing-report/--no-missing-report"),
) -> None:
    try:
        result = fill_special_tables_service(
            template,
            output,
            debug_path=debug_output,
            extract_only=extract_only,
            missing_report=missing_report,
        )
        print_json({"ok": True, **result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("compose-technical-doc")
def compose_technical_doc(
    base: Path = typer.Option(..., "--base"),
    tables: Path = typer.Option(..., "--tables"),
    output: Path = typer.Option(..., "--output"),
    debug_output: Path | None = typer.Option(None, "--debug-output"),
    source_markdown: Path | None = typer.Option(None, "--source-markdown"),
    converter: str = typer.Option("technical_doc_composer", "--converter"),
    reference_doc: Path | None = typer.Option(None, "--reference-doc"),
) -> None:
    try:
        result = compose_technical_doc_service(base, tables, output, debug_path=debug_output)
        if source_markdown is not None:
            result["source_record"] = record_docx_source_service(
                source_markdown,
                output,
                converter=converter,
                reference_doc=reference_doc,
            )
        print_json({"ok": True, **result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("record-technical-doc-source")
def record_technical_doc_source(
    markdown: Path = typer.Option(..., "--markdown"),
    docx: Path = typer.Option(..., "--docx"),
    converter: str = typer.Option(..., "--converter"),
    reference_doc: Path | None = typer.Option(None, "--reference-doc"),
    record: Path | None = typer.Option(None, "--record"),
) -> None:
    try:
        result = record_docx_source_service(
            markdown,
            docx,
            converter=converter,
            reference_doc=reference_doc,
            record_path=record,
        )
        print_json({"ok": True, **result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("verify-technical-doc-source")
def verify_technical_doc_source(
    markdown: Path = typer.Option(..., "--markdown"),
    docx: Path = typer.Option(..., "--docx"),
    record: Path | None = typer.Option(None, "--record"),
) -> None:
    result = verify_docx_source_service(
        markdown,
        docx,
        record_path=record,
    )
    print_json({"ok": bool(result["matches"]), **result})
    if not result["matches"]:
        raise typer.Exit(code=1)


@cli.command("inspect-structured-facts")
def inspect_structured_facts(file_id: str) -> None:
    try:
        store = MetadataStore()
        record = store.get_file(file_id)
        pending_items = store.pending_items_for_file(file_id)
        assets = list(record.get("assets") or [])
        image_assets = [asset for asset in assets if is_image_asset(asset)]
        ocr_success_count = sum(1 for asset in image_assets if asset.get("ocr_status") == "success")
        result = {
            "file_id": file_id,
            "original_name": record.get("original_name"),
            "has_parsed_text": bool(str(record.get("parsed_text") or "").strip()),
            "asset_count": len(assets),
            "ocr_success_count": ocr_success_count,
            "has_resume_parse": bool(record.get("resume_parse")),
            "has_company_qualification": bool(record.get("company_qualification")),
            "has_company_performance": bool(record.get("company_performance")),
            "detected_items_count": len(record.get("detected_items") or []),
            "pending_items_count": len(pending_items),
        }
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("parse-score-table")
def parse_score_table(score_file_id: str, lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        from app.kb_service import parse_score_table as parse_score_table_service

        print("[score-table] parsing score table")
        print(f"[score-table] lot selected: {lot_name}")
        result = parse_score_table_service(score_file_id, lot_name)
        print(f"[score-table] generated {len(result.get('score_items', []))} score items")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("parse-tender-project")
def parse_tender_project(
    tender_file_id: str = typer.Option(..., "--tender-file-id"),
    score_file_id: str = typer.Option(..., "--score-file-id"),
    lot_name: str = typer.Option(..., "--lot-name"),
) -> None:
    try:
        from app.kb_service import parse_tender_project as parse_tender_project_service

        print("[merge] merging tender and score table")
        print(f"[merge] lot selected: {lot_name}")
        result = parse_tender_project_service(tender_file_id, score_file_id, lot_name)
        print(f"[merge] generated tender task graph with {len(result.get('generation_tasks', []))} tasks")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("audit-case-sources")
def audit_case_sources(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        result = audit_case_sources_service(lot_name)
        table = Table(title=f"Case Sources: {lot_name}", show_lines=True)
        for column in [
            "file_id",
            "file_name",
            "file_role",
            "parsed_text_len",
            "tables_count",
            "is_known_mock",
            "is_suspected_mock",
            "physical_path",
            "physical_exists",
            "is_full_tender_document_candidate",
            "risk_notes",
        ]:
            table.add_column(column)
        for item in list(result.get("files") or []):
            table.add_row(
                str(item.get("file_id") or ""),
                str(item.get("file_name") or ""),
                str(item.get("file_role") or ""),
                str(item.get("parsed_text_len") or 0),
                str(item.get("tables_count") or 0),
                str(item.get("is_known_mock")),
                str(item.get("is_suspected_mock")),
                str(item.get("physical_path") or ""),
                str(item.get("physical_exists")),
                str(item.get("is_full_tender_document_candidate")),
                compact_list(item.get("risk_notes")),
            )
        console.print(table)
        for warning in list(result.get("warnings") or []):
            console.print(f"[yellow]{warning}[/yellow]")
        print_json(
            {
                "lot_name": result.get("lot_name"),
                "warnings": result.get("warnings"),
                "readiness": result.get("readiness"),
            }
        )
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("purge-mock-materials")
def purge_mock_materials(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        result = purge_mock_materials_service(lot_name)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-case-readiness")
def inspect_case_readiness(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        result = inspect_case_readiness_service(lot_name)
        console.print("# Case Source Readiness")
        console.print(f"- 标段/包号：{lot_name}")
        console.print(f"- 完整招标文件正文：{'已检测到' if result['full_tender_document']['status'] == 'detected' else '缺失'}")
        console.print(f"- 招标文件附件：{'已检测到' if result['tender_attachment']['status'] == 'detected' else '未检测到'}")
        console.print(f"- 技术规范书：{'已检测到' if result['technical_spec']['status'] == 'detected' else '未检测到'}")
        console.print(f"- 评标办法/评分表：{'已检测到' if result['score_table']['status'] == 'detected' else '未检测到'}")
        console.print(f"- 旧 mock / 泛化摘要文件：{'仍有残留' if result['old_mock_files']['status'] == 'remaining' else '已清理'}")
        console.print(f"- 当前是否允许继续元信息抽取：{'是' if result['allow_metadata_extraction'] else '否，缺少完整招标文件正文'}")
        console.print(f"- 当前是否允许重新生成正式 outline：{'是' if result['allow_formal_outline'] else '否，缺少完整招标文件正文'}")
        if result.get("old_mock_files", {}).get("file_ids"):
            console.print("\n## 已清理/待清理 mock 文件")
            for file_id in result["old_mock_files"]["file_ids"]:
                console.print(f"- {file_id}")
        if result.get("blocking_reasons"):
            console.print("\n## 阻塞原因")
            for reason in result["blocking_reasons"]:
                console.print(f"- {reason}")
        console.print("\n## 下一步")
        for step in list(result.get("next_steps") or []):
            console.print(f"- {step}")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("match-materials")
def match_materials(
    lot_name: str = typer.Option(..., "--lot-name"),
    save: bool = typer.Option(False, "--save"),
) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        metadata = MetadataStore().load()
        result = analyze_project_materials(project, metadata)
        if save:
            save_material_analysis_to_project(lot_name, result)
            print(f"[material-match] saved material_analysis for lot_name={lot_name}")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-material-match")
def inspect_material_match(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        analysis = project.get("material_analysis")
        if not isinstance(analysis, dict):
            raise ValueError(f"material_analysis not found for lot_name={lot_name}; run match-materials --save first")

        summary = analysis.get("summary") or {}
        console.print(
            {
                "lot_name": lot_name,
                "summary": summary,
            }
        )

        table = Table(title=f"Material Match: {lot_name}", show_lines=True)
        for column in [
            "task_name",
            "match_status",
            "required_material_types",
            "matched",
            "missing",
            "top_evidence",
        ]:
            table.add_column(column)

        for item in list(analysis.get("task_material_map") or []):
            matched_materials = list(item.get("matched_materials") or [])
            missing_materials = list(item.get("missing_materials") or [])
            top = matched_materials[0] if matched_materials else {}
            top_evidence = " | ".join(
                str(value or "-")
                for value in [
                    top.get("file_id"),
                    top.get("title"),
                    short_text(top.get("snippet"), 140),
                ]
            )
            table.add_row(
                str(item.get("task_name") or item.get("task_id") or ""),
                str(item.get("match_status") or ""),
                compact_list(item.get("required_material_types")),
                str(len(matched_materials)),
                str(len(missing_materials)),
                top_evidence if matched_materials else "-",
            )
        console.print(table)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("plan-writing")
def plan_writing(
    lot_name: str = typer.Option(..., "--lot-name"),
    save: bool = typer.Option(False, "--save"),
) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        if not isinstance(project.get("material_analysis"), dict):
            raise ValueError(f"material_analysis not found for lot_name={lot_name}; run match-materials --save first")
        result = build_writing_plan(project, MetadataStore().load())
        if save:
            save_writing_plan_to_project(lot_name, result)
            print(f"[writing-plan] saved writing_plan for lot_name={lot_name}")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-writing-plan")
def inspect_writing_plan(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        writing_plan = project.get("writing_plan")
        if not isinstance(writing_plan, dict):
            raise ValueError(f"writing_plan not found for lot_name={lot_name}; run plan-writing --save first")

        summary = writing_plan.get("summary") or {}
        console.print(
            {
                "lot_name": lot_name,
                "summary": summary,
            }
        )

        table = Table(title=f"Writing Plan: {lot_name}", show_lines=True)
        for column in [
            "task_name",
            "section_type",
            "writer_agent",
            "status",
            "evidence",
            "missing",
            "draft_preview",
        ]:
            table.add_column(column)

        for item in list(writing_plan.get("draft_sections") or []):
            table.add_row(
                str(item.get("task_name") or item.get("task_id") or ""),
                str(item.get("section_type") or ""),
                str(item.get("writer_agent") or ""),
                str(item.get("status") or ""),
                str(len(list(item.get("evidence_refs") or []))),
                str(len(list(item.get("missing_materials") or []))),
                short_text(item.get("draft_text"), 200),
            )
        console.print(table)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-agent")
def inspect_agent(agent_name: str) -> None:
    try:
        config = load_agent_config(agent_name)
        schema = config.get("output_schema") if isinstance(config.get("output_schema"), dict) else {}
        required = schema.get("required") if isinstance(schema, dict) else None
        result = {
            "agent_name": agent_name,
            "has_system_prompt": bool(config.get("has_system_prompt")),
            "has_output_schema": bool(config.get("has_output_schema")),
            "schema_required_fields": required if isinstance(required, list) else [],
            "system_prompt_preview": short_text(config.get("system_prompt"), 300),
            "errors": config.get("errors") or [],
            "notes": config.get("notes") or [],
        }
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("run-agent")
def run_agent_command(
    agent_name: str,
    input_json: str | None = typer.Option(None, "--input-json"),
    input_file: Path | None = typer.Option(None, "--input-file"),
    provider: str = typer.Option("mock", "--provider"),
) -> None:
    try:
        if input_file is not None:
            input_data = load_json_file(input_file)
        elif input_json:
            input_data = parse_agent_input_json(input_json)
        else:
            input_data = {}
        if not isinstance(input_data, dict):
            raise ValueError("agent input must be a JSON object")
        print_json(run_agent_service(agent_name, input_data, provider=provider))
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("generate-agent-drafts")
def generate_agent_drafts(
    lot_name: str = typer.Option(..., "--lot-name"),
    save: bool = typer.Option(False, "--save"),
    provider: str = typer.Option("mock", "--provider"),
) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        if not isinstance(project.get("writing_plan"), dict):
            raise ValueError(f"writing_plan not found for lot_name={lot_name}; run plan-writing --save first")
        result = generate_agent_drafts_for_project(project, provider=provider)
        if save:
            save_agent_drafts_to_project(lot_name, result)
            print(f"[agent-drafts] saved agent_drafts for lot_name={lot_name}")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-agent-drafts")
def inspect_agent_drafts(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        agent_drafts = project.get("agent_drafts")
        if not isinstance(agent_drafts, dict):
            raise ValueError(f"agent_drafts not found for lot_name={lot_name}; run generate-agent-drafts --save first")

        summary = agent_drafts.get("summary") or {}
        console.print({"lot_name": lot_name, "summary": summary})

        table = Table(title=f"Agent Drafts: {lot_name}", show_lines=True)
        for column in [
            "task_name",
            "writer_agent",
            "section_type",
            "status",
            "evidence",
            "missing",
            "draft_preview",
        ]:
            table.add_column(column)

        for item in list(agent_drafts.get("agent_draft_sections") or []):
            table.add_row(
                str(item.get("task_name") or item.get("task_id") or ""),
                str(item.get("writer_agent") or ""),
                str(item.get("section_type") or ""),
                str(item.get("status") or ""),
                str(len(list(item.get("evidence_refs") or []))),
                str(len(list(item.get("missing_materials") or []))),
                short_text(item.get("draft_text"), 200),
            )
        console.print(table)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("export-md-drafts")
def export_md_drafts(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    source: str = typer.Option("auto", "--source"),
    save_index: bool = typer.Option(False, "--save-index"),
) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        if source == "agent_drafts" and not isinstance(project.get("agent_drafts"), dict):
            raise ValueError(f"agent_drafts not found for lot_name={lot_name}; run generate-agent-drafts --save first")
        if source == "writing_plan" and not isinstance(project.get("writing_plan"), dict):
            raise ValueError(f"writing_plan not found for lot_name={lot_name}; run plan-writing --save first")
        if source == "auto" and not isinstance(project.get("agent_drafts"), dict) and not isinstance(project.get("writing_plan"), dict):
            raise ValueError(f"no agent_drafts or writing_plan found for lot_name={lot_name}")

        result = export_markdown_drafts(project, output_dir=output_dir, source=source)
        if save_index:
            save_markdown_drafts_index(lot_name, result)
            print(f"[md-drafts] saved markdown_drafts index for lot_name={lot_name}")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-md-drafts")
def inspect_md_drafts(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        markdown_drafts = project.get("markdown_drafts")
        if not isinstance(markdown_drafts, dict):
            raise ValueError(f"markdown_drafts not found for lot_name={lot_name}; run export-md-drafts --save-index first")

        console.print(
            {
                "lot_name": lot_name,
                "output_dir": markdown_drafts.get("output_dir"),
                "source": markdown_drafts.get("source"),
                "summary": markdown_drafts.get("summary") or {},
            }
        )

        table = Table(title=f"Markdown Drafts: {lot_name}", show_lines=True)
        for column in ["kind", "path", "exists", "size", "preview"]:
            table.add_column(column)

        for item in list(markdown_drafts.get("files") or []):
            path = Path(str(item.get("path") or ""))
            exists = path.exists()
            size = path.stat().st_size if exists else 0
            preview = "-"
            if exists:
                preview = "\n".join(path.read_text(encoding="utf-8", errors="ignore").splitlines()[:6])
            table.add_row(
                str(item.get("kind") or ""),
                str(path),
                "yes" if exists else "no",
                str(size),
                short_text(preview, 240),
            )
        console.print(table)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-parsed-text")
def inspect_parsed_text_command(
    file_id: str,
    head: int = typer.Option(3000, "--head"),
    tail: int = typer.Option(3000, "--tail"),
    find: list[str] | None = typer.Option(None, "--find"),
) -> None:
    try:
        result = inspect_parsed_text_service(file_id, head=head, tail=tail, find_terms=find or [])
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-project-text-sources")
def inspect_project_text_sources_command(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        result = inspect_project_text_sources_service(project)
        console.print(
            {
                "lot_name": result.get("lot_name"),
                "tender_file_id": result.get("tender_file_id"),
                "score_file_id": result.get("score_file_id"),
                "candidate_count": len(list(result.get("candidates") or [])),
            }
        )
        table = Table(title=f"Project Text Sources: {lot_name}", show_lines=True)
        for column in ["file_id", "original_name", "source_folder", "parsed_text_len", "candidate_reason"]:
            table.add_column(column)
        for item in list(result.get("candidates") or []):
            table.add_row(
                str(item.get("file_id") or ""),
                str(item.get("original_name") or ""),
                str(item.get("source_folder") or ""),
                str(item.get("parsed_text_len") or 0),
                short_text(item.get("candidate_reason"), 260),
            )
        console.print(table)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-tender-source-files")
def inspect_tender_source_files_command(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        metadata = MetadataStore().load()
        rows = inspect_tender_source_files_service(lot_name, metadata)
        console.print({"lot_name": lot_name, "tender_source_file_count": len(rows)})
        table = Table(title=f"Tender Source Files: {lot_name}", show_lines=True)
        for column in ["file_id", "source_file_name", "source_type", "priority", "parsed_text_len", "tables_count", "is_full_tender_document"]:
            table.add_column(column)
        for item in rows:
            table.add_row(
                str(item.get("file_id") or ""),
                str(item.get("source_file_name") or ""),
                str(item.get("source_type") or ""),
                str(item.get("priority") or ""),
                str(item.get("parsed_text_len") or 0),
                str(item.get("tables_count") or 0),
                str(item.get("is_full_tender_document")),
            )
        console.print(table)
        print_json({"lot_name": lot_name, "files": rows})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("extract-tender-metadata")
def extract_tender_metadata_command(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        result = extract_tender_metadata_service(lot_name)
        print_json({"ok": True, "lot_name": lot_name, "tender_project_metadata": result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-tender-metadata")
def inspect_tender_metadata_command(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        result = inspect_tender_metadata_service(lot_name)
        fields = result.get("fields") or {}
        console.print({"lot_name": lot_name, "version": result.get("version"), "manual_confirm_items": result.get("manual_confirm_items") or []})
        table = Table(title=f"Tender Metadata: {lot_name}", show_lines=True)
        for column in ["field", "value", "source_type", "source_file", "confidence", "manual_confirm", "evidence"]:
            table.add_column(column)
        for key, item in fields.items():
            table.add_row(
                str(key),
                str(item.get("value") or "-"),
                str(item.get("source_type") or "-"),
                str(item.get("source_file_name") or "-"),
                str(item.get("confidence") or 0),
                str(item.get("requires_manual_confirm")),
                short_text(item.get("evidence"), 160),
            )
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-technical-outline")
def build_technical_outline_command(
    input_dir: Path = typer.Option(..., "--input-dir", help="Directory containing manually selected current-package files."),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    output_key: str | None = typer.Option(
        None,
        "--output-key",
        help="Optional output folder name; defaults to the selected directory name.",
    ),
) -> None:
    try:
        result = build_technical_outline_from_directory(
            input_dir,
            output_dir=output_dir,
            output_key=output_key,
        )
        console.print(f"Markdown源码：{result['files']['markdown']}")
        console.print(f"HTML预览：{result['files']['html_preview']}")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-response-outline")
def build_response_outline_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    save_index: bool = typer.Option(False, "--save-index"),
) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        if not project.get("tender_file_id"):
            raise ValueError(f"tender_file_id not found for lot_name={lot_name}")
        if not isinstance(project.get("material_analysis"), dict):
            raise ValueError(f"material_analysis not found for lot_name={lot_name}; run match-materials --save first")

        result = build_response_outline(project, output_dir=output_dir)
        if save_index:
            save_response_outline_index(lot_name, result)
            print(f"[response-outline] saved response_outline index for lot_name={lot_name}")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-technical-writing-outline")
def build_technical_writing_outline_command(
    company_outline: Path = typer.Option(..., "--company-outline"),
    response_outline: Path = typer.Option(..., "--response-outline"),
    output_key: str = typer.Option(..., "--output-key"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    """Build a project-specific writing structure without drafting content."""
    try:
        result = build_technical_writing_outline_files(
            company_outline,
            response_outline,
            output_key=output_key,
            output_dir=output_dir,
        )
        files = result["files"]
        console.print(f"文章结构JSON：{files['technical_writing_outline_json']}")
        console.print(f"人工审核Markdown：{files['technical_writing_outline_markdown']}")
        console.print(f"适配映射报告：{files['outline_adaptation_report']}")
        console.print(f"章节收敛报告：{files['outline_refinement_report']}")
        console.print(f"公司大纲校验：{files['company_outline_validation']}")
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-technical-section-tasks")
def build_technical_section_tasks_command(
    response_outline: Path = typer.Option(..., "--response-outline"),
    writing_outline: Path = typer.Option(..., "--writing-outline"),
    company_outline: Path = typer.Option(..., "--company-outline"),
    output_key: str = typer.Option(..., "--output-key"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    project_context: Path | None = typer.Option(None, "--project-context"),
) -> None:
    """Plan section-level drafting work without querying sources or drafting text."""
    try:
        result = build_section_task_plan_files(
            response_outline,
            writing_outline,
            company_outline,
            output_key=output_key,
            output_dir=output_dir,
            project_context_path=project_context,
        )
        files = result["files"]
        console.print(f"写作任务JSON：{files['section_writing_tasks_json']}")
        console.print(f"写作任务Markdown：{files['section_writing_tasks_markdown']}")
        console.print(f"项目事实请求：{files['project_fact_request']}")
        console.print(f"本地证据查询计划：{files['local_evidence_query_plan']}")
        console.print(f"外部检索计划：{files['web_research_plan']}")
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("plan-technical-writing-information")
def plan_technical_writing_information_command(
    response_outline: Path = typer.Option(..., "--response-outline"),
    writing_outline: Path = typer.Option(..., "--writing-outline"),
    writing_tasks: Path = typer.Option(..., "--writing-tasks"),
    company_outline: Path = typer.Option(..., "--company-outline"),
    output_key: str = typer.Option(..., "--output-key"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    """Plan information requirements and future draft units without executing retrieval or drafting."""
    try:
        information = build_task_information_files(
            response_outline,
            writing_outline,
            writing_tasks,
            company_outline,
            output_key=output_key,
            output_dir=output_dir,
        )
        enriched_outline = enrich_writing_outline_files(
            writing_outline,
            information["files"]["task_information_requirements_json"],
            writing_tasks,
            markdown_renderer=render_outline_markdown,
        )
        draft_units = build_draft_unit_files(
            writing_outline,
            writing_tasks,
            information["files"]["task_information_requirements_json"],
            output_key=output_key,
            output_dir=output_dir,
        )
        files = {**information["files"], **draft_units["files"]}
        console.print(f"任务信息需求JSON：{files['task_information_requirements_json']}")
        console.print(f"任务信息需求Markdown：{files['task_information_requirements_markdown']}")
        console.print(f"任务就绪矩阵：{files['task_readiness_matrix']}")
        console.print(f"正文生成单元JSON：{files['draft_units_json']}")
        console.print(f"正文生成单元Markdown：{files['draft_units_markdown']}")
        console.print(f"已更新文章结构JSON：{enriched_outline['files']['technical_writing_outline_json']}")
        console.print(f"已更新文章结构Markdown：{enriched_outline['files']['technical_writing_outline_markdown']}")
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-technical-agent-context")
def build_technical_agent_context_command(
    response_outline: Path = typer.Option(..., "--response-outline"),
    writing_outline: Path = typer.Option(..., "--writing-outline"),
    target_chapter_id: str | None = typer.Option(None, "--target-chapter-id"),
    target_section_id: str | None = typer.Option(None, "--target-section-id"),
    confirmed_project_facts: Path | None = typer.Option(None, "--confirmed-project-facts"),
    output_key: str = typer.Option(..., "--output-key"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    """Build one chapter/section's minimal planning context without retrieval or drafting."""
    try:
        result = build_technical_agent_context_files(
            response_outline,
            writing_outline,
            target_chapter_id=target_chapter_id,
            target_section_id=target_section_id,
            confirmed_project_facts_path=confirmed_project_facts,
            output_key=output_key,
            output_dir=output_dir,
        )
        files = result["files"]
        console.print(f"章节Agent上下文JSON：{files['agent_context_json']}")
        console.print(f"章节Agent上下文Markdown：{files['agent_context_markdown']}")
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("run-technical-writing-agent")
def run_technical_writing_agent_command(
    response_outline: Path = typer.Option(..., "--response-outline"),
    writing_outline: Path = typer.Option(..., "--writing-outline"),
    task_information: Path = typer.Option(..., "--task-information"),
    draft_units: Path | None = typer.Option(None, "--draft-units"),
    confirmed_project_facts: Path | None = typer.Option(None, "--confirmed-project-facts"),
    target_chapter_id: str | None = typer.Option(None, "--target-chapter-id"),
    target_section_id: str | None = typer.Option(None, "--target-section-id"),
    output_key: str = typer.Option(..., "--output-key"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    model_profile: str | None = typer.Option(None, "--model-profile"),
    web_search_provider: str | None = typer.Option(None, "--web-search-provider"),
    web_search_policy: str = typer.Option("auto", "--web-search-policy"),
    instruction: str | None = typer.Option(None, "--instruction"),
    instruction_file: Path | None = typer.Option(None, "--instruction-file"),
    interactive: bool = typer.Option(False, "--interactive/--no-interactive"),
    instruction_scope: str | None = typer.Option(None, "--instruction-scope"),
    max_agent_steps: int = typer.Option(12, "--max-agent-steps", min=4, max=100),
    max_search_calls: int = typer.Option(4, "--max-search-calls", min=0, max=20),
    max_local_knowledge_calls: int = typer.Option(3, "--max-local-knowledge-calls", min=0, max=20),
    max_web_search_calls: int = typer.Option(1, "--max-web-search-calls", min=0, max=10),
    max_pending_confirmation_calls: int = typer.Option(8, "--max-pending-confirmation-calls", min=0, max=50),
    disable_web: bool = typer.Option(False, "--disable-web"),
    stream: bool = typer.Option(True, "--stream/--no-stream"),
    stream_format: str = typer.Option("rich", "--stream-format"),
    event_log: Path | None = typer.Option(None, "--event-log"),
    show_model_usage: bool = typer.Option(False, "--show-model-usage"),
    show_result_summary: bool = typer.Option(False, "--show-result-summary"),
    verbose_events: bool = typer.Option(False, "--verbose-events"),
) -> None:
    """Run the autonomous, allow-listed single-section technical writing agent."""
    try:
        instruction_bundle = collect_user_instructions(
            instruction=instruction,
            instruction_file=instruction_file,
            interactive=interactive,
            prompt_fn=typer.echo,
        )
        agent_input = TechnicalWritingAgentInput(
            response_outline_path=response_outline,
            writing_outline_path=writing_outline,
            task_information_path=task_information,
            draft_units_path=draft_units,
            confirmed_project_facts_path=confirmed_project_facts,
            target_chapter_id=target_chapter_id,
            target_section_id=target_section_id,
            output_key=output_key,
            user_instruction=instruction_bundle.text,
            user_instruction_sources=tuple(instruction_bundle.sources),
            user_instruction_summary=instruction_bundle.summary,
            web_search_policy=web_search_policy,
            instruction_scope=instruction_scope or "once",
        )
        agent = build_technical_writing_agent(
            agent_input,
            output_dir=output_dir,
            model_profile=model_profile,
            web_search_provider=web_search_provider,
            disable_web=disable_web,
            max_agent_steps=max_agent_steps,
            max_search_calls=max_search_calls,
            max_local_knowledge_calls=max_local_knowledge_calls,
            max_web_search_calls=max_web_search_calls,
            max_pending_confirmation_calls=max_pending_confirmation_calls,
        )
        selected_profile = getattr(agent.llm, "profile", None)
        if selected_profile is not None:
            capabilities = getattr(selected_profile, "capabilities", None)
            mapping = getattr(selected_profile, "request_mapping", None)
            console.print(f"模型Profile：{selected_profile.name}")
            console.print(f"供应商：{selected_profile.provider}")
            console.print(f"接口类型：{selected_profile.api_style}")
            console.print(f"模型：{selected_profile.model}")
            console.print(
                "工具调用："
                + ("支持" if getattr(capabilities, "supports_tools", False) else "不支持")
            )
            console.print(
                "思考模式："
                + ("已启用" if getattr(mapping, "thinking_enabled", False) else "未启用")
            )
        console.print(
            "Web Search Provider："
            + str(web_search_provider or "ark_builtin")
        )
        selected_event_log = event_log or (
            output_dir / output_key / "drafts" / f"{target_section_id or target_chapter_id}.events.jsonl"
        )
        renderer = (
            TechnicalAgentConsoleRenderer(
                stream_format=stream_format,
                show_model_usage=show_model_usage,
                show_result_summary=show_result_summary,
                verbose_events=verbose_events,
            )
            if stream
            else None
        )
        result = agent.run(
            event_handler=renderer,
            event_log_path=selected_event_log,
            stream_model=stream,
        )
        if (
            result["status"] == "completed"
            and interactive
            and instruction_bundle.present
            and instruction_scope is None
            and result.get("instruction_scope_suggestion") in {"project", "global"}
        ):
            console.print("等待用户确认规则范围：[1] 仅本轮 [2] 当前项目 [3] 全局规则")
            choice = typer.prompt("请选择", default="1").strip()
            confirmed_scope = {"2": "project", "3": "global"}.get(choice)
            if confirmed_scope:
                persistence = persist_confirmed_constraint(
                    instruction=instruction_bundle.text,
                    scope=confirmed_scope,
                    output_root=output_dir,
                    output_key=output_key,
                    rules_path=agent.writing_rules.path if agent.writing_rules else None,
                )
                result["instruction_scope"] = confirmed_scope
                result["instruction_constraint_persistence"] = persistence
                Path(result["outputs"]["run_json"]).write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                console.print(
                    f"规则范围处理：scope={confirmed_scope}，status={persistence.get('status')}"
                )
        if not stream or stream_format != "jsonl":
            if result["outputs"].get("candidate_markdown"):
                console.print(
                    f"候选章节草稿：{result['outputs']['candidate_markdown']}"
                )
                console.print(
                    f"候选结构数据：{result['outputs']['candidate_json']}"
                )
            elif result["outputs"].get("formal_markdown"):
                console.print(
                    f"正式章节草稿：{result['outputs']['formal_markdown']}"
                )
            else:
                console.print("章节草稿：本次运行未生成可用文件")
            console.print(f"运行报告：{result['outputs']['run_json']}")
            console.print(f"事件日志：{result['outputs']['events_jsonl']}")
            console.print(
                f"校验状态：{result.get('validation_status')}；"
                f"问题计数：{result.get('issue_counts')}"
            )
            console.print(f"状态：{result['status']}；停止原因：{result['run_summary']['stop_reason']}")
        if result["status"] != "completed":
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("run-technical-fulltext")
def run_technical_fulltext_command(
    response_outline: Path = typer.Option(..., "--response-outline"),
    writing_outline: Path = typer.Option(..., "--writing-outline"),
    task_information: Path = typer.Option(..., "--task-information"),
    output_key: str = typer.Option(..., "--output-key"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    draft_units: Path | None = typer.Option(None, "--draft-units"),
    confirmed_project_facts: Path | None = typer.Option(None, "--confirmed-project-facts"),
    web_search_policy: str = typer.Option("disabled", "--web-search-policy"),
    stream_model: bool = typer.Option(True, "--stream-model/--no-stream-model"),
) -> None:
    """Produce every standard top-level chapter sequentially, then compose a preview."""
    try:
        service = TechnicalFulltextRunner(
            output_dir=output_dir,
            chapter_event_handler_factory=(
                (lambda _chapter_id: TechnicalAgentConsoleRenderer())
                if stream_model
                else None
            ),
        )
        result = service.run(
            response_outline_path=response_outline,
            writing_outline_path=writing_outline,
            task_information_path=task_information,
            draft_units_path=draft_units,
            confirmed_project_facts_path=confirmed_project_facts,
            output_key=output_key,
            web_search_policy=web_search_policy,
            stream_model=stream_model,
        )
        console.print({
            "status": result.get("status"),
            "production_status": result.get("production_status"),
            "composition_status": result.get("composition_status"),
            "chapter_count_expected": result.get("chapter_count_expected"),
            "chapter_count_completed": result.get("chapter_count_completed"),
            "chapter_count_submission_ready": result.get("chapter_count_submission_ready"),
            "chapter_count_blocked": result.get("chapter_count_blocked"),
            "chapter_count_failed": result.get("chapter_count_failed"),
            "outputs": result.get("outputs"),
            "run_path": result.get("run_path"),
        })
        if result.get("composition_status") == "completed_with_blockers":
            console.print("全文组合已完成，但仍存在未解除的章节 blocker。")
            console.print("本次产物仅用于全文审计和人工审核，不得正式提交。")
        if result.get("composition_status") not in {"complete", "completed_with_blockers"}:
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as exc:
        print("ERROR:", exc)
        raise typer.Exit(code=1)


@cli.command("compose-technical-fulltext")
def compose_technical_fulltext_command(
    manifest: Path = typer.Option(..., "--manifest"),
    output_key: str = typer.Option(..., "--output-key"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    """Validate and combine explicitly listed Candidate/Run pairs without a model."""
    try:
        result = TechnicalFulltextComposer(output_dir=output_dir).compose(
            manifest_path=manifest,
            output_key=output_key,
        )
        console.print({
            "status": result.get("status"),
            "composition_status": result.get("composition_status"),
            "composition_issue_counts": result.get("composition_issue_counts"),
            "outputs": result.get("outputs"),
        })
    except Exception as exc:
        print("ERROR:", exc)
        raise typer.Exit(code=1)


@cli.command("retry-technical-fulltext-chapter")
def retry_technical_fulltext_chapter_command(
    output_key: str = typer.Option(..., "--output-key"),
    chapter_id: str = typer.Option(..., "--chapter-id"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    stream_model: bool = typer.Option(True, "--stream-model/--no-stream-model"),
) -> None:
    """Retry one explicitly failed chapter and recompose the original fulltext run."""
    try:
        service = TechnicalFulltextRunner(
            output_dir=output_dir,
            chapter_event_handler_factory=(
                (lambda _chapter_id: TechnicalAgentConsoleRenderer())
                if stream_model
                else None
            ),
        )
        result = service.retry_failed_chapter(
            output_key=output_key,
            chapter_id=chapter_id,
            stream_model=stream_model,
        )
        console.print({
            "status": result.get("status"),
            "production_status": result.get("production_status"),
            "composition_status": result.get("composition_status"),
            "retry_chapter_id": result.get("retry_chapter_id"),
            "chapter_count_expected": result.get("chapter_count_expected"),
            "chapter_count_completed": result.get("chapter_count_completed"),
            "chapter_count_submission_ready": result.get("chapter_count_submission_ready"),
            "chapter_count_blocked": result.get("chapter_count_blocked"),
            "chapter_count_failed": result.get("chapter_count_failed"),
            "outputs": result.get("outputs"),
            "run_path": result.get("run_path"),
        })
        if result.get("composition_status") == "completed_with_blockers":
            console.print("全文组合已完成，但仍存在未解除的章节 blocker。")
            console.print("本次产物仅用于全文审计和人工审核，不得正式提交。")
        if result.get("composition_status") not in {"complete", "completed_with_blockers"}:
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as exc:
        print("ERROR:", exc)
        raise typer.Exit(code=1)


@cli.command("inspect-technical-fulltext")
def inspect_technical_fulltext_command(
    fulltext_json: Path = typer.Option(..., "--fulltext-json"),
) -> None:
    """Print preview metadata only; this command performs no generation or retrieval."""
    try:
        console.print(inspect_technical_fulltext_service(fulltext_json))
    except Exception as exc:
        print("ERROR:", exc)
        raise typer.Exit(code=1)


@cli.command("audit-technical-fulltext")
def audit_technical_fulltext_command(
    fulltext_json: Path = typer.Option(..., "--fulltext-json"),
) -> None:
    """Initialize Stage ⑪ audit artifacts from one committed fulltext; no model is used."""
    try:
        result = TechnicalFulltextAuditor().audit(
            fulltext_json_path=fulltext_json
        )
        console.print({
            "status": result.get("status"),
            "audit_result": result.get("audit_result"),
            "release_ready": result.get("release_ready"),
            "issue_counts": result.get("issue_counts"),
            "metrics": result.get("metrics"),
            "outputs": result.get("outputs"),
        })
    except Exception as exc:
        print("ERROR:", exc)
        raise typer.Exit(code=1)


@cli.command("inspect-technical-fulltext-audit")
def inspect_technical_fulltext_audit_command(
    audit_json: Path = typer.Option(..., "--audit-json"),
) -> None:
    """Read Stage ⑪ audit metadata only; this command never writes artifacts."""
    try:
        console.print(inspect_technical_fulltext_audit_service(audit_json))
    except Exception as exc:
        print("ERROR:", exc)
        raise typer.Exit(code=1)


@cli.command("run-technical-fulltext-semantic-audit")
def run_technical_fulltext_semantic_audit_command(
    audit_json: Path = typer.Option(..., "--audit-json"),
    model_profile: str = typer.Option(
        DEFAULT_FULLTEXT_SEMANTIC_MODEL_PROFILE,
        "--model-profile",
    ),
) -> None:
    """Run bounded Stage ⑪-C structured adjudication; no tools or rewriting are used."""
    try:
        result = TechnicalFulltextSemanticAuditor(
            model_profile=model_profile
        ).audit(audit_json_path=audit_json)
        console.print({
            "status": result.get("status"),
            "audit_result": result.get("audit_result"),
            "release_ready": result.get("release_ready"),
            "semantic_audit": result.get("semantic_audit"),
            "outputs": result.get("outputs"),
        })
    except Exception as exc:
        print("ERROR:", exc)
        raise typer.Exit(code=1)


@cli.command("finalize-technical-fulltext-audit")
def finalize_technical_fulltext_audit_command(
    audit_json: Path = typer.Option(..., "--audit-json"),
) -> None:
    """Finalize an existing Stage ⑪-C artifact deterministically; no model is used."""
    try:
        result = TechnicalFulltextAuditFinalizer().finalize(
            audit_json_path=audit_json
        )
        console.print({
            "status": result.get("status"),
            "audit_result": result.get("audit_result"),
            "release_ready": result.get("release_ready"),
            "issue_counts": result.get("issue_counts"),
            "finalization": result.get("finalization"),
            "outputs": result.get("outputs"),
        })
    except Exception as exc:
        print("ERROR:", exc)
        raise typer.Exit(code=1)


@cli.command("hygiene-technical-fulltext")
def hygiene_technical_fulltext_command(
    fulltext_json: Path = typer.Option(..., "--fulltext-json"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Detect and validate safe repairs without writing hygiene artifacts.",
    ),
) -> None:
    """Run deterministic Stage 11-E hygiene without changing Stage 10 artifacts."""
    try:
        result = TechnicalFulltextHygiene().run(
            fulltext_json_path=fulltext_json,
            dry_run=dry_run,
        )
        console.print({
            "status": result.get("status"),
            "hygiene_status": result.get("hygiene_status"),
            "human_review_hygiene_ready": result.get(
                "human_review_hygiene_ready"
            ),
            "release_ready": result.get("release_ready"),
            "summary": result.get("summary"),
            "outputs": result.get("outputs"),
        })
    except Exception as exc:
        print("ERROR:", exc)
        raise typer.Exit(code=1)


@cli.command("inspect-technical-fulltext-hygiene")
def inspect_technical_fulltext_hygiene_command(
    hygiene_json: Path = typer.Option(..., "--hygiene-json"),
) -> None:
    """Inspect Stage 11-E metadata without modifying any artifact."""
    try:
        console.print(inspect_technical_fulltext_hygiene_service(hygiene_json))
    except Exception as exc:
        print("ERROR:", exc)
        raise typer.Exit(code=1)


@cli.command("inspect-response-outline")
def inspect_response_outline(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        response_outline = project.get("response_outline")
        if not isinstance(response_outline, dict):
            raise ValueError(f"response_outline not found for lot_name={lot_name}; run build-response-outline --save-index first")

        console.print(
            {
                "lot_name": lot_name,
                "version": response_outline.get("version"),
                "output_dir": response_outline.get("output_dir"),
                "summary": response_outline.get("summary") or {},
                "source_files_used": (response_outline.get("summary") or {}).get("source_files_used") or [],
                "missing_tender_requirement_refs": (response_outline.get("summary") or {}).get("missing_tender_requirement_refs"),
            }
        )

        file_by_kind = {str(item.get("kind") or ""): item for item in list(response_outline.get("files") or [])}
        technical_file = file_by_kind.get("technical_response_outline") or file_by_kind.get("technical_outline")
        debug_file = file_by_kind.get("outline_debug")
        filtered_file = file_by_kind.get("filtered_clause_candidates")
        if technical_file is None:
            raise ValueError("technical_response_outline file entry not found in response_outline index")

        filtered_summary = load_filtered_clause_candidate_summary(filtered_file)
        if filtered_summary:
            console.print({"filtered_clause_candidates": filtered_summary})

        table = Table(title=f"Response Outline: {lot_name}", show_lines=True)
        for column in ["kind", "path", "exists", "size", "preview"]:
            table.add_column(column)

        for kind, item in [("technical_response_outline", technical_file), ("outline_debug", debug_file), ("filtered_clause_candidates", filtered_file)]:
            if not item:
                continue
            path = Path(str(item.get("path") or ""))
            exists = path.exists()
            preview = "-"
            size = 0
            if exists:
                size = path.stat().st_size
                preview = "\n".join(path.read_text(encoding="utf-8", errors="ignore").splitlines()[:12])
            table.add_row(kind, str(path), "yes" if exists else "no", str(size), short_text(preview, 500))
        console.print(table)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-scoring-action-plan")
def inspect_scoring_action_plan(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = inspect_scoring_action_plan_service(lot_name, output_dir=output_dir)
        console.print(
            {
                "lot_name": result.get("lot_name"),
                "version": result.get("version"),
                "files": result.get("files"),
                "summary": result.get("summary"),
            }
        )
        table = Table(title=f"Scoring Action Plan: {lot_name}", show_lines=True)
        for column in ["section", "score_item", "target", "materials", "missing_actions", "external_refs"]:
            table.add_column(column)
        for item in list(result.get("sections") or []):
            material_status = item.get("material_status") or {}
            table.add_row(
                short_text(str(item.get("section_title") or "-"), 60),
                short_text(str(item.get("score_item") or "-"), 60),
                short_text(str((item.get("max_score_preparation") or {}).get("target") or "-"), 80),
                ", ".join(f"{key}={value}" for key, value in material_status.items()) or "-",
                str(len(list(item.get("missing_actions") or []))),
                str(len(list(item.get("external_references") or []))),
            )
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-score-maximization-matrix")
def build_score_maximization_matrix(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        result = build_score_maximization_matrix_service(lot_name, output_dir=output_dir, project=project)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-tender-requirement-matrix")
def build_tender_requirement_matrix_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        result = build_tender_requirement_matrix_service(lot_name, output_dir=output_dir, project=project)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-tender-requirement-matrix")
def inspect_tender_requirement_matrix_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = inspect_tender_requirement_matrix_service(lot_name, output_dir=output_dir)
        statistics = result.get("statistics") or {}
        console.print(
            {
                "lot_name": lot_name,
                "requirement_count": statistics.get("requirement_count", 0),
                "requirement_type_counts": statistics.get("requirement_type_counts") or {},
                "compliance_level_counts": statistics.get("compliance_level_counts") or {},
                "risk_type_counts": statistics.get("risk_type_counts") or {},
                "human_review_count": result.get("human_review_count", 0),
                "chapter_coverage": result.get("chapter_coverage") or {},
            }
        )
        table = Table(title=f"Tender Requirement Matrix: {lot_name}", show_lines=True)
        for column in ["level", "requirement_id", "title", "risk_type", "source"]:
            table.add_column(column)
        for level, rows in [
            ("fatal_if_missing", result.get("fatal_if_missing") or []),
            ("must_respond", result.get("must_respond") or []),
        ]:
            for item in rows:
                source = item.get("source_ref") or {}
                table.add_row(
                    level,
                    str(item.get("requirement_id") or "-"),
                    short_text(str(item.get("title") or "-"), 60),
                    str(item.get("risk_type") or "-"),
                    short_text(f"{source.get('source_file_name') or '-'} / {source.get('clause_path') or '-'}", 80),
                )
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-actionable-requirement-index")
def build_actionable_requirement_index_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = build_actionable_requirement_index_service(lot_name, output_dir=output_dir)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-actionable-requirement-index")
def inspect_actionable_requirement_index_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = inspect_actionable_requirement_index_service(lot_name, output_dir=output_dir)
        statistics = result.get("statistics") or {}
        console.print(
            {
                "lot_name": lot_name,
                "raw_requirement_count": statistics.get("raw_requirement_count", 0),
                "action_group_count": statistics.get("action_group_count", 0),
                "merged_requirement_count": statistics.get("merged_requirement_count", 0),
                "action_kind_counts": statistics.get("action_kind_counts") or {},
                "action_dispatch_counts": statistics.get("action_dispatch_counts") or {},
                "action_priority_counts": statistics.get("action_priority_counts") or {},
                "human_review_count": result.get("human_review_count", 0),
                "chapter_coverage": result.get("chapter_coverage") or {},
            }
        )
        table = Table(title=f"Actionable Requirement Index: {lot_name}", show_lines=True)
        for column in ["category", "action_group_id", "title", "priority", "members", "chapters"]:
            table.add_column(column)
        for category in ["provide_evidence", "write_response", "fill_form", "critical"]:
            for item in list(result.get(category) or []):
                table.add_row(
                    category,
                    str(item.get("action_group_id") or "-"),
                    short_text(str(item.get("canonical_title") or "-"), 60),
                    str(item.get("action_priority") or "-"),
                    str(len(item.get("member_requirement_ids") or [])),
                    ", ".join(item.get("related_context_chapter_ids") or []) or "-",
                )
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-evidence-coverage")
def build_evidence_coverage_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = build_evidence_coverage_service(lot_name, output_dir=output_dir)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-evidence-coverage")
def inspect_evidence_coverage_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = inspect_evidence_coverage_service(lot_name, output_dir=output_dir)
        statistics = result.get("statistics") or {}
        console.print(
            {
                "lot_name": lot_name,
                "evidence_requirement_count": statistics.get("evidence_requirement_count", 0),
                "enterprise_evidence_count": statistics.get("enterprise_evidence_count", 0),
                "support_relation_count": statistics.get("support_relation_count", 0),
                "usability_status_counts": statistics.get("usability_status_counts") or {},
                "support_strength_counts": statistics.get("support_strength_counts") or {},
                "evidence_type_counts": result.get("evidence_type_counts") or {},
                "missing_evidence_request_count": result.get("missing_evidence_request_count", 0),
                "manual_review_count": result.get("manual_review_count", 0),
                "evidence_package_completeness": result.get("evidence_package_completeness") or {},
                "evidence_without_location_or_excerpt_count": result.get("evidence_without_location_or_excerpt_count", 0),
            }
        )
        table = Table(title=f"Evidence Coverage: {lot_name}", show_lines=True)
        for column in ["score_item", "required", "confirmed", "candidate", "remaining", "full_score_writing"]:
            table.add_column(column)
        for item in list(result.get("score_item_coverage") or []):
            table.add_row(
                short_text(str(item.get("score_item_name") or item.get("score_item_id") or "-"), 56),
                str(item.get("required_count") if item.get("required_count") is not None else "not_explicit"),
                str(item.get("confirmed_count") or 0),
                str(item.get("candidate_count") or 0),
                str(item.get("remaining_confirmed_gap") if item.get("remaining_confirmed_gap") is not None else "not_explicit"),
                str(item.get("can_support_full_score_writing") or "unknown"),
            )
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-evidence-resolution-queue")
def build_evidence_resolution_queue_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = build_evidence_resolution_queue_service(lot_name, output_dir=output_dir)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-evidence-resolution-queue")
def inspect_evidence_resolution_queue_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = inspect_evidence_resolution_queue_service(lot_name, output_dir=output_dir)
        statistics = result.get("statistics") or {}
        console.print(
            {
                "lot_name": lot_name,
                "raw_manual_review_count": statistics.get("raw_manual_review_count", 0),
                "resolution_case_count": statistics.get("resolution_case_count", 0),
                "subject_type_counts": statistics.get("subject_type_counts") or {},
                "status_counts": statistics.get("status_counts") or {},
                "blocking_level_counts": statistics.get("blocking_level_counts") or {},
                "confirmation_status_by_evidence_type": result.get("confirmation_status_by_evidence_type") or {},
                "confirmation_traceability": result.get("confirmation_traceability") or {},
                "person_confirmation_status": result.get("person_confirmation_status") or {},
                "certificate_confirmation_status": result.get("certificate_confirmation_status") or {},
                "project_package_confirmation_status": result.get("project_package_confirmation_status") or {},
                "material_intake_statistics": result.get("material_intake_statistics") or {},
                "confirmed_change": result.get("confirmed_change") or {},
            }
        )
        table = Table(title=f"Evidence Resolution Queue: {lot_name}", show_lines=True)
        for column in ["owner", "intake_package", "priority", "status"]:
            table.add_column(column)
        for owner, items in (result.get("intake_tasks_by_owner") or {}).items():
            for item in items:
                table.add_row(
                    str(owner),
                    short_text(str(item.get("title") or item.get("intake_package_id") or "-"), 60),
                    str(item.get("priority") or "-"),
                    str(item.get("intake_status") or "-"),
                )
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("apply-evidence-review-decisions")
def apply_evidence_review_decisions_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    input_path: Path = typer.Option(..., "--input", exists=True, file_okay=True, dir_okay=False),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and preview without writing decisions or rebuilding coverage."),
) -> None:
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        evidence_dir = output_dir / lot_name / "evidence"
        queue = load_json_file(evidence_dir / "evidence_resolution_queue.json")
        catalog = load_json_file(evidence_dir / "enterprise_evidence_catalog.json")
        saved = apply_evidence_review_decisions_service(
            lot_name,
            payload,
            queue,
            dry_run=dry_run,
            catalog=catalog,
        )
        if dry_run:
            print_json({"decision_import": saved, "coverage_rebuild": "skipped_in_dry_run"})
            return
        rebuilt = build_evidence_coverage_service(lot_name, output_dir=output_dir)
        print_json({"decision_import": saved, "coverage_rebuild": rebuilt})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-writing-context")
def build_writing_context_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = build_writing_context_service(lot_name, output_dir=output_dir)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-writing-context")
def inspect_writing_context_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = inspect_writing_context_service(lot_name, output_dir=output_dir)
        console.print(
            {
                "lot_name": lot_name,
                "statistics": result.get("statistics") or {},
                "evidence_statistics": result.get("evidence_statistics") or {},
                "fatal_risk_count": result.get("fatal_risk_count", 0),
                "mandatory_requirement_count": result.get("mandatory_requirement_count", 0),
                "unresolved_resolution_count": result.get("unresolved_resolution_count", 0),
            }
        )
        table = Table(title=f"Writing Context: {lot_name}", show_lines=True)
        for column in ["chapter", "task_type", "blocking_status", "scores", "requirements", "placeholders"]:
            table.add_column(column)
        for item in list(result.get("chapters") or []):
            table.add_row(
                short_text(str(item.get("chapter_title") or "-"), 48),
                str(item.get("task_type") or "-"),
                str(item.get("blocking_status") or "-"),
                str(len(item.get("score_item_ids") or [])),
                str(len(item.get("requirement_ids") or [])),
                str(item.get("placeholder_count") or 0),
            )
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-technical-draft")
def build_technical_draft_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    profile: str | None = typer.Option(None, "--profile", help="Override the drafting role profile."),
    preview: bool = typer.Option(
        False,
        "--preview",
        help="Generate a deterministic test preview without calling a model.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate inputs and assemble chapter prompts without calling a model or writing draft artifacts.",
    ),
) -> None:
    try:
        result = build_technical_draft_service(
            lot_name,
            output_dir=output_dir,
            dry_run=dry_run,
            preview=preview,
            profile_name=profile,
        )
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-technical-draft-pilot")
def build_technical_draft_pilot_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    chapter_id: str = typer.Option(..., "--chapter-id"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    draft_profile: str | None = typer.Option(None, "--draft-profile", help="Override the drafting role profile."),
    normalize_profile: str | None = typer.Option(
        None,
        "--normalize-profile",
        help="Override the fast_task normalization profile.",
    ),
) -> None:
    try:
        result = build_technical_draft_pilot_service(
            lot_name,
            chapter_id,
            output_dir=output_dir,
            draft_profile_name=draft_profile,
            normalize_profile_name=normalize_profile,
        )
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-technical-draft-pilot")
def inspect_technical_draft_pilot_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = inspect_technical_draft_pilot_service(lot_name, output_dir=output_dir)
        console.print(
            {
                "chapter_id": result.get("chapter_id"),
                "chapter_title": result.get("chapter_title"),
                "pilot_status": result.get("pilot_status"),
                "deep_stage": result.get("deep_stage"),
                "normalize_stage": result.get("normalize_stage"),
                "placeholder_preservation": result.get("placeholder_preservation"),
                "score_item_coverage": result.get("score_item_coverage"),
                "requirement_coverage": result.get("requirement_coverage"),
                "unsupported_claim_count": result.get("unsupported_claim_count"),
                "content_fidelity": result.get("content_fidelity"),
            }
        )
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("list-llm-profiles")
def list_llm_profiles_command() -> None:
    try:
        profiles = list_profile_summaries()
        table = Table(title="LLM Profiles", show_lines=True)
        for column in ["profile", "provider", "protocol", "model", "api_mode", "structured", "enabled", "roles"]:
            table.add_column(column)
        for item in profiles:
            table.add_row(
                str(item.get("profile_name") or "-"),
                str(item.get("provider") or "-"),
                str(item.get("protocol") or "-"),
                "configured" if item.get("model_configured") else "missing",
                str(item.get("api_mode") or "-"),
                str(item.get("structured_output_mode") or "-"),
                str(bool(item.get("enabled"))).lower(),
                ", ".join(item.get("roles") or []),
            )
        console.print(table)
        print_json({"profiles": profiles})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("check-drafting-model")
def check_drafting_model_command(
    profile: str | None = typer.Option(None, "--profile", help="Override the drafting role profile."),
    smoke_mode: str = typer.Option(
        "minimal",
        "--smoke-mode",
        help="Smoke contract: minimal or chapter-schema.",
    ),
    thinking: str | None = typer.Option(
        None,
        "--thinking",
        help="Temporary chapter-schema thinking mode: enabled or disabled.",
    ),
    timeout_seconds: float | None = typer.Option(
        None,
        "--timeout-seconds",
        help="Temporary chapter-schema timeout; does not update the profile.",
    ),
    max_output_tokens: int | None = typer.Option(
        None,
        "--max-output-tokens",
        help="Temporary smoke output limit; does not update the profile.",
    ),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    no_write: bool = typer.Option(False, "--no-write", help="Run the check without saving the result."),
) -> None:
    try:
        result = check_drafting_model_service(
            profile_name=profile,
            output_dir=output_dir,
            write_result=not no_write,
            smoke_mode=smoke_mode,
            thinking=thinking,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-llm-request")
def inspect_llm_request_command(
    profile: str | None = typer.Option(None, "--profile", help="Override the drafting role profile."),
    smoke_test: bool = typer.Option(False, "--smoke-test", help="Preview the safe drafting smoke request."),
    smoke_mode: str = typer.Option("minimal", "--smoke-mode", help="Smoke contract: minimal or chapter-schema."),
    thinking: str | None = typer.Option(None, "--thinking", help="Temporary smoke thinking mode."),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds", help="Temporary smoke timeout."),
    max_output_tokens: int | None = typer.Option(None, "--max-output-tokens", help="Temporary smoke output limit."),
) -> None:
    try:
        if not smoke_test:
            raise ValueError("only --smoke-test request preview is supported in this phase")
        result = inspect_drafting_request_service(
            profile_name=profile,
            smoke_mode=smoke_mode,
            thinking=thinking,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-technical-draft")
def inspect_technical_draft_command(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = inspect_technical_draft_service(lot_name, output_dir=output_dir)
        report = result.get("report") or {}
        console.print(
            {
                "lot_name": lot_name,
                "draft_mode": result.get("draft_mode"),
                "generation_status_counts": (result.get("statistics") or {}).get("generation_status_counts") or {},
                "placeholder_count": report.get("placeholder_count", 0),
                "confirmed_fact_claim_count": report.get("confirmed_enterprise_fact_claim_count", 0),
                "proposal_statement_count": report.get("proposal_statement_count", 0),
                "unsupported_claim_count": len(report.get("unsupported_claims_detected") or []),
                "prohibited_evidence_attempt_count": len(report.get("prohibited_evidence_attempts") or []),
            }
        )
        table = Table(title=f"Technical Draft: {lot_name}", show_lines=True)
        for column in ["chapter", "task_type", "blocking", "generation", "chars", "scores", "requirements", "placeholders"]:
            table.add_column(column)
        for item in list(result.get("chapters") or []):
            requirement_coverage = item.get("requirement_coverage") or {}
            table.add_row(
                short_text(str(item.get("chapter_title") or "-"), 42),
                str(item.get("task_type") or "-"),
                str(item.get("blocking_status") or "-"),
                str(item.get("generation_status") or "-"),
                str(item.get("character_count") or 0),
                str(len(item.get("score_items") or [])),
                f"{requirement_coverage.get('covered', 0)}/{requirement_coverage.get('total', 0)}",
                str(len(item.get("placeholder_ids") or [])),
            )
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-score-material-outputs")
def build_score_material_outputs(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        result = build_score_material_outputs_service(lot_name, output_dir=output_dir, project=project)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-score-maximization-matrix")
def inspect_score_maximization_matrix(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = inspect_score_maximization_matrix_service(lot_name, output_dir=output_dir)
        console.print(
            {
                "lot_name": result.get("lot_name"),
                "files": result.get("files"),
                "summary": result.get("summary"),
                "manual_review_summary": result.get("manual_review_summary"),
            }
        )
        table = Table(title=f"Score Maximization Matrix: {lot_name}", show_lines=True)
        for column in ["section", "score_item", "goal", "kb_status", "review_table", "request_to"]:
            table.add_column(column)
        for item in list(result.get("items") or []):
            status = item.get("current_kb_status") or {}
            table.add_row(
                short_text(str(item.get("section_title") or "-"), 50),
                short_text(str(item.get("score_item") or "-"), 50),
                short_text(str(item.get("max_preparation_goal") or "-"), 80),
                ", ".join(f"{key}={value}" for key, value in status.items()) or "-",
                str(item.get("manual_review_table_id") or "-"),
                short_text(str(item.get("request_to") or "-"), 50),
            )
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-missing-material-requests")
def inspect_missing_material_requests(
    lot_name: str = typer.Option(..., "--lot-name"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    try:
        result = inspect_missing_material_requests_service(lot_name, output_dir=output_dir)
        console.print(
            {
                "lot_name": result.get("lot_name"),
                "files": result.get("files"),
                "summary": result.get("summary"),
            }
        )
        table = Table(title=f"Missing Material Requests: {lot_name}", show_lines=True)
        for column in ["request_to", "count"]:
            table.add_column(column)
        for item in list(result.get("request_to") or []):
            table.add_row(str(item.get("name") or "-"), str(item.get("count") or 0))
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


def load_filtered_clause_candidate_summary(file_entry: dict | None) -> dict:
    if not isinstance(file_entry, dict):
        return {}
    path = Path(str(file_entry.get("path") or ""))
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8") or "[]")
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    reason_counts: dict[str, int] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("filter_reason") or item.get("reason") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    examples = [
        {
            "section_title": item.get("section_title"),
            "filter_reason": item.get("filter_reason"),
            "source_file_name": item.get("source_file_name"),
            "clause_type": item.get("clause_type"),
            "clause_path": item.get("clause_path"),
        }
        for item in rows[:5]
        if isinstance(item, dict)
    ]
    return {"count": len(rows), "reason_counts": dict(sorted(reason_counts.items())), "examples": examples}


@cli.command("inspect-embedded-attachments")
def inspect_embedded_attachments_command(file_id: str) -> None:
    try:
        result = inspect_embedded_attachments_service(file_id)
        console.print(
            {
                "file_id": file_id,
                "has_embedded_attachments": result.get("has_embedded_attachments"),
                "embedded_objects_count": result.get("embedded_objects_count"),
                "external_links_count": result.get("external_links_count"),
                "requires_manual_upload": result.get("requires_manual_upload"),
                "requires_manual_review": result.get("requires_manual_review"),
                "note": result.get("note") or "",
            }
        )
        if result.get("requires_manual_upload"):
            console.print("检测到疑似链接附件或图标占位，当前未找到可直接抽取的内嵌文件；请人工上传真实附件文件。")
        table = Table(title=f"Embedded Attachments: {file_id}", show_lines=True)
        for column in ["kind", "path", "relationship_id", "target", "target_mode", "can_extract", "manual_upload", "note"]:
            table.add_column(column)
        for item in list(result.get("items") or []):
            table.add_row(
                str(item.get("kind") or ""),
                str(item.get("path") or ""),
                str(item.get("relationship_id") or "-"),
                str(item.get("target") or "-"),
                str(item.get("target_mode") or "-"),
                str(item.get("can_extract")),
                str(item.get("requires_manual_upload")),
                short_text(item.get("note"), 120),
            )
        console.print(table)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("extract-embedded-attachments")
def extract_embedded_attachments_command(
    file_id: str,
    ingest: bool = typer.Option(False, "--ingest"),
) -> None:
    try:
        result = extract_embedded_attachments_service(file_id, ingest=ingest)
        console.print(
            {
                "parent_file_id": result.get("parent_file_id"),
                "source_file_name": result.get("source_file_name"),
                "extracted_count": result.get("extracted_count"),
                "manual_action_required": result.get("manual_action_required"),
                "output_dir": result.get("output_dir"),
                "ingest": result.get("ingest"),
            }
        )
        table = Table(title=f"Extracted Embedded Attachments: {file_id}", show_lines=True)
        for column in ["index", "original_name", "path", "child_file_id", "category", "method"]:
            table.add_column(column)
        for item in list(result.get("extracted_files") or []):
            table.add_row(
                str(item.get("embedded_attachment_index") or ""),
                str(item.get("original_name") or item.get("extracted_file_name") or ""),
                str(item.get("extracted_path") or ""),
                str(item.get("child_file_id") or "-"),
                str(item.get("category") or ""),
                str(item.get("extraction_method") or ""),
            )
        console.print(table)
        if result.get("failed_items"):
            for item in result.get("failed_items") or []:
                console.print(item.get("manual_action") or item)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("classify-kb-material")
def classify_kb_material(file_id: str, force: bool = typer.Option(False, "--force")) -> None:
    try:
        result = classify_kb_material_file(file_id, force=force)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("classify-all-kb-materials")
def classify_all_kb_materials(force: bool = typer.Option(False, "--force")) -> None:
    try:
        result = classify_all_kb_material_files(force=force)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-kb-material")
def inspect_kb_material(file_id: str) -> None:
    try:
        result = inspect_material_profile(file_id)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("list-kb-materials")
def list_kb_materials(
    group: str | None = typer.Option(None, "--group"),
    material_type: str | None = typer.Option(None, "--type"),
    category: str | None = typer.Option(None, "--category"),
) -> None:
    try:
        rows = list_material_profiles(group=group, material_type=material_type, category=category)
        table = Table(title=f"KB Materials ({len(rows)} files)", show_lines=True)
        for column in ["file_id", "original_name", "category", "material_group", "material_types", "confidence", "manual_confirm"]:
            table.add_column(column)
        for item in rows:
            table.add_row(
                str(item.get("file_id") or ""),
                str(item.get("original_name") or ""),
                str(item.get("category") or ""),
                str(item.get("material_group") or ""),
                compact_list(item.get("material_types"), empty="-"),
                str(item.get("confidence") or ""),
                str(item.get("requires_manual_confirm")),
            )
        console.print(table)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("list-department-profiles")
def list_department_profiles_command() -> None:
    try:
        profiles = load_department_profiles()
        table = Table(title=f"Department Profiles ({len(profiles)})", show_lines=True)
        for column in ["department", "business_scope", "products_services", "suitable_project_types", "keywords", "notes"]:
            table.add_column(column)
        for item in profiles:
            table.add_row(
                str(item.get("department") or ""),
                compact_list(item.get("business_scope"), empty="-"),
                compact_list(item.get("products_services"), empty="-"),
                compact_list(item.get("suitable_project_types"), empty="-"),
                compact_list(item.get("keywords"), empty="-"),
                compact_list(item.get("notes"), empty="-"),
            )
        console.print(table)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-department-profile")
def inspect_department_profile(department: str) -> None:
    try:
        print_json(inspect_department_profile_service(department))
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("match-project-departments")
def match_project_departments(lot_name: str = typer.Option(..., "--lot-name")) -> None:
    try:
        projects_data = load_tender_projects()
        project = find_project_by_lot_name(projects_data, lot_name)
        metadata = load_outline_metadata()
        tender_file = find_outline_file_by_id(metadata, project.get("tender_file_id"), "tender_file_id")
        source_files = select_outline_source_files(project, metadata, tender_file)
        tender_text = " ".join(str(item.get("parsed_text") or "") for item in source_files if item.get("id") == project.get("tender_file_id"))
        technical_spec_text = " ".join(str(item.get("parsed_text") or "") for item in source_files if item.get("id") != project.get("tender_file_id"))
        requirement_profile = build_project_requirement_profile(tender_text, list(project.get("score_items") or []), technical_spec_text)
        result = match_project_to_departments(requirement_profile, load_department_profiles())

        console.print(
            {
                "lot_name": lot_name,
                "primary_departments": result.get("primary_departments"),
                "secondary_departments": result.get("secondary_departments"),
                "match_source": result.get("match_source"),
                "used_profile_fields": result.get("used_profile_fields"),
                "routing_notes": result.get("routing_notes"),
                "requires_manual_confirm": result.get("requires_manual_confirm"),
            }
        )
        table = Table(title=f"Project Department Match: {lot_name}", show_lines=True)
        for column in ["department", "score", "source", "fields", "matched_terms", "routing_notes"]:
            table.add_column(column)
        for item in list(result.get("candidate_departments") or []):
            table.add_row(
                str(item.get("department") or ""),
                str(item.get("score") or 0),
                str(item.get("match_source") or ""),
                compact_list(item.get("used_profile_fields"), empty="-"),
                compact_list(item.get("matched_terms"), empty="-"),
                compact_list(item.get("routing_notes"), empty="-"),
            )
        console.print(table)
        print_json({"lot_name": lot_name, "project_requirement_profile": requirement_profile, "department_match": result})
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("classify-personnel")
def classify_personnel(file_id: str, force: bool = typer.Option(False, "--force")) -> None:
    try:
        result = classify_personnel_file(file_id, force=force)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("classify-all-personnel")
def classify_all_personnel(force: bool = typer.Option(False, "--force")) -> None:
    try:
        result = classify_all_personnel_files(force=force)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("set-person-department")
def set_person_department(file_id: str, department: str = typer.Option(..., "--department")) -> None:
    try:
        result = set_person_department_service(file_id, department)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("list-personnel")
def list_personnel(department: str | None = typer.Option(None, "--department")) -> None:
    try:
        rows = list_personnel_profiles(department=department)
        table = Table(title=f"Personnel ({len(rows)} files)", show_lines=True)
        for column in ["file_id", "name", "department", "status", "years", "skills", "certificates", "project_keywords"]:
            table.add_column(column)
        for item in rows:
            table.add_row(
                str(item.get("file_id") or ""),
                str(item.get("person_name") or ""),
                str(item.get("department") or ""),
                str(item.get("department_confirm_status") or ""),
                str(item.get("work_years") or ""),
                compact_list(item.get("skill_tags"), empty="-"),
                compact_list(item.get("certificate_tags"), empty="-"),
                compact_list(item.get("project_keywords"), empty="-"),
            )
        console.print(table)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-personnel")
def inspect_personnel(file_id: str) -> None:
    try:
        result = inspect_personnel_profile(file_id)
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("match-task-materials")
def match_task_materials(
    project_index: int = typer.Option(-1, "--project-index"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    try:
        project = load_project(project_index)
        result = match_materials_for_project(project, limit=limit)
        output_path = DATA_DIR / "task_material_matches.json"
        save_json_file(output_path, result)
        print(f"[task-materials] total_tasks={result.get('total_tasks', 0)}")
        print(f"[task-materials] matched_count={result.get('matched_count', 0)}")
        print(f"[task-materials] saved: {output_path}")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("analyze-material-gaps")
def analyze_material_gaps(
    project_index: int = typer.Option(-1, "--project-index"),
    match_file: Path = typer.Option(DATA_DIR / "task_material_matches.json", "--match-file"),
) -> None:
    try:
        project = load_project(project_index)
        match_result = load_json_file(match_file)
        result = analyze_material_gaps_for_project(project, match_result)
        output_path = DATA_DIR / "material_gap_analysis.json"
        save_json_file(output_path, result)
        print(f"[material-gaps] total_tasks={result.get('total_tasks', 0)}")
        print(f"[material-gaps] summary={result.get('summary', {})}")
        print(f"[material-gaps] saved: {output_path}")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("build-response-matrix")
def build_response_matrix(
    project_index: int = typer.Option(-1, "--project-index"),
    match_file: Path = typer.Option(DATA_DIR / "task_material_matches.json", "--match-file"),
    gap_file: Path = typer.Option(DATA_DIR / "material_gap_analysis.json", "--gap-file"),
) -> None:
    try:
        project = load_project(project_index)
        match_result = load_json_file(match_file)
        gap_result = load_json_file(gap_file)
        result = build_response_matrix_for_project(project, match_result, gap_result)
        output_path = DATA_DIR / "response_matrix.json"
        save_json_file(output_path, result)
        print(f"[response-matrix] total_items={result.get('total_items', 0)}")
        print(f"[response-matrix] summary={result.get('summary', {})}")
        print(f"[response-matrix] saved: {output_path}")
        print_json(result)
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("inspect-response-matrix")
def inspect_response_matrix(
    matrix_file: Path = typer.Option(DATA_DIR / "response_matrix.json", "--matrix-file"),
    status: str | None = typer.Option(None, "--status"),
    task_type: str | None = typer.Option(None, "--task-type"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    try:
        matrix = load_json_file(matrix_file)
        items = list(matrix.get("items") or [])

        if status:
            items = [item for item in items if str(item.get("status") or "") == status]
        if task_type:
            items = [item for item in items if str(item.get("task_type") or "") == task_type]
        if isinstance(limit, int) and limit >= 0:
            items = items[:limit]

        inspect_console = Console(width=240)
        table = Table(title=f"Response Matrix Inspection ({len(items)} items)", show_lines=True)
        for column in [
            "task_id",
            "task_type",
            "target_section",
            "status",
            "readiness",
            "risk_level",
            "matched_material_count",
            "covered_evidence",
            "missing_evidence",
            "recommended_writer_agent",
            "top_matched_materials",
        ]:
            table.add_column(column)

        for item in items:
            matched_materials = list(item.get("matched_materials") or [])
            table.add_row(
                str(item.get("task_id") or ""),
                str(item.get("task_type") or ""),
                str(item.get("target_section") or ""),
                str(item.get("status") or ""),
                str(item.get("readiness") or ""),
                str(item.get("risk_level") or ""),
                str(len(matched_materials)),
                compact_list(item.get("covered_evidence")),
                compact_list(item.get("missing_evidence")),
                str(item.get("recommended_writer_agent") or ""),
                format_top_materials(matched_materials),
            )

        inspect_console.print(table)
        inspect_console.print(
            {
                "matrix_file": str(matrix_file),
                "status_filter": status,
                "task_type_filter": task_type,
                "shown": len(items),
                "summary": matrix.get("summary", {}),
            }
        )
    except Exception as e:
        print("ERROR:", e)
        raise typer.Exit(code=1)


@cli.command("generate-section")
def generate_section(section: str = typer.Option(..., "--section"), query: str = typer.Option(..., "--query")) -> None:
    print_json(SectionService().generate_section(section, query))


@cli.command("review-section")
def review_section(section: str = typer.Option(..., "--section")) -> None:
    print_json({"section": section, "risks": ReviewService().review_section(section)})


if __name__ == "__main__":
    cli()
