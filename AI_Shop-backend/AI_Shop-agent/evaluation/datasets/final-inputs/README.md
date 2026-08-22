# Final inputs

This directory intentionally contains no tracked final cases.

A final dataset is supplied only after freeze-final has recorded the exact source,
configuration, provider, knowledge, and catalog fingerprints. claim-final validates
the unseen JSONL, rejects overlap with development and regression inputs, records its
hash in datasets/locks/consumed-final.json, and consumes that hash even when the
single execution fails.
