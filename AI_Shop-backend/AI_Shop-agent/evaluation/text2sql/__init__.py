"""Independent Text2SQL V0 evaluation package.

This package deliberately does not extend ``evaluation.core.Domain``.  The
existing Search/RAG/Agent release locks must remain byte-for-byte compatible
while Text2SQL is still a provisional development diagnostic.
"""

CASE_SCHEMA_VERSION = "aishop-text2sql-case/v0"
CATALOG_SCHEMA_VERSION = "aishop-analytics-catalog/v0"
DATASET_SCHEMA_VERSION = "aishop-text2sql-dataset/v0"
EVIDENCE_SCHEMA_VERSION = "aishop-text2sql-evidence/v0"
REVIEW_SCHEMA_VERSION = "aishop-text2sql-gold-review/v0"
