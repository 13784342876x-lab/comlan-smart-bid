from typing import Literal

from pydantic import BaseModel, Field

KnowledgeStatus = Literal["有效", "即将过期", "已过期", "未设置有效期"]
SplitStatus = Literal["pending", "confirmed", "ignored"]
InsertMode = Literal["page_width", "half_width", "original_ratio", "table_cell"]


class KnowledgeAsset(BaseModel):
    id: str
    file_id: str
    name: str
    asset_type: str
    source_page: str
    asset_key: str
    preview_url: str | None = None
    ocr_text: str
    recommended_usage: str
    insertable: bool = True
    insert_mode: InsertMode = "page_width"


class DetectedItem(BaseModel):
    id: str
    source_file_id: str
    name: str
    category: str
    document_type: str
    tags: list[str] = Field(default_factory=list)
    valid_from: str | None = None
    valid_to: str | None = None
    source_page: str
    source_text: str
    evidence_text: str = ""
    evidence_level: str = "explicit"
    related_asset_ids: list[str] = Field(default_factory=list)
    recommended_usage: str
    confidence: float
    status: SplitStatus = "pending"


class FileClassification(BaseModel):
    source_file_id: str
    category: str
    document_type: str
    material_group: str | None = None
    source_type: str | None = None
    material_types: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    valid_from: str | None = None
    valid_to: str | None = None
    recommended_usage: str
    confidence: float
    is_bundle: bool = False


class PendingKnowledgeItem(BaseModel):
    id: str
    source_file_id: str
    name: str
    category: str
    document_type: str
    tags: list[str] = Field(default_factory=list)
    valid_from: str | None = None
    valid_to: str | None = None
    source_text: str
    evidence_text: str = ""
    evidence_level: str = "explicit"
    related_asset_ids: list[str] = Field(default_factory=list)
    recommended_usage: str
    confidence: float
    status: SplitStatus = "pending"
    created_at: str


class KnowledgeFile(BaseModel):
    id: str
    original_name: str
    file_ext: str
    mime_type: str
    size: int
    category: str
    tags: list[str] = Field(default_factory=list)
    storage_type: str = "local"
    storage_key: str
    uploaded_at: str
    valid_from: str | None = None
    valid_to: str | None = None
    status: KnowledgeStatus = "未设置有效期"
    parsed_text: str = ""
    assets: list[KnowledgeAsset] = Field(default_factory=list)
    detected_items: list[DetectedItem] = Field(default_factory=list)
    classification: FileClassification | None = None
