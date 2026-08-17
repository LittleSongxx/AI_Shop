import hashlib

import pytest

from scripts import export_interview_traces as exporter


def _episode(*, known: bool) -> dict:
    facts = {
        "actionType": "REFUND",
        "actionProposed": True,
        "userConfirmed": True,
        "remoteOutcomeKnown": known,
        "actionOutcome": "CONFIRMED" if known else None,
    }
    return {
        "runId": "run-1" if known else "run-2",
        "traceId": "trace-1",
        "status": "SUCCEEDED",
        "outcome": "ok",
        "scenario": "ORDER_AFTERSALES",
        "intent": "REFUND",
        "modelName": "provider-model",
        "inputTokens": 100,
        "outputTokens": 50,
        "costCny": 0.02,
        "latencyMs": 1200,
        "ttftMs": 250,
        "experiment": {"orchestration": {"mode": "workflow"}},
        "conversation": {
            "userMessage": "订单 SM202608170001 要退款，手机号 13800138000",
            "assistantMessage": ("请确认【act_1234567890abcdef1234567890abcdef】"),
            "bizType": "action_confirm",
            "sourceRefs": [],
        },
        "episodeEvaluation": {
            "verdict": "CANCEL_CONFIRMED" if known else "OUTCOME_UNKNOWN",
            "facts": facts,
        },
        "steps": [
            {
                "stepId": 1,
                "eventType": "TOOL_CALL",
                "nodeName": "tools",
                "status": "OK",
                "toolName": "PROPOSE_REFUND",
                "input": {
                    "args": {
                        "userId": "user-1",
                        "orderId": "SM202608170001",
                        "actionToken": "act_1234567890abcdef1234567890abcdef",
                    }
                },
                "output": {"status": "ok"},
            },
            {
                "stepId": 2,
                "eventType": "ACTION_CONFIRMED_BY_USER",
                "nodeName": "pending_action",
                "status": "OK",
                "output": facts,
            },
        ],
        "handoffs": [],
        "children": [],
    }


def test_trace_pair_requires_confirmed_refund_and_unknown_mysql_state():
    success = _episode(known=True)
    unknown = _episode(known=False)

    exporter.validate_trace_pair(
        success,
        [{"actionType": "REFUND", "status": "CONFIRMED"}],
        unknown,
        [{"actionType": "REFUND", "status": "MANUAL_REVIEW"}],
    )


def test_trace_pair_rejects_a_fake_known_unknown_outcome():
    unknown = _episode(known=True)

    with pytest.raises(exporter.TraceExportError, match="known remote outcome"):
        exporter.validate_trace_pair(
            _episode(known=True),
            [{"status": "CONFIRMED"}],
            unknown,
            [{"status": "INCONCLUSIVE"}],
        )


def test_public_trace_redacts_tokens_pii_and_business_ids():
    public = exporter.public_trace(
        "confirmed_refund",
        _episode(known=True),
        [{"actionType": "REFUND", "status": "CONFIRMED"}],
    )
    serialized = str(public)

    assert "13800138000" not in serialized
    assert "SM202608170001" not in serialized
    assert "act_1234567890abcdef1234567890abcdef" not in serialized
    assert "[ACTION_TOKEN]" in serialized
    assert "sha256:" in serialized


def test_bundle_writer_emits_verifiable_sha256_files(tmp_path):
    bundle = exporter.build_bundle(
        _episode(known=True),
        [{"actionType": "REFUND", "status": "CONFIRMED"}],
        _episode(known=False),
        [{"actionType": "REFUND", "status": "INCONCLUSIVE"}],
    )
    output = tmp_path / "bundle"

    hashes = exporter.write_bundle(bundle, output)

    assert (output / "traces.json").is_file()
    assert (output / "report.md").is_file()
    assert (output / "manifest.json").is_file()
    assert (output / "SHA256SUMS").is_file()
    assert (
        hashes["traces.json"] == hashlib.sha256((output / "traces.json").read_bytes()).hexdigest()
    )
    assert "LIVE_FULL_STACK" in (output / "traces.json").read_text(encoding="utf-8")
