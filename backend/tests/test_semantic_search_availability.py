import pytest

from app import vector_store, task_material_matcher


def test_unconfigured_search_and_index_never_open_or_write_database(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Database must not be opened for unavailable semantic operations")
    monkeypatch.setattr(vector_store.chromadb, "PersistentClient", forbidden)
    store = vector_store.VectorStore()
    with pytest.raises(vector_store.SemanticSearchUnavailable, match="语义检索不可用"):
        store.search("requirements")
    with pytest.raises(vector_store.SemanticSearchUnavailable):
        store.index_text({"id": "test"}, ["actual text"])


def test_existing_index_inspection_uses_no_embedding_or_collection_creation(monkeypatch):
    class Collection:
        def get(self, **kwargs):
            return {"ids": [], "metadatas": [], "documents": []}
        def count(self):
            return 7
    class Client:
        def get_collection(self, **kwargs):
            assert kwargs["embedding_function"] is None
            return Collection()
    monkeypatch.setattr(vector_store.chromadb, "PersistentClient", lambda **kwargs: Client())
    result = vector_store.VectorStore().inspect_by_file_id("test")
    assert result["collection_count"] == 7


def test_material_match_keeps_unavailable_distinct_from_no_match(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No database access expected")
    monkeypatch.setattr(vector_store.chromadb, "PersistentClient", forbidden)
    result = task_material_matcher.match_materials_for_project({
        "generation_tasks": [{"task_id": "one", "target_section": "服务方案"}],
    })
    assert result["status"] == "partial"
    assert result["results"][0]["status"] == "unavailable"
    assert result["results"][0]["matched_materials"] == []


def test_search_cli_reports_unavailability(monkeypatch):
    import cli
    from typer.testing import CliRunner
    class KB:
        def search(self, *args, **kwargs):
            raise vector_store.SemanticSearchUnavailable(vector_store.UNAVAILABLE_MESSAGE)
    monkeypatch.setattr(cli, "KnowledgeBaseService", KB)
    result = CliRunner().invoke(cli.cli, ["search-kb", "query"])
    assert result.exit_code == 1
    assert '"status": "unavailable"' in result.stdout
