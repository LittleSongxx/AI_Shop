# AI Shop AI evaluation

- Run: final-20260820-ai-quality-v2
- Split: final
- Dataset SHA-256: a6aa8a436c1457f78d76fb44e19d1227c6972fae2068e291318f3f8147145d75
- Execution mode: LOCAL_FULL_STACK
- Overall gate: PASS

## Domain gates

- search: PASS
- rag: PASS
- agent: PASS

## Metrics

### search

- executionRate: 1.0 (n=21, 95% CI [0.845361, 1.0] (wilson))
- recallAt3: 1.0 (n=20, 95% CI [1.0, 1.0] (percentile-bootstrap))
- recallAt5: 1.0 (n=20, 95% CI [1.0, 1.0] (percentile-bootstrap))
- recallAt10: 1.0 (n=20, 95% CI [1.0, 1.0] (percentile-bootstrap))
- mrrAt10: 0.966667 (n=20, 95% CI [0.9, 1.0] (percentile-bootstrap))
- ndcgAt5: 0.972792 (n=20, 95% CI [0.918375, 1.0] (percentile-bootstrap))
- ndcgAt10: 0.972792 (n=20, 95% CI [0.920583, 1.0] (percentile-bootstrap))
- noResultAccuracy: 1.0 (n=21, 95% CI [0.845361, 1.0] (wilson))
- constraintViolationCount: 0.0 (n=21)
- providerCompleteness: 1.0 (n=21, 95% CI [0.845361, 1.0] (wilson))
- runtimeErrorCount: 0.0 (n=21)
- latencyMsP50: 381.220749 (n=21, 95% CI [288.886943, 470.048456] (percentile-bootstrap))
- latencyMsP95: 591.056916 (n=21, 95% CI [487.473782, 617.628437] (percentile-bootstrap))
- latencyMsP99: 612.314133 (n=21, 95% CI [515.359389, 617.628437] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### rag

- executionRate: 1.0 (n=21, 95% CI [0.845361, 1.0] (wilson))
- retrievalRecallAt3: 1.0 (n=19, 95% CI [1.0, 1.0] (percentile-bootstrap))
- retrievalRecallAt5: 1.0 (n=19, 95% CI [1.0, 1.0] (percentile-bootstrap))
- retrievalMrrAt10: 0.973684 (n=19, 95% CI [0.921053, 1.0] (percentile-bootstrap))
- retrievalNdcgAt5: 0.980575 (n=19, 95% CI [0.941726, 1.0] (percentile-bootstrap))
- sourcePrecision: 0.973684 (n=19, 95% CI [0.921053, 1.0] (percentile-bootstrap))
- sourceCoverage: 1.0 (n=19, 95% CI [1.0, 1.0] (percentile-bootstrap))
- generationCorrectness: 0.904762 (n=21, 95% CI [0.710859, 0.973481] (wilson))
- requiredClaimCompleteness: 0.928571 (n=21, 95% CI [0.809524, 1.0] (percentile-bootstrap))
- citationSupport: 0.952381 (n=21, 95% CI [0.857143, 1.0] (percentile-bootstrap))
- groundedFaithfulness: 1.0 (n=21, 95% CI [1.0, 1.0] (percentile-bootstrap))
- noAnswerAccuracy: 1.0 (n=21, 95% CI [0.845361, 1.0] (wilson))
- injectionResistance: 1.0 (n=2, 95% CI [0.34238, 1.0] (wilson))
- invalidCitationCount: 0.0 (n=21)
- severeSafetyViolationCount: 0.0 (n=21)
- providerCompleteness: 1.0 (n=21, 95% CI [0.845361, 1.0] (wilson))
- runtimeErrorCount: 0.0 (n=21)
- latencyMsP50: 2515.201171 (n=21, 95% CI [1480.361352, 2956.46427] (percentile-bootstrap))
- latencyMsP95: 4419.628742 (n=21, 95% CI [3518.858388, 5540.426454] (percentile-bootstrap))
- latencyMsP99: 5316.266912 (n=21, 95% CI [3651.939522, 5540.426454] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

### agent

- executionRate: 1.0 (n=8, 95% CI [0.675592, 1.0] (wilson))
- taskSuccess: 1.0 (n=8, 95% CI [0.675592, 1.0] (wilson))
- executionCompleteness: 1.0 (n=8, 95% CI [0.675592, 1.0] (wilson))
- toolSelectionAccuracy: 1.0 (n=8, 95% CI [0.675592, 1.0] (wilson))
- toolArgumentAccuracy: 1.0 (n=8, 95% CI [0.675592, 1.0] (wilson))
- terminalStateCorrectness: 1.0 (n=8, 95% CI [0.675592, 1.0] (wilson))
- retryIdempotency: 1.0 (n=6, 95% CI [0.609666, 1.0] (wilson))
- providerCompleteness: 1.0 (n=8, 95% CI [0.675592, 1.0] (wilson))
- severeSafetyViolationCount: 0.0 (n=8)
- runtimeErrorCount: 0.0 (n=8)
- latencyMsP50: 3278.184799 (n=8, 95% CI [224.713579, 11769.561359] (percentile-bootstrap))
- latencyMsP95: 13084.3909 (n=8, 95% CI [4446.864951, 13792.376038] (percentile-bootstrap))
- latencyMsP99: 13650.77901 (n=8, 95% CI [5123.703975, 13792.376038] (percentile-bootstrap), notes=DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100)

## Interpretation boundary

- All quality gates are domain hard gates; no weighted aggregate can hide a failure.
- Provider or dependency absence is an execution failure, never a skip or pass.
- Latency is LOCAL_FULL_STACK evidence and is not a production SLO.
- P99 is descriptive when its eligible sample count is below 100.
