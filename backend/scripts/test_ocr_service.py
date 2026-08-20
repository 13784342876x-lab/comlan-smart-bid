import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.ocr_service as ocr_service
from app.ocr_service import (
    extract_ocr_text,
    get_ocr_diagnostics,
    is_ocr_available,
    normalize_paddleocr_result,
    ocr_assets,
    ocr_image,
)


def main() -> None:
    assert os.environ.get("FLAGS_use_mkldnn") == "0"
    assert os.environ.get("FLAGS_enable_pir_api") == "0"
    diagnostics = get_ocr_diagnostics()
    available = is_ocr_available("paddleocr")

    missing_image = ocr_image(str(BACKEND_ROOT / "missing-image-for-ocr.png"))
    assert missing_image["ocr_status"] == "failed"

    empty_assets = ocr_assets([])
    assert empty_assets == []

    non_image = [{"id": "asset_txt_001", "asset_type": "text", "text": "not an image"}]
    assert ocr_assets(non_image) == non_image

    missing_path_assets = [{"id": "asset_missing_001", "asset_type": "image", "asset_key": "assets/not-found.png"}]
    missing_path_result = ocr_assets(missing_path_assets)
    assert missing_path_result[0]["ocr_status"] == "failed"
    assert "not found" in (missing_path_result[0].get("ocr_error") or "")

    if not available:
        skipped = ocr_image(__file__)
        assert skipped["ocr_status"] == "skipped"
    else:
        previous_instance = ocr_service._PADDLE_OCR_INSTANCE

        class FailingOCR:
            def predict(self, image_path):
                raise TypeError("simulated runtime failure")

        ocr_service._PADDLE_OCR_INSTANCE = FailingOCR()
        failed = ocr_image(__file__)
        assert failed["ocr_status"] == "failed"
        assert "simulated runtime failure" in (failed.get("error") or "")
        ocr_service._PADDLE_OCR_INSTANCE = previous_instance

    paddle3_lines = normalize_paddleocr_result(
        {
            "rec_texts": ["VMware Certified Professional", "Certificate ID ABC123"],
            "rec_scores": [0.91, 0.81],
            "dt_polys": [[[0, 0], [10, 0], [10, 10], [0, 10]], [[0, 20], [10, 20], [10, 30], [0, 30]]],
        }
    )
    assert [line["text"] for line in paddle3_lines] == ["VMware Certified Professional", "Certificate ID ABC123"]
    assert round(sum(line["confidence"] for line in paddle3_lines) / len(paddle3_lines), 2) == 0.86
    paddle3_text = extract_ocr_text(
        {
            "rec_texts": ["VMware Certified Professional", "Certificate ID ABC123"],
            "rec_scores": [0.91, 0.81],
            "dt_polys": [[[0, 0], [10, 0], [10, 10], [0, 10]], [[0, 20], [10, 20], [10, 30], [0, 30]]],
        }
    )
    assert isinstance(paddle3_text["ocr_text"], str)
    assert "VMware Certified Professional" in paddle3_text["ocr_text"]
    assert paddle3_text["ocr_confidence"] > 0

    old_lines = normalize_paddleocr_result(
        [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("NUTANIX NCP", 0.88)]]
    )
    assert old_lines[0]["text"] == "NUTANIX NCP"
    assert old_lines[0]["confidence"] == 0.88
    old_text = extract_ocr_text([[[[0, 0], [1, 0], [1, 1], [0, 1]], ("NUTANIX NCP", 0.88)]])
    assert old_text["ocr_text"] == "NUTANIX NCP"
    assert isinstance(old_text["ocr_text"], str)
    assert old_text["ocr_confidence"] == 0.88

    bbox_only = normalize_paddleocr_result([[[189.0, 138.0], [503.0, 138.0], [503.0, 168.0], [189.0, 168.0]]])
    assert bbox_only == []
    bbox_text = extract_ocr_text({"rec_texts": [[[189.0, 138.0], [503.0, 138.0]]], "rec_scores": [0.99]})
    assert isinstance(bbox_text["ocr_text"], str)
    assert bbox_text["ocr_text"] == ""
    assert "[[189.0, 138.0]" not in bbox_text["ocr_text"]

    print(
        json.dumps(
            {
                "paddleocr_available": available,
                "FLAGS_use_mkldnn": diagnostics.get("FLAGS_use_mkldnn"),
                "FLAGS_enable_pir_api": diagnostics.get("FLAGS_enable_pir_api"),
                "paddleocr_version": diagnostics.get("paddleocr_version"),
                "paddle_version": diagnostics.get("paddle_version"),
                "missing_image_status": missing_image["ocr_status"],
                "missing_path_asset_status": missing_path_result[0]["ocr_status"],
                "skipped_logic": not available,
                "format_parser_cases": ["paddleocr_3_dict", "old_list_tuple", "bbox_not_text"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("ocr service tests passed")


if __name__ == "__main__":
    main()
