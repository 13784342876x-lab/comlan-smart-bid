from __future__ import annotations

from typing import Any


def render_release_markdown(cleaned_artifact: dict[str, Any]) -> str:
    """Render canonical cleaned fulltext as deterministic Pandoc Markdown."""
    fulltext = cleaned_artifact["fulltext"]
    lines: list[str] = []
    for chapter in sorted(fulltext["chapters"], key=lambda item: int(item["order"])):
        lines.extend((f"# {chapter['chapter_title']}", ""))
        outline_by_id = {
            item["section_id"]: item for item in chapter["section_outline"]
        }
        sections = sorted(
            chapter["sections"],
            key=lambda item: int(outline_by_id[item["section_id"]]["document_order"]),
        )
        for section in sections:
            outline = outline_by_id[section["section_id"]]
            heading_level = int(outline["depth"]) + 1
            content = section["content"]
            first_line, separator, remainder = content.partition("\n")
            marker, space, title = first_line.partition(" ")
            if (
                separator
                and space
                and 1 <= len(marker) <= 6
                and set(marker) == {"#"}
                and title.strip() == section["heading"].strip()
            ):
                content = remainder.lstrip("\n")
            lines.extend(
                (
                    f"{'#' * heading_level} {section['heading']}",
                    "",
                    content,
                    "",
                )
            )
    return "\n".join(lines).rstrip() + "\n"
