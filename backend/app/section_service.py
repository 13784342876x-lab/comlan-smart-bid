from config import BACKEND_ROOT
from app.agent_runner import run_agent


class SectionService:
    def generate_section(self, section: str, query: str, model_profile: str | None = None) -> dict:
        # The legacy vector index contains hash embeddings, not semantic evidence.
        result = run_agent("technical_writer", {
            "task_name": section, "writing_goal": query,
            "evidence_refs": [], "missing_materials": [
                {"reason": "语义检索不可用：旧向量索引尚未迁移至真实 embedding；不得编造证明材料或引用"},
            ],
        }, agents_dir=str(BACKEND_ROOT / "agents"), model_profile=model_profile)
        payload = result.get("result")
        payload = payload if isinstance(payload, dict) else {}
        content = str(payload.get("content") or result.get("raw_output") or "")
        if result.get("status") == "success" and not content.strip():
            result["status"] = "failed"
            result.setdefault("errors", []).append("model returned no section content")
        return {
            "section_name": section, "content": content, "used_kb_files": [],
            "status": result["status"], "run_result": result,
            "retrieval_status": "unavailable", "audit_status": "not_reviewed",
            "warnings": ["语义检索不可用；当前草稿未绑定知识库证据，需人工补充与审核"],
        }
