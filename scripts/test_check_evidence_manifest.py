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
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_valid_minimum_manifest(self):
        self.assertEqual(validate_manifest(_manifest(self.root), check_local_results=False), [])

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

    def test_required_evidence_ids_are_enforced(self):
        manifest = _manifest(self.root)
        manifest["requiredEvidenceIds"] = ["source", "live-task-contract"]

        errors = validate_manifest(manifest, check_local_results=False)

        self.assertTrue(any("live-task-contract" in error for error in errors))

    def test_text_contracts_require_and_forbid_tokens(self):
        manifest = _manifest(self.root)
        artifact = self.root / "artifact.py"
        artifact.write_text("EXECUTION_MODE = 'LIVE_FULL_STACK'\n", encoding="utf-8")
        relative = str(artifact.relative_to(Path(__file__).resolve().parents[1]))
        manifest["implementationArtifacts"] = [
            {
                "path": relative,
                "requiredTokens": ["LIVE_FULL_STACK"],
                "forbiddenTokens": ["SIMULATED_RESULT"],
            }
        ]

        self.assertEqual(validate_manifest(manifest, check_local_results=False), [])

        artifact.write_text("SIMULATED_RESULT = True\n", encoding="utf-8")
        errors = validate_manifest(manifest, check_local_results=False)
        self.assertTrue(any("missing required token" in error for error in errors))
        self.assertTrue(any("contains forbidden token" in error for error in errors))

    def test_ai_assisted_review_requires_complete_consistent_rubric(self):
        manifest = _manifest(self.root)
        result_dir = self.root / "local-result"
        result_dir.mkdir()
        result = result_dir / "summary.json"
        result.write_text(
            json.dumps(
                {
                    "metadata": {
                        "schemaVersion": "aishop-eval/v1",
                        "suite": "rag-generation-live-v1",
                        "runId": "run-1",
                    },
                    "summary": {
                        "caseCount": 1,
                        "executedCount": 1,
                        "criticalSafetyViolationCount": 0,
                    },
                    "cases": [{"caseId": "case-1"}],
                }
            ),
            encoding="utf-8",
        )
        review = result_dir / "ai-review.json"
        review.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "suite": "rag-generation-live-v1",
                    "runId": "run-1",
                    "reviewerType": "AI_ASSISTED_INITIAL_REVIEW",
                    "status": "COMPLETED",
                    "cases": [
                        {
                            "caseId": "case-1",
                            "grounded": True,
                            "complete": True,
                            "citationAligned": True,
                            "safe": True,
                            "verdict": "PASS",
                            "reason": "证据、答案与引用一致。",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        relative_result = str(result.relative_to(Path(__file__).resolve().parents[1]))
        relative_review = str(review.relative_to(Path(__file__).resolve().parents[1]))
        manifest["evidence"][0].update(
            {
                "kind": "evaluation",
                "suite": "rag-generation-live-v1",
                "level": "E3_CONFIGURED_LIVE",
                "state": "LOCAL_RESULT",
                "resultLocation": "local-ignored",
                "resultPath": relative_result,
                "resultSha256": hashlib.sha256(result.read_bytes()).hexdigest(),
                "caseCount": 1,
                "reviewPath": relative_review,
                "reviewSha256": hashlib.sha256(review.read_bytes()).hexdigest(),
                "reviewCaseCount": 1,
            }
        )

        self.assertEqual(validate_manifest(manifest, check_local_results=True), [])

        payload = json.loads(review.read_text(encoding="utf-8"))
        payload["cases"][0]["verdict"] = "FAIL"
        review.write_text(json.dumps(payload), encoding="utf-8")
        manifest["evidence"][0]["reviewSha256"] = hashlib.sha256(review.read_bytes()).hexdigest()
        errors = validate_manifest(manifest, check_local_results=True)
        self.assertTrue(any("verdict is inconsistent" in error for error in errors))

    def test_failed_retained_evidence_may_preserve_safety_failure(self):
        manifest = _manifest(self.root)
        result = self.root / "failed-retained.json"
        result.write_text(
            json.dumps(
                {
                    "metadata": {
                        "schemaVersion": "aishop-eval/v1",
                        "suite": "rag-generation-live-v2",
                        "runId": "run-failed",
                    },
                    "summary": {
                        "caseCount": 1,
                        "executedCount": 1,
                        "criticalSafetyViolationCount": 1,
                    },
                    "cases": [{"caseId": "unsafe-case"}],
                }
            ),
            encoding="utf-8",
        )
        relative = str(result.relative_to(Path(__file__).resolve().parents[1]))
        manifest["evidence"][0].update(
            {
                "kind": "evaluation",
                "suite": "rag-generation-live-v2",
                "level": "E3_CONFIGURED_LIVE",
                "state": "LOCAL_RESULT",
                "resultLocation": "local-ignored",
                "resultPath": relative,
                "resultSha256": hashlib.sha256(result.read_bytes()).hexdigest(),
                "caseCount": 1,
            }
        )

        errors = validate_manifest(manifest, check_local_results=True)
        self.assertTrue(any("critical safety violations" in error for error in errors))

        manifest["evidence"][0]["qualityGateState"] = "FAILED_RETAINED"
        self.assertEqual(validate_manifest(manifest, check_local_results=True), [])


if __name__ == "__main__":
    unittest.main()
