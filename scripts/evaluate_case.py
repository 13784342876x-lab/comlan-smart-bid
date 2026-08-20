from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"


@dataclass
class CheckResult:
    name: str
    status: str
    expected: Any = None
    actual: Any = None
    reason: str = ""


def main() -> int:
    args = parse_args()
    case_dir = Path(args.case_dir).expanduser().resolve()

    try:
        expected_doc = load_json(case_dir / "expected.json")
    except Exception as exc:
        print(f"FAIL: cannot read expected.json: {exc}")
        return 2

    expected = unwrap_expected(expected_doc)

    try:
        actual, actual_source = load_or_build_actual(
            case_dir=case_dir,
            expected=expected,
            expected_doc=expected_doc,
            actual_file=Path(args.actual_file).expanduser().resolve() if args.actual_file else None,
            refresh=args.refresh,
            tender_file_id=args.tender_file_id,
            score_file_id=args.score_file_id,
            lot_name=args.lot_name,
            write_actual=not args.no_write_actual,
        )
    except Exception as exc:
        print(f"FAIL: cannot prepare actual_tender_project.json: {exc}")
        return 2

    expected_lot = expected_value(expected, "lot_name") or find_config_value("lot_name", expected, expected_doc, {})
    actual_project = normalize_project(actual, expected_lot=expected_lot)
    results = evaluate(expected, actual_project)
    print_report(case_dir, actual_source, results)
    return 0 if all(result.status != "FAIL" for result in results) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate parse-score-table and parse-tender-project output against a case expected.json."
    )
    parser.add_argument("--case-dir", required=True, help="Case directory containing expected.json.")
    parser.add_argument(
        "--actual-file",
        help="Optional actual tender project JSON path. Defaults to <case-dir>/actual_tender_project.json.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore existing actual_tender_project.json and run the existing backend parser services.",
    )
    parser.add_argument("--tender-file-id", help="Override tender_file_id from case config or expected.json.")
    parser.add_argument("--score-file-id", help="Override score_file_id from case config or expected.json.")
    parser.add_argument("--lot-name", help="Override lot_name from expected.json.")
    parser.add_argument(
        "--no-write-actual",
        action="store_true",
        help="Do not write generated actual_tender_project.json back to the case directory.",
    )
    return parser.parse_args()


def load_or_build_actual(
    *,
    case_dir: Path,
    expected: dict[str, Any],
    expected_doc: dict[str, Any],
    actual_file: Path | None,
    refresh: bool,
    tender_file_id: str | None,
    score_file_id: str | None,
    lot_name: str | None,
    write_actual: bool,
) -> tuple[dict[str, Any], str]:
    actual_path = actual_file or case_dir / "actual_tender_project.json"
    if actual_path.exists() and not refresh:
        return load_json(actual_path), str(actual_path)

    config = load_optional_case_config(case_dir)
    tender_file_id = tender_file_id or find_config_value("tender_file_id", expected, expected_doc, config)
    score_file_id = score_file_id or find_config_value("score_file_id", expected, expected_doc, config)
    lot_name = lot_name or find_config_value("lot_name", expected, expected_doc, config)

    missing = [
        name
        for name, value in (
            ("tender_file_id", tender_file_id),
            ("score_file_id", score_file_id),
            ("lot_name", lot_name),
        )
        if not value
    ]
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(
            f"{actual_path} does not exist and cannot run parser because {missing_text} is missing. "
            "Put these values in expected.json/case.json or pass command-line overrides."
        )

    actual = run_existing_parsers(str(tender_file_id), str(score_file_id), str(lot_name), case_dir, write_outputs=write_actual)
    if write_actual:
        actual_path.parent.mkdir(parents=True, exist_ok=True)
        actual_path.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return actual, f"generated via KnowledgeBaseService -> {actual_path}"


