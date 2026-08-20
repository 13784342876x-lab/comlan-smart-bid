import json
import re
from pathlib import Path


OUTLINE_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "writing"
    / "company_master_outline_v0.json"
)

EXPECTED_TITLES = {
    "MODULE-IMPLEMENTATION": "项目实施方案",
    "MODULE-PROJECT-MANAGEMENT": "项目管理与团队配置",
    "MODULE-QUALITY-ACCEPTANCE": "质量保证、测试与验收方案",
    "MODULE-SERVICE-WARRANTY": "售后服务与运行保障方案",
    "MODULE-PARAMETER-RESPONSE": "技术参数及点对点应答",
    "MODULE-EVIDENCE-APPENDIX": "技术证明材料及附件索引",
}
MOJIBAKE_FRAGMENTS = ("瀹", "椤", "鎶€", "璐ㄩ", "鍩硅")


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def test_company_master_outline_is_valid_utf8_json():
    with OUTLINE_PATH.open("r", encoding="utf-8") as stream:
        data = json.load(stream)

    assert data["schema_version"] == "company_technical_writing_outline_v0"
    assert data["name"] == "公司技术标通用写作大纲V0"


def test_company_master_outline_has_complete_unique_modules():
    data = json.loads(OUTLINE_PATH.read_text(encoding="utf-8"))
    modules = data["modules"]
    module_ids = [module["module_id"] for module in modules]

    assert len(modules) == 13
    assert len(module_ids) == len(set(module_ids))

    for module in modules:
        title = module["default_title"]
        assert isinstance(title, str) and title.strip()
        assert re.search(r"[\u4e00-\u9fff]", title)
        assert not any(fragment in title for fragment in MOJIBAKE_FRAGMENTS)


def test_company_master_outline_contains_no_mojibake_or_broken_strings():
    raw_text = OUTLINE_PATH.read_text(encoding="utf-8")
    data = json.loads(raw_text)
    strings = list(_all_strings(data))

    assert not any(fragment in raw_text for fragment in MOJIBAKE_FRAGMENTS)
    assert "\ufffd" not in raw_text
    assert all(not value.endswith("?") for value in strings)
    assert all(not re.search(r"(?:^|[\u4e00-\u9fff])\?(?:$|[，。；：、])", value) for value in strings)

    for module in data["modules"]:
        for field in ("placement_aliases", "trigger_terms", "default_sections"):
            values = module[field]
            assert isinstance(values, list)
            assert all(isinstance(value, str) and value.strip() for value in values)


def test_company_master_outline_critical_titles():
    data = json.loads(OUTLINE_PATH.read_text(encoding="utf-8"))
    titles = {
        module["module_id"]: module["default_title"]
        for module in data["modules"]
    }

    for module_id, expected_title in EXPECTED_TITLES.items():
        assert titles[module_id] == expected_title
