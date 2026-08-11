from __future__ import annotations

import json

import pytest

from app.visual.index import VisualProductIndex


class _Response:
    def __init__(self, payload: dict | None = None):
        self._payload = payload or {}
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/_bulk"):
            return _Response({"errors": False})
        return _Response()


@pytest.mark.asyncio
async def test_replace_product_uses_stable_ids_and_version_guard(monkeypatch):
    client = _Client()

    async def get_client(*_args, **_kwargs):
        return client

    monkeypatch.setattr("app.visual.index.get_client", get_client)
    index = VisualProductIndex()
    await index.replace_product(
        "P_100",
        200,
        [
            {
                "documentType": "IMAGE",
                "coverIndex": 0,
                "productName": "测试商品",
                "productText": "测试商品",
                "categoryId": "C1",
                "brand": "品牌",
                "status": 1,
                "minPrice": 1,
                "maxPrice": 2,
                "modelVersion": "v1",
                "indexedAt": "2026-08-10T00:00:00+00:00",
                "imageSha256": "a" * 64,
                "normalizedSha256": "b" * 64,
                "embedding": [0.1] * 1024,
            }
        ],
        index_name="visual-test",
    )

    bulk = next(kwargs for _method, url, kwargs in client.calls if url.endswith("/_bulk"))
    lines = bulk["content"].decode("utf-8").strip().splitlines()
    action = json.loads(lines[0])
    body = json.loads(lines[1])
    assert "update" in action
    assert "200" not in action["update"]["_id"]
    assert body["scripted_upsert"] is True
    assert "params.version >= ctx._source.productVersion" in body["script"]["source"]
    assert body["upsert"]["productVersion"] == 200

    cleanup = next(
        kwargs
        for _method, url, kwargs in client.calls
        if "_delete_by_query" in url
    )
    filters = cleanup["json"]["query"]["bool"]["filter"]
    assert {"range": {"productVersion": {"lte": 200}}} in filters


@pytest.mark.asyncio
async def test_versioned_delete_never_removes_newer_product_documents(monkeypatch):
    client = _Client()

    async def get_client(*_args, **_kwargs):
        return client

    monkeypatch.setattr("app.visual.index.get_client", get_client)
    index = VisualProductIndex()
    await index.delete_product("P_100", product_version=100, index_name="visual-test")

    payload = client.calls[0][2]["json"]
    assert payload == {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"productId": "P_100"}},
                    {"range": {"productVersion": {"lte": 100}}},
                ]
            }
        }
    }


@pytest.mark.parametrize(
    ("score", "expected_cosine"),
    [
        (0.0, -1.0),
        (0.5, 0.0),
        (0.85, 0.7),
        (1.0, 1.0),
        (2.0, 1.0),
    ],
)
def test_knn_es_score_is_converted_back_to_cosine(score, expected_cosine):
    hit = VisualProductIndex._to_hit(
        {
            "_id": "visual-1",
            "_score": score,
            "_source": {
                "productId": "P1",
                "documentType": "IMAGE",
                "coverIndex": 0,
            },
        },
        "image_knn",
    )

    assert hit.score == score
    assert hit.cosine == pytest.approx(expected_cosine)


def test_non_knn_score_is_never_mislabelled_as_cosine():
    hit = VisualProductIndex._to_hit(
        {
            "_id": "visual-1",
            "_score": 12.5,
            "_source": {"productId": "P1", "documentType": "IMAGE"},
        },
        "text",
    )

    assert hit.score == 12.5
    assert hit.cosine is None
