from app.llm_mock import mock_review_section


class ReviewService:
    def review_section(self, section: str) -> list[dict]:
        return mock_review_section(section)
