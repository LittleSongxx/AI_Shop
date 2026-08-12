import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from check_evidence_manifest import validate_manifest


def _manifest(tmp_path: Path) -> dict:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text('{"id":"case-1"}\n', encoding="utf-8")
    lock = tmp_path / "dataset.lock.json"
    lock.write_text(
        json.dumps(
            {
                "dataset": "dataset.jsonl",
                "datasetSha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return {
        "schemaVersion": 1,
        "implementationBaseline": {
            "gitHead": "a" * 40,
            "workspaceDiffSha256": "b" * 64,
        },
        "evidence": [
            {
                "id": "source",
                "kind": "source-check",
                "level": "E0_SOURCE",
                "state": "VERIFIED",
                "command": "python check.py",
                "claim": "source exists",
                "boundary": "not a runtime result",
                "resultLocation": "tracked",
                "resultPath": "README.md",
                "datasetLocks": [],
            }
        ],
        "honestBoundaries": ["no production claim"],
        "claimDocuments": ["README.md"],
        "forbiddenCurrentClaims": [],
    }


class EvidenceManifestTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_valid_minimum_manifest(self):
        self.assertEqual(
            validate_manifest(_manifest(self.root), check_local_results=False), []
        )

    def test_not_collected_cannot_have_result_path(self):
        manifest = _manifest(self.root)
        manifest["evidence"][0].update(
            {
                "level": "E4_REAL_USER",
                "state": "NOT_COLLECTED",
                "resultLocation": "not-collected",
                "resultPath": "fake.json",
            }
        )
        errors = validate_manifest(manifest, check_local_results=False)
        self.assertTrue(any("must not have a result path" in error for error in errors))

    def test_duplicate_evidence_id_fails(self):
        manifest = _manifest(self.root)
        manifest["evidence"].append(dict(manifest["evidence"][0]))
        errors = validate_manifest(manifest, check_local_results=False)
        self.assertTrue(any("duplicate evidence id" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
