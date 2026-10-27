from pathlib import Path
from typing import Any
from datetime import datetime
from copy import deepcopy
import json
import uuid

from app.document_parser import parse_document
from app.document_splitter import split_document
from app.file_classifier import classify_file as classify_file_record
from app.file_store import ingest_file
from app.llm_mock import mock_tender_parse
from app.tender_project_builder import build_tender_project_graph
from app.metadata_store import MetadataStore
from app.score_table_locator import build_score_items_from_location, locate_score_table
from app.vector_store import VectorStore, chunk_text
from app.source_package_service import SourcePackageService, apply_project_profile_to_score_parse
from config import BACKEND_ROOT, DATA_DIR, INGEST_ALLOWED_EXTENSIONS, SOURCE_PACKAGE_ARCHIVE_EXTENSIONS


class KnowledgeBaseService:
    def __init__(self, metadata: MetadataStore | None = None) -> None:
        self.metadata = metadata or MetadataStore()

    def ingest_file(self, path: Path, category: str, tags: list[str]) -> dict[str, Any]:
        record = ingest_file(path, category, tags)
        data = dump_model(record)
        self.metadata.add_file(data)
        return data

    def parse_file(self, file_id: str) -> dict[str, Any]:
        record = self.metadata.get_file(file_id)
        parse_result = parse_document(record)
        patch: dict[str, Any] = {
            "parse_ok": parse_result.get("ok", False),
            "parse_message": parse_result.get("message", ""),
            "sections": parse_result.get("sections", []),
            "tables": parse_result.get("tables", []),
            "assets": [dump_model(asset) for asset in parse_result.get("assets", [])],
            "warnings": parse_result.get("warnings", []),
        }
        patch["parsed_text"] = parse_result.get("parsed_text", "") if parse_result.get("ok") else ""
        if not parse_result.get("ok"):
            patch["failed_parse_text"] = parse_result.get("parsed_text", "")
            if record.get("parsed_text"):
                patch["previous_parsed_text"] = record["parsed_text"]
        updated = self.metadata.update_file(file_id, patch)
        return {
            "ok": parse_result.get("ok", False),
            "file_id": file_id,
            "file_type": parse_result.get("file_type", record.get("file_ext")),
            "parsed_text": updated.get("parsed_text", ""),
            "sections": updated.get("sections", []),
            "tables": updated.get("tables", []),
            "assets": updated.get("assets", []),
            "warnings": updated.get("warnings", []),
            "message": updated.get("parse_message", ""),
            "record": updated,
        }

    def split_file(self, file_id: str) -> dict[str, Any]:
        record = self.metadata.get_file(file_id)
        replaced_count = len(record.get("detected_items", []) or [])
        items = [dump_model(item) for item in split_document(record)]
        updated = self.metadata.update_file(file_id, {"detected_items": items})
        return {
            **updated,
            "replaced_detected_items_count": replaced_count,
            "detected_items_count": len(items),
        }

    def classify_file(self, file_id: str) -> dict[str, Any]:
        record = self.metadata.get_file(file_id)
        if not (record.get("parsed_text") or "").strip():
            raise ValueError("parsed_text is empty; run parse-file first")
        classification = dump_model(classify_file_record(record))
        self.metadata.update_file(file_id, {"classification": classification})
        return classification

    def ingest_auto(self, path: Path) -> dict[str, Any]:
        if path.is_file() and path.suffix.lower().lstrip(".") in SOURCE_PACKAGE_ARCHIVE_EXTENSIONS:
            package_service = SourcePackageService(output_root=BACKEND_ROOT / "outputs")
            package = package_service.discover(path)
            package = package_service.ingest_and_parse(
                package,
                self,
                tags=["自动识别", "source_package"],
            )
            return {
                "mode": "source_package",
                "package_id": package["package_id"],
                "manifest_path": package["manifest_path"],
                "package": package,
                **package["summary"],
                "status": "DONE",
            }
        if path.is_dir():
            print(f"[ingest-auto] scanning folder: {path}")
            results = []
            all_pending = []
            for child in sorted(path.iterdir()):
                if child.is_file() and child.suffix.lower().lstrip(".") in INGEST_ALLOWED_EXTENSIONS:
                    result = self.ingest_auto(child)
                    results.append(result)
                    all_pending.extend(result.get("pending_items", []))
            print(f"[done] ingest-auto finished: {len(results)} files, {len(all_pending)} pending items")
            return {
                "mode": "folder",
                "path": str(path),
                "files": results,
                "pending_items": all_pending,
                "status": "DONE",
            }

        if not path.exists():
            raise FileNotFoundError(f"file not found: {path}")

        print(f"[ingest] saving file: {path}")
        record = self.ingest_file(path, category="待分类", tags=["自动识别"])
        print(f"[ingest] file saved: {record['id']}")
        print("[parse] extracting text")
        parsed = self.parse_file(record["id"])
        print("[parse] done")
        print("[classify] running rules")
        classification = self.classify_file(record["id"])
        print("[classify] done")
        if classification.get("is_bundle"):
            print("[split] bundle detected, splitting document")
        else:
            print("[split] not required")
        pending_items = self._create_pending_items(record["id"], classification)
        detected_items = parsed.get("detected_items") or self.metadata.get_file(record["id"]).get("detected_items", [])
        print(f"[split] detected {len(detected_items)} items")
        print(f"[pending-items] generated {len(pending_items)} items")
        print("[done] ingest-auto finished")
        return {
            "mode": "file",
            "file_id": record["id"],
            "file": parsed,
            "classification": classification,
            "is_bundle": bool(classification.get("is_bundle")),
            "detected_items": detected_items,
            "pending_items": pending_items,
            "status": "DONE",
        }

    def list_split_items(self, file_id: str) -> list[dict[str, Any]]:
        return self.metadata.get_file(file_id).get("detected_items", [])

    def confirm_split_item(self, file_id: str, item_id: str) -> dict[str, Any]:
        record = self.metadata.get_file(file_id)
        items = record.get("detected_items", [])
        found = False
        for item in items:
            if item["id"] == item_id:
                item["status"] = "confirmed"
                found = True
                break
        if not found:
            raise KeyError(f"item_id not found: {item_id}")
        updated = self.metadata.update_file(file_id, {"detected_items": items})
        return next(item for item in updated["detected_items"] if item["id"] == item_id)

    def list_pending_items(self) -> list[dict[str, Any]]:
        return self.metadata.list_pending_items()

    def confirm_pending_item(self, item_id: str) -> dict[str, Any]:
        return self.metadata.update_pending_item_status(item_id, "confirmed")

    def ignore_pending_item(self, item_id: str) -> dict[str, Any]:
        return self.metadata.update_pending_item_status(item_id, "ignored")

    def index_file(self, file_id: str) -> dict[str, Any]:
        record = self.metadata.get_file(file_id)
        parsed_text = record.get("parsed_text") or ""
        if not parsed_text.strip():
            raise ValueError("parsed_text is empty; run parse-file first")

        confirmed_pending_items = self.metadata.pending_items_for_file(file_id, status="confirmed")
        vector = VectorStore()
        if confirmed_pending_items:
            total = 0
            indexed_items = []
            for item in confirmed_pending_items:
                item_text = build_item_index_text(item)
                count = vector.index_text(record, chunk_text(item_text), item=item)
                total += count
                indexed_items.append({"item_id": item["id"], "name": item["name"], "chunks": count})
            return {
                "file_id": file_id,
                "mode": "confirmed_pending_items",
                "indexed_chunks": total,
                "indexed_items": indexed_items,
                "collection": "comlan_kb",
            }

        confirmed_items = [item for item in record.get("detected_items", []) if item.get("status") == "confirmed"]
        if confirmed_items:
            total = 0
            indexed_items = []
            for item in confirmed_items:
                item_text = build_item_index_text(item)
                count = vector.index_text(record, chunk_text(item_text), item=item)
                total += count
                indexed_items.append({"item_id": item["id"], "name": item["name"], "chunks": count})
            return {
                "file_id": file_id,
                "mode": "confirmed_detected_items",
                "indexed_chunks": total,
                "indexed_items": indexed_items,
                "collection": "comlan_kb",
            }

        chunks = chunk_text(parsed_text)
        count = vector.index_text(record, chunks)
        return {"file_id": file_id, "mode": "source_file", "indexed_chunks": count, "collection": "comlan_kb"}

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return VectorStore().search(query, top_k)

    def parse_tender(self, tender_ref: str) -> dict[str, Any]:
        agent = load_tender_parser_agent()
        source, document_text = self._resolve_tender_source(tender_ref)
        parsed = mock_tender_parse(document_text)
        if parsed.get("status") == "failed":
            return {
                "status": "failed",
                "parsed": False,
                "agent": agent["name"],
                "source": source,
                "message": parsed.get("message", "招标文件解析失败"),
                "sections": [],
                "disqualification_rules": [],
                "generation_tasks": [],
            }
        return {
            "status": "success",
            "parsed": True,
            "agent": agent["name"],
            "source": source,
            "project_summary": parsed.get("project_summary", ""),
            "sections": parsed.get("sections", []),
            "disqualification_rules": parsed.get("disqualification_rules", []),
            "generation_tasks": parsed.get("generation_tasks", []),
        }

    # def parse_score_table(self, score_file_id: str, lot_name: str) -> dict[str, Any]:
    #     agent = load_agent("score_table_parser_agent")
    #     record = self.metadata.get_file(score_file_id)
    #     document_text = record.get("parsed_text") or ""
    #     if not document_text.strip():
    #         raise ValueError("parsed_text is empty; run parse-file first")
    #     location = locate_score_table(document_text, record.get("tables", []), lot_name)
    #     if location.get("business_template_name") and location.get("technical_template_name") and location.get("weights"):
    #         parsed_items = build_score_items_from_location(location, record.get("tables", []))
    #         result = {
    #             "status": "success",
    #             "parsed": True,
    #             "agent": agent["name"],
    #             "score_file_id": score_file_id,
    #             "score_table_mode": "auto_lot_matched",
    #             "lot_name": lot_name,
    #             "matched_business_template": location.get("business_template_name"),
    #             "matched_technical_template": location.get("technical_template_name"),
    #             "weights": location.get("weights", {}),
    #             "business_score_items": parsed_items["business_score_items"],
    #             "technical_score_items": parsed_items["technical_score_items"],
    #             "price_score_rule": parsed_items["price_score_rule"],
    #             "score_items": parsed_items["score_items"],
    #             "total_score": parsed_items["total_score"],
    #             "disqualification_rules": parsed_items["disqualification_rules"],
    #             "generation_tasks": parsed_items["generation_tasks"],
    #             "locator_confidence": location.get("confidence", 0),
    #             "locator_warnings": location.get("warnings", []),
    #         }
    #         self.metadata.update_file(score_file_id, {"score_table_parse": result})
    #         return result
    #     if location.get("warnings"):
    #         result = {
    #             "status": "needs_review",
    #             "parsed": False,
    #             "agent": agent["name"],
    #             "score_file_id": score_file_id,
    #             "lot_name": lot_name,
    #             "message": "无法自动定位该标段的商务或技术评分模板，请人工确认候选块；无需传入 table-index。",
    #             "locator_confidence": location.get("confidence", 0),
    #             "warnings": location.get("warnings", []),
    #             "candidate_business_blocks": location.get("candidate_business_blocks", []),
    #             "candidate_technical_blocks": location.get("candidate_technical_blocks", []),
    #             "weights": location.get("weights", {}),
    #             "score_items": [],
    #             "generation_tasks": [],
    #             "disqualification_rules": [],
    #         }
    #         self.metadata.update_file(score_file_id, {"score_table_parse": result})
    #         return result
    #     parsed = mock_score_table_parse(document_text, lot_name)
    #     if parsed.get("status") == "failed":
    #         result = {
    #             "status": "failed",
    #             "parsed": False,
    #             "agent": agent["name"],
    #             "score_file_id": score_file_id,
    #             "lot_name": lot_name,
    #             "message": parsed.get("message", "评分表解析失败"),
    #             "score_items": [],
    #             "generation_tasks": [],
    #             "disqualification_rules": [],
    #         }
    #         self.metadata.update_file(score_file_id, {"score_table_parse": result})
    #         return result
    #     result = {
    #         "status": "success",
    #         "parsed": True,
    #         "agent": agent["name"],
    #         "score_file_id": score_file_id,
    #         **parsed,
    #     }
    #     self.metadata.update_file(score_file_id, {"score_table_parse": result})
    #     return result

    def parse_score_table(
        self,
        score_file_id: str,
        lot_name: str | None,
        *,
        project_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        agent = load_agent("score_table_parser_agent")
        record = self.metadata.get_file(score_file_id)
        document_text = record.get("parsed_text") or ""

        if not document_text.strip():
            raise ValueError("parsed_text is empty; run parse-file first")

        print(
            "[score-table] locating score templates by lot"
            if lot_name
            else "[score-table] locating score templates in selected score source"
        )

        applicability_sources = [
            {
                "file_id": item.get("id"),
                "parsed_text": item.get("parsed_text") or "",
                "tables": item.get("tables") or [],
            }
            for item in self.metadata.list_files()
            if item.get("parsed_text")
        ]
        location = locate_score_table(
            document_text,
            record.get("tables", []),
            lot_name,
            applicability_texts=applicability_sources,
        )
        parsed_items = build_score_items_from_location(location, record.get("tables", []))

        required_rule_groups_extracted = bool(
            location.get("technical_score_table_candidates")
            and location.get("business_score_table")
            and location.get("price_scoring_method")
        )
        unsupported_score_format = bool(location.get("unsupported_score_format")) or not required_rule_groups_extracted
        technical_template_unresolved = bool(location.get("technical_template_unresolved"))
        component_weights_unresolved = bool(location.get("component_weights_unresolved"))

        review_gate = None
        if unsupported_score_format:
            review_gate = "unsupported_score_format"
        elif technical_template_unresolved:
            review_gate = "technical_template_unresolved"
        elif component_weights_unresolved:
            review_gate = "component_weights_unresolved"

        status = "needs_review" if review_gate else "success"
        if review_gate == "technical_template_unresolved":
            message = "评分规则已抽取，但存在多套技术评分候选，且 scoped sources 中没有明确的本项目适用映射。"
        elif review_gate == "component_weights_unresolved":
            message = "评分规则和适用技术模板已抽取，但商务、技术、价格 component weights 尚未完整确认。"
        elif review_gate == "unsupported_score_format":
            message = "评分来源中存在无法完整结构化的评分格式，需要人工确认。"
        else:
            message = "评分规则、适用技术模板和 component weights 已确认。"

        print(
            f"[score-table] extracted technical candidates="
            f"{len(location.get('technical_score_table_candidates') or [])}, "
            f"business={bool(location.get('business_score_table'))}, "
            f"price={bool(location.get('price_scoring_method'))}"
        )
        if review_gate:
            print(f"[score-table] review gate: {review_gate}")

        result = {
            "schema_version": "score_table_parse_v2",
            "status": status,
            "parsed": bool(location.get("score_rules_extracted")),
            "agent": "score_table_locator",
            "score_file_id": score_file_id,
            "score_table_mode": "auto_lot_matched" if lot_name else "auto_scoped_source",
            "lot_name": lot_name,
            "message": message,
            "review_gate": review_gate,
            "score_rules_extracted": bool(location.get("score_rules_extracted")),
            "technical_template_resolution_status": location.get("technical_template_resolution_status"),
            "technical_template_unresolved": technical_template_unresolved,
            "component_weights_unresolved": component_weights_unresolved,
            "unsupported_score_format": unsupported_score_format,
            "matched_business_template": (
                (location.get("business_score_table") or {}).get("heading")
                or location.get("business_template_name")
            ),
            "matched_technical_template": location.get("technical_template_name"),
            "selected_technical_candidate_id": location.get("selected_technical_candidate_id"),
            "technical_template_selection_reason": location.get("technical_template_selection_reason"),
            "technical_template_evidence": location.get("technical_template_evidence") or [],
            "weights": location.get("weights", {}),
            "business_score_table": parsed_items.get("business_score_table"),
            "technical_score_table_candidates": parsed_items.get("technical_score_table_candidates", []),
            "price_scoring_method": parsed_items.get("price_scoring_method"),
            "business_score_items": parsed_items.get("business_score_items", []),
            "technical_score_items": parsed_items.get("technical_score_items", []),
            "price_score_rule": parsed_items.get("price_score_rule", {}),
            "score_items": parsed_items.get("score_items", []),
            "total_score": parsed_items.get("total_score", 100),
            "score_scale": parsed_items.get("score_scale", {}),
            "disqualification_rules": parsed_items.get("disqualification_rules", []),
            "generation_tasks": parsed_items.get("generation_tasks", []),
            "locator_confidence": location.get("confidence", 0),
            "locator_warnings": location.get("warnings", []),
            "candidate_business_blocks": location.get("candidate_business_blocks", []),
            "candidate_technical_blocks": location.get("candidate_technical_blocks", []),
        }

        result = apply_project_profile_to_score_parse(result, project_profile)
        self.metadata.update_file(score_file_id, {"score_table_parse": result})
        return result



    def parse_tender_project(
        self,
        tender_file_id: str,
        score_file_id: str,
        lot_name: str | None,
        *,
        score_parse_override: dict[str, Any] | None = None,
        project_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        load_agent("tender_merge_agent")
        tender_parse = self.parse_tender(tender_file_id)
        if tender_parse.get("status") == "failed":
            return {
                "status": "failed",
                "merged": False,
                "agent": "tender_merge_agent",
                "message": tender_parse.get("message", "招标文件解析失败，无法合并项目任务图"),
                "generation_tasks": [],
            }
        score_parse = deepcopy(score_parse_override) if score_parse_override is not None else None
        if score_parse is None:
            score_record = self.metadata.get_file(score_file_id)
            score_parse = score_record.get("score_table_parse")
            if (
                not score_parse
                or score_parse.get("schema_version") != "score_table_parse_v2"
                or score_parse.get("lot_name") != lot_name
            ):
                score_parse = self.parse_score_table(
                    score_file_id,
                    lot_name,
                    project_profile=project_profile,
                )
        if score_parse.get("status") in {"failed", "needs_review"}:
            return {
                "status": score_parse.get("status"),
                "merged": False,
                "agent": "tender_merge_agent",
                "message": score_parse.get("message", "评分表解析失败，无法合并项目任务图"),
                "review_gate": score_parse.get("review_gate"),
                "score_rules_extracted": score_parse.get("score_rules_extracted", False),
                "technical_template_resolution_status": score_parse.get("technical_template_resolution_status"),
                "technical_template_unresolved": score_parse.get("technical_template_unresolved", False),
                "component_weights_unresolved": score_parse.get("component_weights_unresolved", False),
                "unsupported_score_format": score_parse.get("unsupported_score_format", False),
                "technical_candidate_count": len(score_parse.get("technical_score_table_candidates") or []),
                "business_score_table_extracted": bool(score_parse.get("business_score_table")),
                "price_scoring_method_extracted": bool(score_parse.get("price_scoring_method")),
                "weights": score_parse.get("weights") or {},
                "generation_tasks": [],
            }
        graph = build_tender_project_graph(tender_file_id, score_file_id, lot_name, tender_parse, score_parse)
        result = {
            "status": "success",
            "merged": True,
            "agent": "tender_merge_agent",
            "review_gate": None,
            "score_rules_extracted": score_parse.get("score_rules_extracted", True),
            "technical_template_resolution_status": score_parse.get("technical_template_resolution_status"),
            "technical_template_unresolved": False,
            "component_weights_unresolved": False,
            "unsupported_score_format": False,
            "technical_candidate_count": len(score_parse.get("technical_score_table_candidates") or []),
            "business_score_table_extracted": bool(score_parse.get("business_score_table")),
            "price_scoring_method_extracted": bool(score_parse.get("price_scoring_method")),
            "weights": score_parse.get("weights") or {},
            **graph,
        }
        save_tender_project(result)
        return result

    def _resolve_tender_source(self, tender_ref: str) -> tuple[str, str]:
        try:
            record = self.metadata.get_file(tender_ref)
            text = record.get("parsed_text") or ""
            return f"metadata:{tender_ref}", text
        except KeyError:
            path = Path(tender_ref)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"tender file or file_id not found: {tender_ref}")
            if path.suffix.lower() in {".txt", ".md"}:
                return str(path), path.read_text(encoding="utf-8", errors="ignore")
            return str(path), ""

    def _create_pending_items(self, file_id: str, classification: dict[str, Any]) -> list[dict[str, Any]]:
        record = self.metadata.get_file(file_id)
        split_items: list[dict[str, Any]] = []
        try:
            split_record = self.split_file(file_id)
            split_items = [
                item
                for item in split_record.get("detected_items", [])
                if item.get("document_type") != "未分类资料"
            ]
        except ValueError:
            split_items = []

        if split_items:
            items = [pending_from_detected_item(item) for item in split_items]
        elif classification.get("is_bundle"):
            split_record = self.split_file(file_id)
            items = [pending_from_detected_item(item) for item in split_record.get("detected_items", [])]
        else:
            items = [pending_from_classification(record, classification)]
        return self.metadata.upsert_pending_items_for_file(file_id, items)


def build_item_index_text(item: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"资料名称：{item.get('name', '')}",
            f"资料类型：{item.get('document_type', '')}",
            f"资料分类：{item.get('category', '')}",
            f"标签：{', '.join(item.get('tags', []))}",
            f"有效期：{item.get('valid_from') or ''} 至 {item.get('valid_to') or ''}",
            f"来源页码：{item.get('source_page', '')}",
            f"来源文本：{item.get('source_text', '')}",
            f"证据等级：{item.get('evidence_level', '')}",
            f"证据原文：{item.get('evidence_text', '')}",
            f"推荐用途：{item.get('recommended_usage', '')}",
        ]
    )


