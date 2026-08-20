from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

# Windows CPU 上 PaddlePaddle 3.x 的 oneDNN/PIR 组合可能触发
# ConvertPirAttribute2RuntimeAttribute 兼容错误。OCR 服务默认关闭这两项，
# 使用 setdefault 保留用户显式设置的覆盖能力。
# Current tested local stack: paddleocr==3.7.0, paddlepaddle==3.3.1.
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")

from config import BACKEND_ROOT, DATA_DIR, METADATA_PATH, STORAGE_DIR


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PATH_FIELDS = ["path", "file_path", "saved_path", "local_path", "asset_path", "relative_path", "asset_key", "preview_url"]
_PADDLE_OCR_INSTANCE: Any | None = None


class PaddleOCRNotInstalled(Exception):
    pass


def is_ocr_available(engine: str = "paddleocr") -> bool:
    if engine != "paddleocr":
        return False
    try:
        import paddleocr  # noqa: F401
    except Exception:
        return False
    return True


def get_ocr_diagnostics(engine: str = "paddleocr") -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "FLAGS_use_mkldnn": os.environ.get("FLAGS_use_mkldnn"),
        "FLAGS_enable_pir_api": os.environ.get("FLAGS_enable_pir_api"),
        "paddleocr_version": None,
        "paddle_version": None,
        "ocr_available": is_ocr_available(engine),
        "recommended_versions": {
            "paddleocr": "3.7.0",
            "paddlepaddle": "3.3.1",
        },
    }
    if engine != "paddleocr":
        return diagnostics
    try:
        import paddleocr

        diagnostics["paddleocr_version"] = getattr(paddleocr, "__version__", "unknown")
    except Exception:
        pass
    try:
        import paddle

        diagnostics["paddle_version"] = getattr(paddle, "__version__", "unknown")
    except Exception:
        pass
    return diagnostics


def ocr_image(image_path: str, engine: str = "paddleocr") -> dict[str, Any]:
    if engine != "paddleocr":
        return empty_ocr_result("skipped", engine, error=f"unsupported OCR engine: {engine}")

    path = Path(image_path)
    if not path.exists() or not path.is_file():
        return empty_ocr_result("failed", engine, error=f"image path not found: {image_path}")

    if not is_ocr_available(engine):
        return empty_ocr_result(
            "skipped",
            engine,
            error="PaddleOCR is not installed; optional install: pip install paddleocr",
        )

    try:
        raw_result = _run_paddleocr(str(path))
        extracted = extract_ocr_text(raw_result)
        return {
            "ocr_status": "success",
            "ocr_engine": engine,
            "ocr_text": extracted["ocr_text"],
            "ocr_confidence": extracted["ocr_confidence"],
            "ocr_lines": extracted["ocr_lines"],
            "error": None,
        }
    except Exception as exc:
        return empty_ocr_result("failed", engine, error=str(exc))


def _run_paddleocr(image_path: str) -> Any:
    ocr = _get_paddle_ocr_instance()
    if hasattr(ocr, "predict"):
        return ocr.predict(image_path)
    if hasattr(ocr, "ocr"):
        return ocr.ocr(image_path)
    raise RuntimeError("PaddleOCR instance has neither predict nor ocr method")


def _get_paddle_ocr_instance() -> Any:
    global _PADDLE_OCR_INSTANCE
    if _PADDLE_OCR_INSTANCE is not None:
        return _PADDLE_OCR_INSTANCE
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        raise PaddleOCRNotInstalled(str(exc)) from exc
    _PADDLE_OCR_INSTANCE = PaddleOCR(lang="ch")
    return _PADDLE_OCR_INSTANCE


