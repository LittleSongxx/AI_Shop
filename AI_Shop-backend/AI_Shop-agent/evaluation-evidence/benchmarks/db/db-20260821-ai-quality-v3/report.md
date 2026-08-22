# AI Shop DB batch benchmark

- Benchmark: db-20260821-ai-quality-v3
- Created: 2026-08-20T17:27:09.546Z
- Scope: isolated real database, read-only query benchmark
- Production SLO claim: none

## Measurements

### Candidate count 1
- batchOfferSnapshot: roundTrips=1, connectionUsage=1, P50=0.739ms, P95=0.928ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=1, connectionUsage=1, P50=0.726ms, P95=0.787ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionUsage=1, P50=0.751ms, P95=0.782ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=1, connectionUsage=1, P50=0.754ms, P95=0.923ms, errorRate=0.0
- rollbackProbePassed=True

### Candidate count 10
- batchOfferSnapshot: roundTrips=1, connectionUsage=1, P50=0.91ms, P95=0.931ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=10, connectionUsage=1, P50=7.028ms, P95=7.541ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionUsage=1, P50=0.861ms, P95=0.901ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=10, connectionUsage=1, P50=6.883ms, P95=6.985ms, errorRate=0.0
- rollbackProbePassed=True

### Candidate count 50
- batchOfferSnapshot: roundTrips=1, connectionUsage=1, P50=1.621ms, P95=1.872ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=50, connectionUsage=1, P50=32.916ms, P95=33.187ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionUsage=1, P50=1.314ms, P95=2.243ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=50, connectionUsage=1, P50=42.885ms, P95=45.722ms, errorRate=0.0
- rollbackProbePassed=True

### Candidate count 100
- batchOfferSnapshot: roundTrips=1, connectionUsage=1, P50=2.459ms, P95=2.909ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=100, connectionUsage=1, P50=76.873ms, P95=81.038ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionUsage=1, P50=2.346ms, P95=2.55ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=100, connectionUsage=1, P50=93.235ms, P95=96.092ms, errorRate=0.0
- rollbackProbePassed=True

## Interpretation boundary

These timings describe this local database, pool, schema, and fixture only. They are not production capacity, latency SLO, or a cross-region benchmark.
