from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "online-ai-eval.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_online_workflow_runs_one_complete_visible_split() -> None:
    text = _workflow()

    assert "python -m evaluation.cli validate" in text
    assert 'python -m evaluation.cli preflight --split "${EVAL_SPLIT}"' in text
    assert "python -m evaluation.cli run" in text
    assert '--split "${EVAL_SPLIT}"' in text
    assert '--run-id "${RUN_ID}"' in text
    assert "AI_Shop-backend/AI_Shop-agent/benchmarks/" not in text


def test_online_workflow_starts_real_agent_and_preserves_failed_evidence() -> None:
    text = _workflow()

    assert "python -m uvicorn app.main:app" in text
    assert "python -m app.worker" in text
    assert "alembic upgrade head" in text
    assert "if: always()" in text
    assert "evaluation/.runs/${{ steps.eval-ids.outputs.run_id }}" in text
    assert "evaluation/.runs/${{ steps.eval-ids.outputs.repeat_run_id }}" in text
    assert "evaluation/.runs/${{ steps.eval-ids.outputs.fault_run_id }}" in text
    assert (
        "evaluation-evidence/benchmarks/db/"
        "${{ steps.eval-ids.outputs.benchmark_run_id }}"
    ) in text
    assert "evaluation/.runs/online-*" not in text
    assert "if-no-files-found: warn" in text


def test_online_workflow_emits_v3_slice_repeat_and_fault_evidence() -> None:
    text = _workflow()

    assert "python -m evaluation.cli slices" in text
    assert "python -m evaluation.cli repeat" in text
    assert "--k 5" in text
    assert "python -m evaluation.cli fault-test" in text
    assert "evaluation/fault_scenarios.json" in text
    assert "python -m evaluation.cli benchmark-db" in text
    assert "--sizes 1,10,50,100" in text


def test_online_workflow_is_fail_closed_and_excludes_final() -> None:
    text = _workflow()

    assert "Missing required live evaluation configuration" in text
    assert "exit 1" in text
    assert "cancel-in-progress: false" in text
    assert "--confirm-final" not in text
    assert "freeze-final" not in text
    assert "claim-final" not in text
    for required_configuration in (
        "AI_EVAL_INTERNAL_TOKEN",
        "AI_EVAL_MYSQL_PASSWORD",
        "AI_EVAL_REDIS_PASSWORD",
        "AI_EVAL_RABBITMQ_URL",
        "AI_EVAL_LLM_API_KEY",
        "AI_EVAL_EMBEDDING_API_KEY",
        "AI_EVAL_RERANK_API_KEY",
    ):
        assert required_configuration in text
