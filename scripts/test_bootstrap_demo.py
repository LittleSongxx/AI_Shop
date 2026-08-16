import json
import inspect
from pathlib import Path

import httpx
import pytest

from scripts.bootstrap_demo import (
    AI_DEMO_MESSAGE,
    BootstrapError,
    find_demo_ai_message,
    load_environment,
    load_knowledge_catalog,
    normalize_agent_message,
    publish_knowledge,
    wait_for_knowledge_contract,
)


def test_load_environment_prefers_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime.env"
    local = tmp_path / ".env.local"
    runtime.write_text("ADMIN_ACCOUNT=runtime-admin\nADMIN_PASSWORD=runtime-secret\n")
    local.write_text("ADMIN_ACCOUNT=local-admin\n")
    monkeypatch.setitem(load_environment.__globals__, "RUNTIME_ENV", runtime)
    monkeypatch.setitem(load_environment.__globals__, "LOCAL_ENV", local)
    monkeypatch.setenv("ADMIN_ACCOUNT", "process-admin")

    values = load_environment()

    assert values["ADMIN_ACCOUNT"] == "process-admin"
    assert values["ADMIN_PASSWORD"] == "runtime-secret"


def test_normalize_agent_message_matches_agent_input_guard() -> None:
    assert normalize_agent_message("  商品，\u200b 推荐\t测试  ") == "商品, 推荐 测试"


def test_find_demo_ai_message_accepts_nfkc_stored_punctuation() -> None:
    stored = normalize_agent_message(AI_DEMO_MESSAGE)
    row = {"messageId": 42, "userMessage": stored, "assistantMessage": "done"}

    assert find_demo_ai_message([row]) is row


def test_repository_knowledge_catalog_locks_twelve_documents_and_75_sections() -> None:
    catalog = load_knowledge_catalog()

    assert catalog["expectedDocumentCount"] == 12
    assert catalog["expectedKnowledgeChunkCount"] == 75
    assert catalog["expectedFaqCount"] == 6
    assert len(catalog["documents"]) == 12
    assert sum(len(item["sections"]) for item in catalog["documents"]) == 75
    assert all(len(item["sha256"]) == 64 for item in catalog["documents"])
    assert all(len(item["normalizedSha256"]) == 64 for item in catalog["documents"])


def test_knowledge_contract_uses_keyword_subfields_for_exact_es_filters() -> None:
    source = inspect.getsource(wait_for_knowledge_contract)

    assert '"metadata.dataType.keyword"' in source
    assert '"metadata.status.keyword"' in source
    assert '"metadata.documentId.keyword"' in source


def test_catalog_rejects_markdown_changed_without_sha_update(tmp_path: Path) -> None:
    markdown = tmp_path / "01.md"
    markdown.write_text("# 标题\n\n## 规则\n\n正文。\n", encoding="utf-8")
    catalog = {
        "schemaVersion": "aishop-knowledge-catalog/v1",
        "catalogVersion": 1,
        "expectedDocumentCount": 1,
        "expectedKnowledgeChunkCount": 1,
        "expectedFaqCount": 6,
        "documents": [
            {
                "file": markdown.name,
                "title": "标题",
                "domain": "GENERAL",
                "sha256": "0" * 64,
                "sections": [
                    {
                        "heading": "规则",
                        "factId": "test.rule",
                        "equivalentRefs": [],
                        "sourceFacts": ["README.md"],
                    }
                ],
            }
        ],
    }
    path = tmp_path / "catalog.v1.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(BootstrapError, match="SHA 不匹配"):
        load_knowledge_catalog(path)


def test_publish_knowledge_passes_catalog_domain_and_skips_published_document(
) -> None:
    catalog = load_knowledge_catalog()
    first = catalog["documents"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        assert b'name="domain"' in body
        assert str(first["domain"]).encode() in body
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "documentId": 1,
                    "sourceName": first["file"],
                    "domain": first["domain"],
                    "status": "PUBLISHED",
                    "indexSchemaVersion": 1,
                },
            },
        )

    single = {**catalog, "documents": [first]}
    with httpx.Client(
        base_url="http://example.test", transport=httpx.MockTransport(handler)
    ) as client:
        assert publish_knowledge(client, single) == (0, 1)


def test_publish_knowledge_reindexes_legacy_published_document() -> None:
    catalog = load_knowledge_catalog()
    first = catalog["documents"][0]
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/upload"):
            data = {
                "documentId": 7,
                "sourceName": first["file"],
                "domain": first["domain"],
                "status": "PUBLISHED",
                "indexSchemaVersion": 0,
            }
        else:
            assert request.url.path.endswith("/reindex")
            data = {
                "documentId": 7,
                "sourceName": first["file"],
                "domain": first["domain"],
                "status": "PUBLISHED",
                "indexSchemaVersion": 1,
                "reindexed": True,
            }
        return httpx.Response(200, json={"code": 200, "data": data})

    single = {**catalog, "documents": [first]}
    with httpx.Client(
        base_url="http://example.test", transport=httpx.MockTransport(handler)
    ) as client:
        assert publish_knowledge(client, single) == (0, 1)

    assert requests == [
        "/admin-api/knowledge/upload",
        "/admin-api/knowledge/reindex",
    ]
