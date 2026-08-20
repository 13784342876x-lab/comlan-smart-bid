from config import BACKEND_ROOT
from app.kb_service import KnowledgeBaseService
from app.llm_mock import mock_section_content


class SectionService:
    def __init__(self) -> None:
        self.kb = KnowledgeBaseService()

    def generate_section(self, section: str, query: str) -> dict:
        kb_results = self.kb.search(query, top_k=5)
        _ = (BACKEND_ROOT / "agents" / "technical_writer" / "system.txt").read_text(encoding="utf-8")
        _ = (BACKEND_ROOT / "agents" / "technical_writer" / "user_template.txt").read_text(encoding="utf-8")
        return mock_section_content(section, query, kb_results)
