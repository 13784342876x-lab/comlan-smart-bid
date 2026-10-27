from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
STORAGE_DIR = BACKEND_ROOT / "storage"
ORIGINAL_DIR = STORAGE_DIR / "original"
ASSETS_DIR = STORAGE_DIR / "assets"
PARSED_DIR = STORAGE_DIR / "parsed"
DATA_DIR = BACKEND_ROOT / "data"
CHROMA_DIR = DATA_DIR / "chroma"
METADATA_PATH = DATA_DIR / "metadata.json"
CHROMA_COLLECTION = "comlan_kb"

INGEST_ALLOWED_EXTENSIONS = {
    "pdf",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "ppt",
    "pptx",
    "txt",
    "md",
    "png",
    "jpg",
    "jpeg",
    "webp",
}
SOURCE_PACKAGE_ARCHIVE_EXTENSIONS = {"zip"}
# Archive-expansion limits are deliberately centralized because this facility is
# shared by ingest-auto and project-scoped bid ingestion.
SOURCE_PACKAGE_MAX_ARCHIVE_DEPTH = 6
SOURCE_PACKAGE_MAX_FILE_COUNT = 2_000
SOURCE_PACKAGE_MAX_SINGLE_FILE_BYTES = 100 * 1024 * 1024
SOURCE_PACKAGE_MAX_TOTAL_UNCOMPRESSED_BYTES = 1_000 * 1024 * 1024
PARSE_ALLOWED_EXTENSIONS = INGEST_ALLOWED_EXTENSIONS | {"txt"}

MIME_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "txt": "text/plain",
}


def ensure_directories() -> None:
    for path in [ORIGINAL_DIR, ASSETS_DIR, PARSED_DIR, DATA_DIR, CHROMA_DIR]:
        path.mkdir(parents=True, exist_ok=True)
    if not METADATA_PATH.exists():
        METADATA_PATH.write_text('{"files": []}\n', encoding="utf-8")
