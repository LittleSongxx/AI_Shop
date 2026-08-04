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


@pytest.mark.parametrize("sample_rate", [-0.01, 1.01])
def test_rag_cache_sample_rate_must_be_a_probability(sample_rate):
    with pytest.raises(ValueError, match="RAG_CACHE_SAMPLE_RATE"):
        Settings(rag_cache_sample_rate=sample_rate)


@pytest.mark.parametrize("worker_port", [0, 65_536, 7050])
def test_worker_metrics_port_must_be_valid_and_separate(worker_port):
    with pytest.raises(ValueError, match="WORKER_METRICS_PORT"):
        Settings(worker_metrics_port=worker_port)


@pytest.mark.parametrize("ttl", [0, 1, 4])
def test_worker_heartbeat_ttl_matches_runtime_minimum(ttl):
    with pytest.raises(ValueError, match="AGENT_WORKER_HEARTBEAT_TTL_SECONDS"):
        Settings(agent_worker_heartbeat_ttl_seconds=ttl)
