from __future__ import annotations

from typing import Any

from app.config.settings import Settings, get_settings
from app.infra.http_client import get_client


def validate_mapping(
    mapping: dict[str, Any],
    *,
    index: str,
    field: str,
    dimensions: int,
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
    return {
        "ok": not errors,
        "index": index,
        "field": field,
        "expectedDimensions": dimensions,
        "actualType": actual_type,
        "actualDimensions": actual_dimensions,
        "errors": errors,
    }


def index_mapping_body(settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    return {
        "mappings": {
            "properties": {
                "content": {"type": "text"},
                "metadata": {"type": "object", "dynamic": True},
                current.es_vector_field: {
                    "type": "dense_vector",
                    "dims": current.es_vector_dimensions,
                    "index": True,
                    "similarity": "cosine",
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
