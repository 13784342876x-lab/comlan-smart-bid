from config import BACKEND_ROOT
from app.agent_runner import run_agent


class ReviewService:
    def review_section(self, section: str, text: str = "", requirements: str = "", model_profile: str | None = None) -> dict:
        if not text.strip() or not requirements.strip():
            return {
                "section": section, "status": "not_reviewed", "risks": [],
                "errors": ["审核需要实际章节正文和对应招标要求；仅提供章节名称无法审核"],
            }
        result = run_agent("chapter_review", {
            "task_name": section, "section_text": text, "requirements": requirements,
            "scope": "仅审查所提供的正文和要求；证据真实性及全文覆盖未经验证，不得声明正式审核通过",
        }, agents_dir=str(BACKEND_ROOT / "agents"), model_profile=model_profile)
        payload = result.get("result")
        risks = payload.get("risks") if isinstance(payload, dict) else None
        valid = isinstance(risks, list) and all(isinstance(item, dict) for item in risks)
        if not valid:
            result["status"] = "failed"
            result.setdefault("errors", []).append("review result must contain a risks array of objects")
        return {
            "section": section, "status": "reviewed_partial_scope" if result["status"] == "success" else "failed",
            "risks": risks if valid else [], "run_result": result,
            "release_ready": False, "errors": result.get("errors", []),
        }
