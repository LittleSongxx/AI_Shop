# AI Shop DB batch benchmark

- Benchmark: db-benchmark-v9-20260822
- Created: 2026-08-22T04:37:11.560Z
- Scope: real local database reads plus a rollback-only write probe
- Dedicated benchmark database: True
- Production SLO claim: none

## Measurements

### Candidate count 1
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.887ms, P95=4.217ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.84ms, P95=1.102ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.728ms, P95=0.83ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.68ms, P95=0.758ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 10
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=11.668ms, P95=15.037ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=10, connectionAcquisitions=1, P50=13.297ms, P95=14.206ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=1.084ms, P95=1.157ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=10, connectionAcquisitions=1, P50=7.042ms, P95=8.289ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 50
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=34.944ms, P95=39.04ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=50, connectionAcquisitions=1, P50=58.513ms, P95=73.67ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=1.988ms, P95=2.346ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=50, connectionAcquisitions=1, P50=35.086ms, P95=40.482ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 100
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=23.864ms, P95=30.747ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=100, connectionAcquisitions=1, P50=89.805ms, P95=90.782ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=2.405ms, P95=2.429ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=100, connectionAcquisitions=1, P50=70.501ms, P95=71.49ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

## Transaction rollback probe

- passed=True
- beforeCount=0
- insideTransactionCount=1
- afterRollbackCount=0
- committedWrites=0

## Interpretation boundary

These timings describe this shared local development database, pool, schema, and candidate selection only. They are not production capacity, latency SLO, or a cross-region benchmark.