def ocr_assets(
    assets: list[dict[str, Any]],
    base_dir: str | None = None,
    engine: str = "paddleocr",
    force: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    processed = 0
    for asset in assets:
        item = dict(asset)
        if not is_image_asset(item):
            updated.append(item)
            continue
        clear_stale_unavailable_status(item, engine)
        if limit is not None and processed >= limit:
            updated.append(item)
            continue
        if not force and item.get("ocr_status") == "success" and str(item.get("ocr_text") or "").strip():
            item["_ocr_already_done"] = True
            updated.append(item)
            continue

        processed += 1
        image_path = resolve_asset_path(item, base_dir=base_dir)
        if image_path is None:
            result = empty_ocr_result("failed", engine, error="image path not found for asset")
        else:
            result = ocr_image(str(image_path), engine=engine)

        item["ocr_status"] = result.get("ocr_status")
        item["ocr_engine"] = result.get("ocr_engine")
        item["ocr_text"] = result.get("ocr_text", "")
        item["ocr_confidence"] = result.get("ocr_confidence", 0.0)
        item["ocr_lines"] = result.get("ocr_lines", [])
        item["ocr_error"] = result.get("error")
        item["ocr_updated_at"] = datetime.now().isoformat(timespec="seconds")
        if image_path:
            item.setdefault("local_path", str(image_path))
        updated.append(item)
    return updated


def clear_stale_unavailable_status(asset: dict[str, Any], engine: str) -> None:
    if not is_ocr_available(engine):
        return
    if asset.get("ocr_status") != "skipped":
        return
    error = str(asset.get("ocr_error") or "")
    if "not installed" not in error and "No module named" not in error:
        return
    asset["ocr_status"] = "not_run"
    asset["ocr_engine"] = engine
    asset["ocr_text"] = ""
    asset["ocr_confidence"] = 0.0
    asset["ocr_lines"] = []
    asset["ocr_error"] = None


def resolve_asset_path(asset: dict[str, Any], base_dir: str | None = None) -> Path | None:
    candidates: list[Path] = []
    base_paths = [
        Path(base_dir) if base_dir else None,
        BACKEND_ROOT,
        DATA_DIR,
        METADATA_PATH.parent,
        STORAGE_DIR,
    ]
    base_paths = [path for path in base_paths if path is not None]

    for field in PATH_FIELDS:
        value = asset.get(field)
        if not value:
            continue
        raw = Path(str(value))
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.extend(base / raw for base in base_paths)
            if str(value).startswith("assets/"):
                candidates.append(STORAGE_DIR / str(value))

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def is_image_asset(asset: dict[str, Any]) -> bool:
    asset_type = str(asset.get("asset_type") or "").lower()
    if asset_type and asset_type != "image":
        return False
    values = [str(asset.get(field) or "") for field in PATH_FIELDS + ["name"]]
    suffixes = {Path(value).suffix.lower() for value in values if value}
    return asset_type == "image" or bool(suffixes & IMAGE_EXTENSIONS)


def empty_ocr_result(status: str, engine: str, error: str | None = None) -> dict[str, Any]:
    return {
        "ocr_status": status,
        "ocr_engine": engine,
        "ocr_text": "",
        "ocr_confidence": 0.0,
        "ocr_lines": [],
        "error": error,
    }


def normalize_paddleocr_result(raw_result: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    _collect_ocr_lines(raw_result, lines)
    return lines


def extract_ocr_text(result: Any) -> dict[str, Any]:
    lines = normalize_paddleocr_result(result)
    texts: list[str] = []
    confidence_values: list[float] = []
    normalized_lines: list[dict[str, Any]] = []

    for line in lines:
        raw_text = line.get("text")
        _raise_if_bbox_text(raw_text)
        if not isinstance(raw_text, str):
            continue
        text = raw_text.strip()
        if not text:
            continue
        confidence = _safe_float(line.get("confidence"))
        normalized_lines.append(
            {
                "text": text,
                "confidence": confidence,
                "box": line.get("box"),
            }
        )
        texts.append(text)
        if isinstance(line.get("confidence"), (int, float)):
            confidence_values.append(confidence)

    ocr_text = "\n".join(texts).strip()
    _raise_if_bbox_text(ocr_text)
    return {
        "ocr_text": ocr_text,
        "ocr_confidence": float(mean(confidence_values)) if confidence_values else 0.0,
        "ocr_lines": normalized_lines,
    }


def _collect_ocr_lines(result: Any, lines: list[dict[str, Any]]) -> None:
    if result is None:
        return
    normalized = _object_to_mapping(result)
    if normalized is not result:
        _collect_ocr_lines(normalized, lines)
        return
    if isinstance(result, dict):
        if _collect_from_text_score_fields(result, lines):
            return
        for key in ["res", "result", "data", "ocr_result", "results"]:
            if key in result:
                _collect_ocr_lines(result[key], lines)
        return
    if isinstance(result, str):
        text = result.strip()
        if text:
            lines.append({"text": text, "confidence": 0.0, "box": None})
        return
    if isinstance(result, (list, tuple)):
        if _looks_like_old_line(result):
            lines.append(_old_line_to_dict(result))
            return
        for item in result:
            _collect_ocr_lines(item, lines)


def _object_to_mapping(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, str)) or value is None:
        return value
    for attr in ["to_dict", "dict"]:
        method = getattr(value, attr, None)
        if callable(method):
            try:
                mapped = method()
                if mapped is not value:
                    return mapped
            except Exception:
                pass
    json_value = getattr(value, "json", None)
    if callable(json_value):
        try:
            json_value = json_value()
        except Exception:
            json_value = None
    if isinstance(json_value, dict):
        return json_value
    mapped = {}
    for key in ["rec_texts", "rec_scores", "dt_polys", "rec_boxes", "boxes"]:
        if hasattr(value, key):
            mapped[key] = getattr(value, key)
    return mapped if mapped else value


def _collect_from_text_score_fields(result: dict[str, Any], lines: list[dict[str, Any]]) -> bool:
    texts = result.get("rec_texts") or result.get("texts") or result.get("text")
    scores = result.get("rec_scores") or result.get("scores") or result.get("confidence")
    boxes = result.get("dt_polys") or result.get("rec_boxes") or result.get("boxes") or result.get("box")
    if texts is None:
        return False
    if isinstance(texts, str):
        text_list = [texts]
    elif isinstance(texts, (list, tuple)):
        text_list = list(texts)
    else:
        return False
    score_list = _as_list(scores, len(text_list), 0.0)
    box_list = _as_list(boxes, len(text_list), None)
    added = False
    for text, score, box in zip(text_list, score_list, box_list):
        if isinstance(text, str) and text.strip():
            lines.append({"text": text.strip(), "confidence": _safe_float(score), "box": box})
            added = True
    return added


def _looks_like_old_line(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return False
    text_info = value[1]
    if not isinstance(text_info, (list, tuple)) or len(text_info) < 1:
        return False
    if not isinstance(text_info[0], str):
        return False
    if len(text_info) >= 2 and not isinstance(text_info[1], (int, float)):
        return False
    return True


def _old_line_to_dict(value: Any) -> dict[str, Any]:
    box = value[0]
    text_info = value[1]
    text = text_info[0] if len(text_info) >= 1 else ""
    confidence = text_info[1] if len(text_info) >= 2 else 0.0
    return {"text": text.strip(), "confidence": _safe_float(confidence), "box": box}


def _raise_if_bbox_text(value: Any) -> None:
    if isinstance(value, list) and value and isinstance(value[0], list):
        raise ValueError("OCR text parsing failed: bbox returned instead of text")


def _as_list(value: Any, length: int, default: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        result = list(value)
    elif value is None:
        result = []
    else:
        result = [value]
    if len(result) < length:
        result.extend([default] * (length - len(result)))
    return result[:length]


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0
