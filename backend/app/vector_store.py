from typing import Any

import chromadb

from config import CHROMA_COLLECTION, CHROMA_DIR


class SemanticSearchUnavailable(RuntimeError):
    """No verified semantic embedding/index is configured."""


UNAVAILABLE_MESSAGE = "语义检索不可用：尚未配置并验证真实 embedding 与独立索引；旧哈希索引仅供检查。可使用 query-knowledge 进行结构化与关键词查询。"


class VectorStore:
    def __init__(self) -> None:
        self._collection = None

    @property
    def collection(self):
        # Inspection remains available without generating new vectors or creating a collection.
        if self._collection is None:
            self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            self._collection = self.client.get_collection(
                name=CHROMA_COLLECTION, embedding_function=None,
            )
        return self._collection

    def index_text(self, record: dict[str, Any], chunks: list[str], item: dict[str, Any] | None = None) -> int:
        raise SemanticSearchUnavailable(UNAVAILABLE_MESSAGE)

    def inspect_by_file_id(self, file_id: str, limit: int | None = None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"where": {"file_id": file_id}, "include": ["metadatas", "documents"]}
        if isinstance(limit, int) and limit > 0:
            kwargs["limit"] = limit
        result = self.collection.get(**kwargs)
        ids = list(result.get("ids") or [])
        metadatas = list(result.get("metadatas") or [])
        documents = list(result.get("documents") or [])
        items = []
        for vector_id, metadata, document in zip(ids, metadatas, documents):
            items.append(
                {
                    "vector_id": vector_id,
                    "file_id": metadata.get("file_id"),
                    "split_item_id": metadata.get("split_item_id"),
                    "name": metadata.get("original_name"),
                    "category": metadata.get("category"),
                    "document_type": metadata.get("document_type", ""),
                    "source_name": metadata.get("source_name") or metadata.get("source_file_name", ""),
                    "tags": metadata.get("tags", ""),
                    "matched_text": document or "",
                }
            )
        return {
            "file_id": file_id,
            "collection_count": self.collection.count(),
            "matched_count": len(ids),
            "items": items,
        }

    def delete_by_file_id(self, file_id: str) -> dict[str, Any]:
        before_count = self.collection.count()
        existing = self.collection.get(where={"file_id": file_id}, include=["metadatas"])
        existing_ids = list(existing.get("ids") or [])
        if existing_ids:
            self.collection.delete(where={"file_id": file_id})
        after_count = self.collection.count()
        return {
            "file_id": file_id,
            "before_count": before_count,
            "after_count": after_count,
            "deleted_count": len(existing_ids),
        }

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        raise SemanticSearchUnavailable(UNAVAILABLE_MESSAGE)



def chunk_text(text: str, chunk_size: int = 500) -> list[str]:
    text = text.strip()
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] if text else []
