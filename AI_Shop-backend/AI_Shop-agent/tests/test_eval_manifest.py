from __future__ import annotations

import json
from pathlib import Path

import httpx

from benchmarks.eval_runtime.adapters import _blocked_provider_result
from benchmarks.eval_runtime.contracts import (
    EvidenceLevel,
    FailureClass,
    classify_exception,
)
from benchmarks.eval_runtime.evidence import EvidenceStore
from benchmarks.eval_runtime.manifest import ensure_run_manifest, write_final_manifest
from benchmarks.eval_runtime.registry import SuiteDefinition


def _suite(tmp_path: Path) -> SuiteDefinition:
    lock = tmp_path / "suite.lock.json"
    lock.write_text('{"dataset":"locked"}\n', encoding="utf-8")
    return SuiteDefinition(
        "search-v3",
        {
            "profile": "local-live",
            "providerPolicy": "FAIL_CLOSED_NO_FALLBACK",
            "suiteLock": str(lock),
        },
        tmp_path / "search-v3.json",
    )


def test_manifest_keeps_identity_and_terminal_lifecycle(tmp_path, monkeypatch):
    from benchmarks.eval_runtime import manifest as manifest_module

    suite = _suite(tmp_path)
    store = EvidenceStore(tmp_path, suite="search-v3", run_id="run-1")
    monkeypatch.setattr(manifest_module, "_git_sha", lambda: "abcdef0")
    monkeypatch.setattr(
        manifest_module,
        "_provider_bundle",
        lambda _suite: {"llm": {"model": "provider-model"}, "fallbackPolicy": "FORBIDDEN_FOR_FORMAL_LIVE"},
    )

    created = ensure_run_manifest(
        store,
        suite,
        "run-1",
        {"phase": "VALIDATED", "state": "IN_PROGRESS"},
        fixture_snapshot_id="fixture-1",
        knowledge_release=7,
        execution_mode="local-live",
    )
    terminal = write_final_manifest(
        store,
        suite,
        "run-1",
        {"phase": "PACKAGED", "state": "COMPLETE"},
        stage_status="COMPLETE",
        fixture_snapshot_id="fixture-1",
        knowledge_release=7,
        execution_mode="local-live",
    )

    assert created["evidenceLevel"] == EvidenceLevel.E3
    assert terminal["lifecycle"] == {"phase": "PACKAGED", "state": "COMPLETE"}
    assert terminal["terminal"] is True
    persisted = json.loads(store.path("run-manifest.json").read_text(encoding="utf-8"))
    assert persisted["gitSha"] == "abcdef0"
    assert "apiKey" not in json.dumps(persisted)


def test_provider_http_auth_quota_and_timeout_are_blocking_classes():
    auth_error = httpx.HTTPStatusError(
        "invalid_api_key",
        request=httpx.Request("POST", "https://provider.example/v1"),
        response=httpx.Response(401),
    )
    quota_error = RuntimeError("insufficient_quota")

    assert classify_exception(auth_error) == FailureClass.PROVIDER_ERROR
    assert classify_exception(quota_error) == FailureClass.RATE_LIMITED
    assert classify_exception(TimeoutError("provider timeout")) == FailureClass.TIMEOUT


def test_formal_live_fallback_is_blocked(tmp_path):
    suite = _suite(tmp_path)
    result = _blocked_provider_result(
        suite,
        stage="final",
        payload={"runtimeTrace": {"fallback": True}},
    )

    assert result is not None
    assert result.status == "BLOCKED"
    assert result.failure_class == FailureClass.PROVIDER_ERROR
