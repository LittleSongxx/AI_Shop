import pytest

from app.config.settings import Settings
from app.rag.index_contract import index_mapping_body, validate_mapping


def test_validate_mapping_accepts_shared_contract():
    mapping = {
        "aishop_vectorstore": {
            "mappings": {
                "properties": {
                    "embedding": {"type": "dense_vector", "dims": 1024},
                }
            }
        }
    }

    result = validate_mapping(
        mapping,
        index="aishop_vectorstore",
        field="embedding",
        dimensions=1024,
    )

    assert result["ok"] is True
    assert result["errors"] == []


def test_validate_mapping_rejects_non_contract_field_and_dimension():
    mapping = {
        "aishop_vectorstore": {
            "mappings": {
                "properties": {
                    "wrong_vector": {"type": "dense_vector", "dims": 768},
                }
            }
        }
    }

    result = validate_mapping(
        mapping,
        index="aishop_vectorstore",
        field="embedding",
        dimensions=1024,
    )

    assert result["ok"] is False
    assert len(result["errors"]) == 2


def test_mapping_body_uses_configured_field():
    settings = Settings(es_vector_field="embedding")
    field = index_mapping_body(settings)["mappings"]["properties"]["embedding"]

    assert field["type"] == "dense_vector"
    assert field["dims"] == 1024


def test_context_threshold_must_be_below_budget():
    with pytest.raises(ValueError):
        Settings(working_token_budget=1000, compress_token_threshold=1000)