def run_existing_parsers(
    tender_file_id: str,
    score_file_id: str,
    lot_name: str,
    case_dir: Path,
    *,
    write_outputs: bool,
) -> dict[str, Any]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.kb_service import KnowledgeBaseService

    service = KnowledgeBaseService()
    score_result = service.parse_score_table(score_file_id, lot_name)
    if write_outputs:
        score_path = case_dir / "actual_score_table.json"
        score_path.parent.mkdir(parents=True, exist_ok=True)
        score_path.write_text(json.dumps(score_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return service.parse_tender_project(tender_file_id, score_file_id, lot_name)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_optional_case_config(case_dir: Path) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for name in ("case.json", "config.json", "case_config.json"):
        path = case_dir / name
        if path.exists():
            data = load_json(path)
            if isinstance(data, dict):
                merged.update(data)
    return merged


def unwrap_expected(data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("expected"), dict):
        return data["expected"]
    return data


def normalize_project(data: dict[str, Any], expected_lot: Any = None) -> dict[str, Any]:
    if isinstance(data.get("actual_tender_project"), dict):
        return normalize_project(data["actual_tender_project"], expected_lot)
    if isinstance(data.get("result"), dict):
        return normalize_project(data["result"], expected_lot)
    projects = data.get("projects")
    if isinstance(projects, list) and projects:
        if expected_lot:
            for project in reversed(projects):
                if normalize_text(project.get("lot_name")) == normalize_text(expected_lot):
                    return project
        return projects[-1]
    return data


def evaluate(expected: dict[str, Any], actual: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    check_scalar(results, "lot_name", expected_value(expected, "lot_name"), actual.get("lot_name"))
    check_weights(results, expected_value(expected, "weights"), extract_weights(actual))
    check_count(results, "business_items_count", expected_value(expected, "business_items_count"), business_items(actual))
    check_count(results, "technical_items_count", expected_value(expected, "technical_items_count"), technical_items(actual))
    check_count(results, "score_items_count", expected_value(expected, "score_items_count"), score_items(actual))
    check_count(results, "task_count", expected_value(expected, "task_count"), actual.get("generation_tasks") or [])
    check_key_items(
        results,
        "key_business_score_items",
        expected_value(expected, "key_business_score_items"),
        business_items(actual),
    )
    check_key_items(
        results,
        "key_technical_score_items",
        expected_value(expected, "key_technical_score_items"),
        technical_items(actual),
    )
    check_price_rule(results, expected_value(expected, "price_score_rule"), actual.get("price_score_rule"))
    return results


def expected_value(expected: dict[str, Any], name: str) -> Any:
    aliases: dict[str, list[list[str]]] = {
        "lot_name": [["lot_name"], ["input", "lot_name"], ["inputs", "lot_name"]],
        "weights": [["weights"], ["score_scale", "weights"]],
        "business_items_count": [["business_items_count"], ["business_score_items_count"], ["counts", "business_items"]],
        "technical_items_count": [["technical_items_count"], ["technical_score_items_count"], ["counts", "technical_items"]],
        "score_items_count": [["score_items_count"], ["counts", "score_items"]],
        "task_count": [["task_count"], ["generation_tasks_count"], ["counts", "tasks"], ["counts", "generation_tasks"]],
        "key_business_score_items": [
            ["key_business_score_items"],
            ["key_business_items"],
            ["business_key_score_items"],
            ["business_score_items"],
        ],
        "key_technical_score_items": [
            ["key_technical_score_items"],
            ["key_technical_items"],
            ["technical_key_score_items"],
            ["technical_score_items"],
        ],
        "price_score_rule": [["price_score_rule"], ["price_rule"]],
    }
    for path in aliases[name]:
        value = deep_get(expected, path)
        if value is not None:
            return value
    return None


def find_config_value(name: str, expected: dict[str, Any], expected_doc: dict[str, Any], config: dict[str, Any]) -> Any:
    aliases: dict[str, list[list[str]]] = {
        "tender_file_id": [["tender_file_id"], ["inputs", "tender_file_id"], ["input", "tender_file_id"]],
        "score_file_id": [["score_file_id"], ["inputs", "score_file_id"], ["input", "score_file_id"]],
        "lot_name": [["lot_name"], ["inputs", "lot_name"], ["input", "lot_name"]],
    }
    for source in (config, expected_doc, expected):
        for path in aliases[name]:
            value = deep_get(source, path)
            if value:
                return value
    return None


def deep_get(data: Any, path: list[str]) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def check_scalar(results: list[CheckResult], name: str, expected: Any, actual: Any) -> None:
    if expected is None:
        results.append(CheckResult(name, "SKIP", reason="expected value not provided"))
    elif values_equal(expected, actual):
        results.append(CheckResult(name, "PASS", expected, actual))
    else:
        results.append(CheckResult(name, "FAIL", expected, actual, "value mismatch"))


def check_weights(results: list[CheckResult], expected: Any, actual: dict[str, Any]) -> None:
    if expected is None:
        results.append(CheckResult("weights", "SKIP", reason="expected value not provided"))
        return
    expected_weights = coerce_weights(expected)
    if not isinstance(expected_weights, dict):
        results.append(CheckResult("weights", "FAIL", expected, actual, "expected weights must be an object"))
        return
    for key, expected_value_for_key in expected_weights.items():
        name = f"weights.{key}"
        actual_value = actual.get(key)
        if values_equal(expected_value_for_key, actual_value):
            results.append(CheckResult(name, "PASS", expected_value_for_key, actual_value))
        else:
            results.append(CheckResult(name, "FAIL", expected_value_for_key, actual_value, "weight mismatch"))


def check_count(results: list[CheckResult], name: str, expected: Any, actual_items: Any) -> None:
    if expected is None:
        results.append(CheckResult(name, "SKIP", reason="expected value not provided"))
        return
    actual_count = len(actual_items) if isinstance(actual_items, list) else 0
    if values_equal(expected, actual_count):
        results.append(CheckResult(name, "PASS", expected, actual_count))
    else:
        results.append(CheckResult(name, "FAIL", expected, actual_count, "count mismatch"))


def check_key_items(
    results: list[CheckResult],
    name: str,
    expected_items: Any,
    actual_items: list[dict[str, Any]],
) -> None:
    if expected_items is None:
        results.append(CheckResult(name, "SKIP", reason="expected value not provided"))
        return
    if isinstance(expected_items, dict):
        expected_items = [{"item_name": key, "score": value} for key, value in expected_items.items()]
    if not isinstance(expected_items, list):
        results.append(CheckResult(name, "FAIL", expected_items, actual_items, "expected key items must be a list"))
        return
    for index, expected_item in enumerate(expected_items, start=1):
        label = item_label(expected_item) or f"#{index}"
        check_name = f"{name}.{label}"
        match = find_score_item(actual_items, expected_item)
        if not match:
            results.append(CheckResult(check_name, "FAIL", expected_item, None, "score item not found"))
            continue
        expected_score = item_score(expected_item)
        actual_score = item_score(match)
        if expected_score is None:
            results.append(CheckResult(check_name, "PASS", expected_item, match))
        elif values_equal(expected_score, actual_score):
            results.append(CheckResult(check_name, "PASS", expected_item, match))
        else:
            results.append(CheckResult(check_name, "FAIL", expected_item, match, "score mismatch"))


def check_price_rule(results: list[CheckResult], expected: Any, actual: Any) -> None:
    if expected is None:
        results.append(CheckResult("price_score_rule", "SKIP", reason="expected value not provided"))
        return
    ok, reason = match_expected_value(expected, actual)
    status = "PASS" if ok else "FAIL"
    results.append(CheckResult("price_score_rule", status, expected, actual, reason))


def extract_weights(actual: dict[str, Any]) -> dict[str, Any]:
    direct = coerce_weights(actual.get("weights"))
    if direct:
        return direct
    score_scale = actual.get("score_scale")
    if isinstance(score_scale, dict):
        nested = coerce_weights(score_scale.get("weights"))
        if nested:
            return nested
    return coerce_weights(actual.get("document_structure")) or {}


def coerce_weights(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        weights: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"business", "technical", "price", "qualification"}:
                weights[item_type] = item.get("weight")
        return weights
    return {}


def business_items(actual: dict[str, Any]) -> list[dict[str, Any]]:
    items = actual.get("business_score_items")
    if isinstance(items, list):
        return items
    return [item for item in score_items(actual) if item.get("category") == "business"]


def technical_items(actual: dict[str, Any]) -> list[dict[str, Any]]:
    items = actual.get("technical_score_items")
    if isinstance(items, list):
        return items
    return [item for item in score_items(actual) if item.get("category") == "technical"]


def score_items(actual: dict[str, Any]) -> list[dict[str, Any]]:
    items = actual.get("score_items")
    return items if isinstance(items, list) else []


def find_score_item(actual_items: list[dict[str, Any]], expected_item: Any) -> dict[str, Any] | None:
    if not isinstance(expected_item, dict):
        expected_item = {"item_name": expected_item}
    expected_id = expected_item.get("id")
    if expected_id:
        for item in actual_items:
            if normalize_text(item.get("id")) == normalize_text(expected_id):
                return item
    expected_name = item_label(expected_item)
    if not expected_name:
        return None
    normalized_expected = normalize_text(expected_name)
    for item in actual_items:
        if normalize_text(item_label(item)) == normalized_expected:
            return item
    for item in actual_items:
        normalized_actual = normalize_text(item_label(item))
        if normalized_expected in normalized_actual or normalized_actual in normalized_expected:
            return item
    return None


def item_label(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("item_name") or item.get("name") or item.get("title") or "")
    return str(item)


def item_score(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("score")
    return None


def match_expected_value(expected: Any, actual: Any) -> tuple[bool, str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False, "actual value is not an object"
        for key, expected_child in expected.items():
            if key.endswith("_contains"):
                actual_key = key[: -len("_contains")]
                actual_text = normalize_text(actual.get(actual_key))
                expected_text = normalize_text(expected_child)
                if expected_text not in actual_text:
                    return False, f"{actual_key} does not contain expected text"
                continue
            ok, reason = match_expected_value(expected_child, actual.get(key))
            if not ok:
                return False, f"{key}: {reason}"
        return True, ""
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False, "actual value is not a list"
        for expected_item in expected:
            found = any(match_expected_value(expected_item, actual_item)[0] for actual_item in actual)
            if not found:
                return False, f"list item not found: {to_json(expected_item)}"
        return True, ""
    if isinstance(expected, str) and isinstance(actual, (dict, list)):
        if normalize_text(expected) in normalize_text(to_json(actual)):
            return True, ""
        return False, "expected text not found in actual object"
    if values_equal(expected, actual):
        return True, ""
    return False, "value mismatch"


def values_equal(expected: Any, actual: Any) -> bool:
    expected_num = parse_number(expected)
    actual_num = parse_number(actual)
    if expected_num is not None and actual_num is not None:
        return abs(expected_num - actual_num) < 1e-6
    return normalize_text(expected) == normalize_text(actual)


def parse_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return None


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value)).strip()


def print_report(case_dir: Path, actual_source: str, results: list[CheckResult]) -> None:
    failed = [result for result in results if result.status == "FAIL"]
    passed = [result for result in results if result.status == "PASS"]
    skipped = [result for result in results if result.status == "SKIP"]

    print("== Case Evaluation ==")
    print(f"case_dir: {case_dir}")
    print(f"actual: {actual_source}")
    print(f"overall: {'PASS' if not failed else 'FAIL'}")
    print(f"summary: PASS {len(passed)} / FAIL {len(failed)} / SKIP {len(skipped)}")
    print()

    for result in results:
        print(f"[{result.status}] {result.name}")
        if result.status == "FAIL":
            print(f"  reason: {result.reason}")
            print(f"  expected: {to_json(result.expected)}")
            print(f"  actual:   {to_json(result.actual)}")
        elif result.status == "SKIP":
            print(f"  reason: {result.reason}")


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
