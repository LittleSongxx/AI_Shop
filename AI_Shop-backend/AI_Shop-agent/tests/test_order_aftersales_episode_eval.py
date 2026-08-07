from benchmarks.run_episode_eval import (
    DEFAULT_DATASET,
    DEFAULT_LOCK,
    evaluate_cases,
    load_cases,
    run,
    validate_lock,
)


def test_frozen_order_aftersales_episode_dataset_is_locked_and_green():
    cases = load_cases(DEFAULT_DATASET)
    contract = validate_lock(cases, DEFAULT_DATASET, DEFAULT_LOCK)
    summary = evaluate_cases(cases)

    assert contract["caseCount"] == 7
    assert summary["failures"] == []
    assert summary["passRate"] == 1.0
    assert summary["reviewEligibleAccuracy"] == 1.0
    assert summary["trainingEligibilityAccuracy"] == 1.0
    assert run()["summary"]["passed"] == 7
