# AI Shop DB batch benchmark

- Benchmark: db-20260821-ai-quality-v4-r2
- Created: 2026-08-21T12:52:19.374Z
- Scope: real local database reads plus a rollback-only write probe
- Dedicated benchmark database: False
- Production SLO claim: none

## Measurements

### Candidate count 1
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.776ms, P95=4.663ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.744ms, P95=0.806ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.661ms, P95=0.699ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.637ms, P95=0.659ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 10
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=3.811ms, P95=28.084ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=10, connectionAcquisitions=1, P50=7.446ms, P95=7.678ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.782ms, P95=1.093ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=10, connectionAcquisitions=1, P50=6.078ms, P95=6.13ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 50
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=13.896ms, P95=28.631ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=50, connectionAcquisitions=1, P50=38.592ms, P95=40.205ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=1.669ms, P95=2.277ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=50, connectionAcquisitions=1, P50=32.488ms, P95=35.05ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 100
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=14.16ms, P95=16.694ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=100, connectionAcquisitions=1, P50=66.37ms, P95=66.466ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=2.056ms, P95=2.156ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=100, connectionAcquisitions=1, P50=57.697ms, P95=59.59ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

## Transaction rollback probe

- passed=True
- beforeCount=0
- insideTransactionCount=1
- afterRollbackCount=0
- committedWrites=0

## Interpretation boundary

These timings describe this shared local development database, pool, schema, and candidate selection only. They are not production capacity, latency SLO, or a cross-region benchmark.
