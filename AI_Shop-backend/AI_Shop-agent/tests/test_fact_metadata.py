import hashlib
from pathlib import Path

from app.rag.canonical_facts import DEFAULT_CATALOG_PATH, get_canonical_fact_catalog
from app.rag.fact_metadata import FactMetadataCatalog


def test_fact_metadata_covers_catalog_and_is_bound_to_its_sha():
    metadata = FactMetadataCatalog.load()
    catalog = get_canonical_fact_catalog()

    assert metadata.catalog_sha256 == hashlib.sha256(
        Path(DEFAULT_CATALOG_PATH).read_bytes()
    ).hexdigest()
    assert len(metadata.facts) == 73
    assert set(metadata.facts) == set(catalog.fact_to_refs)
    assert all(row.domain for row in metadata.facts.values())
    assert all(row.fact_type for row in metadata.facts.values())
    assert all(row.aliases and row.atomic_claims for row in metadata.facts.values())
