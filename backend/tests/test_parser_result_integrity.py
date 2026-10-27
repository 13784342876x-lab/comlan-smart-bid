from pathlib import Path
from types import SimpleNamespace

import fitz

from app import document_parser, kb_service


def test_pdf_requires_actual_text_and_preserves_short_text(tmp_path):
    path = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(path)
    result = document_parser.parse_pdf(path, {})
    assert result["ok"] is False
    assert result["parsed_text"] == ""
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Short valid text")
        document.new_page()
        document.save(path)
    result = document_parser.parse_pdf(path, {})
    assert result["ok"] is True
    assert "Short valid text" in result["parsed_text"]
    assert any("[2]" in warning for warning in result["warnings"])


def test_failed_reparse_preserves_diagnostics_not_active_text(monkeypatch):
    record = {"id": "source", "file_ext": "docx", "parsed_text": "previous valid text"}

    class MemoryMetadata:
        def get_file(self, file_id):
            return dict(record)

        def update_file(self, file_id, patch):
            record.update(patch)
            return dict(record)

    monkeypatch.setattr(kb_service, "parse_document", lambda record: {
        "ok": False, "parsed_text": "partial current text", "message": "failed",
    })
    service = kb_service.KnowledgeBaseService(metadata=MemoryMetadata())
    for _ in range(2):
        result = service.parse_file("source")
        assert not result["ok"]
        assert result["parsed_text"] == ""
        assert result["record"]["previous_parsed_text"] == "previous valid text"
        assert result["record"]["failed_parse_text"] == "partial current text"


def test_conversion_never_uses_unrelated_output_and_isolates_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(document_parser, "PARSED_DIR", tmp_path)
    monkeypatch.setattr(document_parser, "find_soffice", lambda: Path("soffice"))
    output_dirs = []

    def convert(command, **kwargs):
        output_dir = Path(command[command.index("--outdir") + 1])
        output_dirs.append(output_dir)
        (output_dir / "unrelated.docx").write_text("unrelated")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    def unexpected_parser(*args):
        raise AssertionError("Unrelated output must never be parsed")

    monkeypatch.setattr(document_parser.subprocess, "run", convert)
    for _ in range(2):
        result = document_parser.parse_via_libreoffice(
            tmp_path / "source.doc", {"file_ext": "doc"}, "docx", unexpected_parser,
        )
        assert result["ok"] is False
    assert output_dirs[0] != output_dirs[1]
