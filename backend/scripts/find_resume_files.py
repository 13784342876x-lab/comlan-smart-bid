import json
import re
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import METADATA_PATH


NAME_KEYWORDS = ["吴云畅", "冯振书", "云服务部", "数字化办公"]
TEXT_SIGNALS = ["工作经历", "项目经验", "专业技能", "相关证书", "姓名", "性别", "工作年限"]


def first_text(record: dict[str, Any]) -> str:
    for path in [
        ("parsed_text",),
        ("text",),
        ("content",),
        ("parse_result", "parsed_text"),
        ("parse_result", "text"),
    ]:
        value: Any = record
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def first_assets(record: dict[str, Any]) -> list[Any]:
    for path in [("assets",), ("parse_result", "assets")]:
        value: Any = record
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, list):
            return value
    return []


def guess_score(record: dict[str, Any]) -> int:
    name = record.get("original_name") or ""
    category = record.get("category") or ""
    text = first_text(record)
    score = 0
    score += sum(3 for keyword in NAME_KEYWORDS if keyword in name)
    if "简历" in name:
        score += 6
    elif "证书" in name:
        score += 1
    score += sum(2 for keyword in ["简历", "人员", "工程师"] if keyword in category)
    score += sum(2 for signal in TEXT_SIGNALS if signal in text)
    if "项目负责人简历" in text:
        score += 2
    return score


def possible_person_name(text: str) -> str | None:
    match = re.search(r"姓名\s*[:：\t ]+\s*([\u4e00-\u9fff]{2,4})", text)
    if match and match.group(1) != "姓名":
        return match.group(1)
    match = re.search(r"姓名\s*[:：\t ]+\s*姓名\s*[:：\t ]+\s*([\u4e00-\u9fff]{2,4})", text)
    if match:
        return match.group(1)
    return None


def find_candidates() -> list[dict[str, Any]]:
    if not METADATA_PATH.exists():
        return []
    data = json.loads(METADATA_PATH.read_text(encoding="utf-8") or "{}")
    rows = []
    for record in data.get("files", []):
        text = first_text(record)
        score = guess_score(record)
        if score <= 0:
            continue
        rows.append(
            {
                "file_id": record.get("id"),
                "original_name": record.get("original_name"),
                "category": record.get("category"),
                "has_parsed_text": bool(text.strip()),
                "asset_count": len(first_assets(record)),
                "guessed_resume_score": score,
                "possible_person_name": possible_person_name(text),
            }
        )
    return sorted(rows, key=lambda item: item["guessed_resume_score"], reverse=True)


def main() -> None:
    rows = find_candidates()
    print(json.dumps({"candidate_count": len(rows), "candidates": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