def pending_from_classification(record: dict[str, Any], classification: dict[str, Any]) -> dict[str, Any]:
    assets = record.get("assets", [])
    return {
        "id": f"pending_{uuid.uuid4().hex[:12]}",
        "source_file_id": record["id"],
        "name": record["original_name"],
        "category": classification["category"],
        "document_type": classification["document_type"],
        "tags": classification.get("tags", []),
        "valid_from": classification.get("valid_from"),
        "valid_to": classification.get("valid_to"),
        "source_text": (record.get("parsed_text") or "")[:600],
        "evidence_text": (record.get("parsed_text") or "")[:600],
        "evidence_level": "explicit" if (record.get("parsed_text") or "").strip() else "title_only",
        "related_asset_ids": [asset["id"] for asset in assets[:3]],
        "recommended_usage": classification.get("recommended_usage", ""),
        "confidence": classification.get("confidence", 0.5),
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def pending_from_detected_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"pending_{uuid.uuid4().hex[:12]}",
        "source_file_id": item["source_file_id"],
        "name": item["name"],
        "category": item["category"],
        "document_type": item["document_type"],
        "tags": item.get("tags", []),
        "valid_from": item.get("valid_from"),
        "valid_to": item.get("valid_to"),
        "source_text": item.get("source_text", ""),
        "evidence_text": item.get("evidence_text", item.get("source_text", "")),
        "evidence_level": item.get("evidence_level", "explicit"),
        "related_asset_ids": item.get("related_asset_ids", []),
        "recommended_usage": item.get("recommended_usage", ""),
        "confidence": item.get("confidence", 0.5),
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def dump_model(model) -> dict[str, Any]:
    if isinstance(model, dict):
        return model
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def load_tender_parser_agent() -> dict[str, Any]:
    return load_agent("tender_parser_agent")


def load_agent(agent_name: str) -> dict[str, Any]:
    agent_dir = BACKEND_ROOT / "agents" / agent_name
    system_path = agent_dir / "system.txt"
    schema_path = agent_dir / "output_schema.json"
    if not system_path.exists() or not schema_path.exists():
        raise FileNotFoundError(f"{agent_name} files are missing")
    return {
        "name": agent_name,
        "system": system_path.read_text(encoding="utf-8"),
        "output_schema": schema_path.read_text(encoding="utf-8"),
    }


def save_tender_project(project: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "tender_projects.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8") or '{"projects": []}')
    else:
        data = {"projects": []}
    projects = data.setdefault("projects", [])
    project_key = (project["tender_file_id"], project["score_file_id"], project["lot_name"])
    projects[:] = [
        item
        for item in projects
        if (item.get("tender_file_id"), item.get("score_file_id"), item.get("lot_name")) != project_key
    ]
    projects.append(project)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ingest_auto(path: str | Path) -> dict[str, Any]:
    return KnowledgeBaseService().ingest_auto(Path(path))


def classify_file(file_id: str) -> dict[str, Any]:
    return KnowledgeBaseService().classify_file(file_id)


def split_file(file_id: str) -> dict[str, Any]:
    return KnowledgeBaseService().split_file(file_id)


def list_pending_items() -> list[dict[str, Any]]:
    return KnowledgeBaseService().list_pending_items()


def confirm_item(item_id: str) -> dict[str, Any]:
    return KnowledgeBaseService().confirm_pending_item(item_id)


def ignore_item(item_id: str) -> dict[str, Any]:
    return KnowledgeBaseService().ignore_pending_item(item_id)


def parse_tender(tender_ref: str) -> dict[str, Any]:
    return KnowledgeBaseService().parse_tender(tender_ref)


def parse_score_table(score_file_id: str, lot_name: str | None) -> dict[str, Any]:
    return KnowledgeBaseService().parse_score_table(score_file_id, lot_name)


def parse_tender_project(tender_file_id: str, score_file_id: str, lot_name: str | None) -> dict[str, Any]:
    return KnowledgeBaseService().parse_tender_project(tender_file_id, score_file_id, lot_name)
