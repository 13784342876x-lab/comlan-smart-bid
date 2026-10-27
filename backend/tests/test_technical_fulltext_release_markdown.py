from app.technical_fulltext_release_markdown import render_release_markdown


def test_render_release_markdown_uses_canonical_order_depth_and_content() -> None:
    placeholder = "{{PH-FIELD-HUMAN-CONFIRMATION-001}}"
    table = "| 角色 | 职责 |\n| --- | --- |\n| 项目经理 | 统筹 |"
    artifact = {
        "fulltext": {
            "chapters": [
                {
                    "chapter_id": "WC-002",
                    "chapter_title": "第二章",
                    "order": 2,
                    "section_outline": [
                        {
                            "section_id": "WC-002-01",
                            "depth": 1,
                            "document_order": 3,
                        }
                    ],
                    "sections": [
                        {
                            "section_id": "WC-002-01",
                            "heading": "末节",
                            "content": "- 列表项",
                        }
                    ],
                },
                {
                    "chapter_id": "WC-001",
                    "chapter_title": "第一章",
                    "order": 1,
                    "section_outline": [
                        {
                            "section_id": "WC-001-02",
                            "depth": 2,
                            "document_order": 2,
                        },
                        {
                            "section_id": "WC-001-01",
                            "depth": 1,
                            "document_order": 1,
                        },
                    ],
                    "sections": [
                        {
                            "section_id": "WC-001-02",
                            "heading": "子节",
                            "content": placeholder,
                        },
                        {
                            "section_id": "WC-001-01",
                            "heading": "表格节",
                            "content": f"# 表格节\n\n{table}",
                        },
                    ],
                },
            ]
        }
    }

    markdown = render_release_markdown(artifact)

    assert markdown == (
        "# 第一章\n\n"
        "## 表格节\n\n"
        f"{table}\n\n"
        "### 子节\n\n"
        f"{placeholder}\n\n"
        "# 第二章\n\n"
        "## 末节\n\n"
        "- 列表项\n"
    )
    assert "Release ready" not in markdown
    assert "工作稿" not in markdown
