from __future__ import annotations

from typing import Any

from app.config.settings import Settings, get_settings
from app.infra.http_client import get_client

VECTOR_SIMILARITY = "cosine"
VECTOR_INDEX_TYPE = "int8_hnsw"
VECTOR_HNSW_M = 16
VECTOR_HNSW_EF_CONSTRUCTION = 100


def validate_mapping(
    mapping: dict[str, Any],
    *,
    index: str,
    field: str,
    dimensions: int,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    contract_version: int | None = None,
) -> dict[str, Any]:
    index_mapping = mapping.get(index)
    if not isinstance(index_mapping, dict) and len(mapping) == 1:
        index_mapping = next(iter(mapping.values()))
    properties = (
        (index_mapping or {}).get("mappings", {}).get("properties", {})
        if isinstance(index_mapping, dict)
        else {}
    )
    field_mapping = properties.get(field) if isinstance(properties, dict) else None
    actual_type = field_mapping.get("type") if isinstance(field_mapping, dict) else None
    actual_dimensions = field_mapping.get("dims") if isinstance(field_mapping, dict) else None
    actual_indexed = field_mapping.get("index") if isinstance(field_mapping, dict) else None
    actual_similarity = (
        field_mapping.get("similarity") if isinstance(field_mapping, dict) else None
    )
    index_options = (
        field_mapping.get("index_options") if isinstance(field_mapping, dict) else None
    )
    actual_index_type = (
        index_options.get("type") if isinstance(index_options, dict) else None
    )
    actual_hnsw_m = (
        index_options.get("m") if isinstance(index_options, dict) else None
    )
    actual_ef_construction = (
        index_options.get("ef_construction")
        if isinstance(index_options, dict)
        else None
    )
    mappings = (
        (index_mapping or {}).get("mappings", {})
        if isinstance(index_mapping, dict)
        else {}
    )
    metadata = mappings.get("_meta", {}) if isinstance(mappings, dict) else {}
    contract = (
        metadata.get("aishopEmbeddingContract")
        if isinstance(metadata, dict)
        else None
    )
    actual_provider = (
        contract.get("embeddingProvider") if isinstance(contract, dict) else None
    )
    actual_model = contract.get("embeddingModel") if isinstance(contract, dict) else None
    actual_contract_dimensions = (
        contract.get("embeddingDimensions") if isinstance(contract, dict) else None
    )
    actual_contract_version = (
        contract.get("contractVersion") if isinstance(contract, dict) else None
    )
    errors: list[str] = []
    if actual_type != "dense_vector":
        errors.append(
            f"field '{field}' must be dense_vector, got {actual_type or 'missing'}"
        )
    if actual_dimensions != dimensions:
        errors.append(
            f"field '{field}' must have {dimensions} dimensions, "
            f"got {actual_dimensions or 'missing'}"
        )
    if actual_indexed is not True:
        errors.append(f"field '{field}' must be indexed")
    if actual_similarity != VECTOR_SIMILARITY:
        errors.append(
            f"field '{field}' must use {VECTOR_SIMILARITY} similarity, "
            f"got {actual_similarity or 'missing'}"
        )
    if (
        actual_index_type != VECTOR_INDEX_TYPE
        or actual_hnsw_m != VECTOR_HNSW_M
        or actual_ef_construction != VECTOR_HNSW_EF_CONSTRUCTION
    ):
        errors.append(
            f"field '{field}' must use {VECTOR_INDEX_TYPE} "
            f"(m={VECTOR_HNSW_M}, ef_construction={VECTOR_HNSW_EF_CONSTRUCTION})"
        )
    if embedding_provider is not None:
        if not isinstance(contract, dict):
            errors.append("embedding contract metadata is missing")
        elif (
            actual_provider != embedding_provider
            or actual_model != embedding_model
            or actual_contract_dimensions != dimensions
            or actual_contract_version != contract_version
        ):
            errors.append(
                "embedding contract mismatch: expected "
                f"{embedding_provider}/{embedding_model}/{dimensions}/v{contract_version}, "
                f"got {actual_provider}/{actual_model}/"
                f"{actual_contract_dimensions}/v{actual_contract_version}"
            )
    return {
        "ok": not errors,
        "index": index,
        "field": field,
        "expectedDimensions": dimensions,
        "actualType": actual_type,
        "actualDimensions": actual_dimensions,
        "actualIndexed": actual_indexed,
        "actualSimilarity": actual_similarity,
        "actualIndexType": actual_index_type,
        "actualHnswM": actual_hnsw_m,
        "actualEfConstruction": actual_ef_construction,
        "embeddingProvider": actual_provider,
        "embeddingModel": actual_model,
        "embeddingContractDimensions": actual_contract_dimensions,
        "contractVersion": actual_contract_version,
        "errors": errors,
    }


def effective_embedding_model(settings: Settings) -> str:
    return (
        "local-hash-v1"
        if settings.embedding_provider == "local"
        else settings.embedding_model
    )


def embedding_contract(settings: Settings) -> dict[str, Any]:
    return {
        "embeddingProvider": settings.embedding_provider,
        "embeddingModel": effective_embedding_model(settings),
        "embeddingDimensions": settings.es_vector_dimensions,
        "contractVersion": settings.vector_index_schema_version,
    }


def index_mapping_body(settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    return {
        "mappings": {
            "_meta": {"aishopEmbeddingContract": embedding_contract(current)},
            "properties": {
                "content": {"type": "text"},
                "metadata": {"type": "object", "dynamic": True},
                current.es_vector_field: {
                    "type": "dense_vector",
                    "dims": current.es_vector_dimensions,
                    "index": True,
                    "similarity": VECTOR_SIMILARITY,
                    "index_options": {
                        "type": VECTOR_INDEX_TYPE,
                        "m": VECTOR_HNSW_M,
                        "ef_construction": VECTOR_HNSW_EF_CONSTRUCTION,
                    },
                },
            }
        }
    }


class VectorIndexContract:
    async def check(self) -> dict[str, Any]:
        settings = get_settings()
        base = settings.es_hosts.split(",")[0].rstrip("/")
        try:
            client = await get_client("es", timeout=20)
            response = await client.get(
                f"{base}/{settings.es_index}/_mapping", timeout=5
            )
            response.raise_for_status()
            return validate_mapping(
                response.json(),
                index=settings.es_index,
                field=settings.es_vector_field,
                dimensions=settings.es_vector_dimensions,
                embedding_provider=settings.embedding_provider,
                embedding_model=effective_embedding_model(settings),
                contract_version=settings.vector_index_schema_version,
            )
        except Exception as exc:
            return {
                "ok": False,
                "index": settings.es_index,
                "field": settings.es_vector_field,
                "expectedDimensions": settings.es_vector_dimensions,
                "errors": [f"mapping check failed: {type(exc).__name__}"],
            }

    async def rebuild(self) -> dict[str, Any]:
        settings = get_settings()
        if settings.app_env.strip().lower() == "production":
            raise RuntimeError(
                "production vector index rebuild must use a versioned physical "
                "index and an atomic alias switch"
            )
        base = settings.es_hosts.split(",")[0].rstrip("/")
        client = await get_client("es", timeout=20)
        exists = await client.head(f"{base}/{settings.es_index}")
        if exists.status_code == 200:
            deleted = await client.delete(f"{base}/{settings.es_index}")
            deleted.raise_for_status()
        elif exists.status_code != 404:
            exists.raise_for_status()
        created = await client.put(
            f"{base}/{settings.es_index}",
            json=index_mapping_body(settings),
        )
        created.raise_for_status()
        return await self.check()


vector_index_contract = VectorIndexContract()
