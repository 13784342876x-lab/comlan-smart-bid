from pathlib import Path

from app.llm_mock import mock_tender_parse


class TenderService:
    def parse_tender(self, path: Path) -> dict:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"file not found: {path}")
        return mock_tender_parse(path.name)
