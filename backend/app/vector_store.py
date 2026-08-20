import hashlib
from typing import Any

import chromadb
from chromadb.api.types import EmbeddingFunction

from config import CHROMA_COLLECTION, CHROMA_DIR, ensure_directories


class MockEmbeddingFunction(EmbeddingFunction):
    def __call__(self, input: list[str]) -> list[list[float]]:
        return [mock_embedding(text) for text in input]


def mock_embedding(text: str, dimensions: int = 64) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < dimensions:
        for byte in digest:
            values.append((byte / 255.0) * 2 - 1)
            if len(values) >= dimensions:
                break
        digest = hashlib.sha256(digest).digest()
    return values


class VectorStore:
    def __init__(self) -> None:
        ensure_directories()
        self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.collection = self.client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            embedding_function=MockEmbeddingFunction(),
        )

    def index_text(self, record: dict[str, Any], chunks: list[str], item: dict[str, Any] | None = None) -> int:
        base_id = item["id"] if item else record["id"]
        ids = [f"{base_id}_{index}" for index, _ in enumerate(chunks)]
        metadatas = [
            {
                "file_id": record["id"],
                "split_item_id": item["id"] if item else "",
                "original_name": item["name"] if item else record["original_name"],
                "document_type": item.get("document_type", "") if item else "",
                "source_file_name": record["original_name"],
                "source_name": record["original_name"],
                "category": item["category"] if item else record["category"],
                "tags": ",".join(item.get("tags", []) if item else record.get("tags", [])),
            }
            for _ in chunks
        ]
        self.collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)
        return len(chunks)

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
        result = self.collection.query(query_texts=[query], n_results=top_k)
        rows = []
        for doc, meta, distance in zip(
            result.get("documents", [[]])[0],
            result.get("metadatas", [[]])[0],
            result.get("distances", [[]])[0],
        ):
            rows.append(
                {
                    "file_id": meta.get("file_id"),
                    "split_item_id": meta.get("split_item_id"),
                    "original_name": meta.get("original_name"),
                    "category": meta.get("category"),
                    "tags": meta.get("tags", ""),
                    "matched_text": doc,
                    "distance": distance,
                }
            )
        return rows


def chunk_text(text: str, chunk_size: int = 500) -> list[str]:
    text = text.strip()
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] if text else []
