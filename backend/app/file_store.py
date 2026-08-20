import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from app.schemas import KnowledgeFile
from config import INGEST_ALLOWED_EXTENSIONS, MIME_TYPES, ORIGINAL_DIR, STORAGE_DIR, ensure_directories


def validate_ingest_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"file not found: {path}")
    ext = path.suffix.lower().lstrip(".")
    if ext not in INGEST_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(INGEST_ALLOWED_EXTENSIONS))
        raise ValueError(f"unsupported file type; allowed: {allowed}")
    return ext


def safe_name(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", stem).strip("_")
    return stem[:48] or "file"


def ingest_file(path: Path, category: str, tags: list[str]) -> KnowledgeFile:
    ensure_directories()
    ext = validate_ingest_file(path)
    file_id = f"file_{uuid.uuid4().hex[:12]}"
    year = datetime.now().strftime("%Y")
    target_dir = ORIGINAL_DIR / year
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{safe_name(path.name)}_{file_id}.{ext}"
    target_path = target_dir / target_name
    shutil.copyfile(path, target_path)
    return KnowledgeFile(
        id=file_id,
        original_name=path.name,
        file_ext=ext,
        mime_type=MIME_TYPES.get(ext, "application/octet-stream"),
        size=path.stat().st_size,
        category=category,
        tags=tags,
        storage_key=f"original/{year}/{target_name}",
        uploaded_at=datetime.now().isoformat(timespec="seconds"),
    )


def resolve_storage_key(storage_key: str) -> Path:
    return STORAGE_DIR / storage_key
