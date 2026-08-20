from __future__ import annotations

import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from app.embedded_attachment_inspector import extract_embedded_attachments, inspect_embedded_attachments
from cli import cli


def write_metadata(path: Path, files: list[dict]) -> Path:
    path.write_text(json.dumps({"files": files}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def make_docx(path: Path, rels: str = "", embedding: bool = False) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types><Override PartName="/word/embeddings/oleObject1.bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/></Types>')
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("word/_rels/document.xml.rels", rels or "<Relationships/>")
        if embedding:
            archive.writestr("word/embeddings/oleObject1.bin", b"fake ole")
    return path


def test_docx_word_embeddings_are_detected(tmp_path: Path) -> None:
    docx = make_docx(tmp_path / "with_embed.docx", embedding=True)
    metadata_path = write_metadata(
        tmp_path / "metadata.json",
        [{"id": "file_docx", "original_name": "招标文件-服务.docx", "file_ext": "docx", "path": str(docx)}],
    )

    result = inspect_embedded_attachments("file_docx", metadata_path=metadata_path)

    assert result["has_embedded_attachments"] is True
    assert result["embedded_objects_count"] >= 1
    assert result["ole_objects_count"] >= 1
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert saved["files"][0]["embedded_attachments"]["version"] == "v0"


def test_docx_ole_relationship_is_detected(tmp_path: Path) -> None:
    rels = '<Relationships><Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/oleObject1.bin"/></Relationships>'
    docx = make_docx(tmp_path / "with_rel.docx", rels=rels)
    metadata_path = write_metadata(
        tmp_path / "metadata.json",
        [{"id": "file_docx", "original_name": "技术规范书.docx", "file_ext": "docx", "path": str(docx)}],
    )

    result = inspect_embedded_attachments("file_docx", metadata_path=metadata_path)

    assert result["ole_objects_count"] == 1
    assert result["items"][0]["relationship_id"] == "rId9"


def test_docx_external_relationship_requires_manual_upload(tmp_path: Path) -> None:
    rels = '<Relationships><Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="file:///C:/temp/attachment.xlsx" TargetMode="External"/></Relationships>'
    docx = make_docx(tmp_path / "external.docx", rels=rels)
    metadata_path = write_metadata(
        tmp_path / "metadata.json",
        [{"id": "file_docx", "original_name": "技术规范书.docx", "file_ext": "docx", "path": str(docx)}],
    )

    result = inspect_embedded_attachments("file_docx", metadata_path=metadata_path)

    assert result["external_links_count"] == 1
    assert result["requires_manual_upload"] is True
    assert "人工上传真实附件文件" in result["note"]


def test_legacy_doc_requires_manual_review(tmp_path: Path) -> None:
    metadata_path = write_metadata(
        tmp_path / "metadata.json",
        [{"id": "file_doc", "original_name": "招标文件附件-服务.doc", "file_ext": "doc", "parsed_text": "详见附件，双击图标查看。"}],
    )

    result = inspect_embedded_attachments("file_doc", metadata_path=metadata_path)

    assert result["possible_ole_embedded_objects"] is True
    assert result["inspection_method"] == "legacy_doc_heuristic"
    assert result["requires_manual_review"] is True


def test_legacy_doc_ocr_icon_filename_is_reported(tmp_path: Path) -> None:
    metadata_path = write_metadata(
        tmp_path / "metadata.json",
        [
            {
                "id": "file_doc",
                "original_name": "招标公告附件.doc",
                "file_ext": "doc",
                "parsed_text": "详见附件1.招标需求一览表",
                "assets": [{"name": "image1.wmf", "ocr_text": "需求-览表0425\n13.xlsx", "source_location": "word/media/image1.wmf"}],
            }
        ],
    )

    result = inspect_embedded_attachments("file_doc", metadata_path=metadata_path)

    assert "需求一览表042513.xlsx" in result["suspected_embedded_file_names"]
    assert result["items"][0]["kind"] == "iconHint"
    assert "疑似内嵌文件图标" in result["items"][0]["note"]


def test_extract_legacy_doc_carves_xlsx_payload(tmp_path: Path) -> None:
    xlsx = tmp_path / "payload.xlsx"
    with zipfile.ZipFile(xlsx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    doc = tmp_path / "legacy.doc"
    doc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 128 + xlsx.read_bytes() + b"\x00" * 64)
    metadata_path = write_metadata(
        tmp_path / "metadata.json",
        [
            {
                "id": "file_doc",
                "original_name": "招标公告附件.doc",
                "file_ext": "doc",
                "path": str(doc),
                "assets": [{"name": "image1.wmf", "ocr_text": "需求一览表042513.xlsx"}],
            }
        ],
    )

    result = extract_embedded_attachments("file_doc", metadata_path=metadata_path, output_root=tmp_path / "out")

    assert result["extracted_count"] == 1
    extracted = Path(result["extracted_files"][0]["extracted_path"])
    assert extracted.name == "需求一览表042513.xlsx"
    assert zipfile.is_zipfile(extracted)
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert saved["files"][0]["embedded_attachments"]["extracted_children"][0]["original_name"] == "需求一览表042513.xlsx"

    inspect_embedded_attachments("file_doc", metadata_path=metadata_path)
    saved_after_inspect = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert saved_after_inspect["files"][0]["embedded_attachments"]["extracted_children"][0]["original_name"] == "需求一览表042513.xlsx"


def test_inspect_embedded_attachments_cli_outputs_chinese(monkeypatch) -> None:
    monkeypatch.setattr(
        "cli.inspect_embedded_attachments_service",
        lambda file_id: {
            "has_embedded_attachments": False,
            "embedded_objects_count": 0,
            "external_links_count": 1,
            "requires_manual_upload": True,
            "requires_manual_review": True,
            "note": "检测到疑似链接附件或图标占位，当前未找到可直接抽取的内嵌文件；请人工上传真实附件文件。",
            "items": [],
        },
    )

    result = CliRunner().invoke(cli, ["inspect-embedded-attachments", "file_xxx"])

    assert result.exit_code == 0
    assert "请人工上传真实附件文件" in result.output
