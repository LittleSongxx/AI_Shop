# AI Shop DB batch benchmark

- Benchmark: db-batch-nplus1-equivalent-v4-20260821
- Created: 2026-08-21T09:51:58.703Z
- Scope: real local database reads plus a rollback-only write probe
- Dedicated benchmark database: False
- Production SLO claim: none

## Measurements

### Candidate count 1
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.796ms, P95=4.334ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.736ms, P95=0.798ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.668ms, P95=0.694ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.62ms, P95=0.68ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 10
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=3.219ms, P95=21.418ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=10, connectionAcquisitions=1, P50=7.26ms, P95=7.606ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.766ms, P95=0.801ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=10, connectionAcquisitions=1, P50=5.851ms, P95=5.888ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 50
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=9.626ms, P95=20.098ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=50, connectionAcquisitions=1, P50=35.151ms, P95=35.761ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=1.454ms, P95=1.626ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=50, connectionAcquisitions=1, P50=28.746ms, P95=29.014ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 100
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=11.267ms, P95=12.045ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=100, connectionAcquisitions=1, P50=62.953ms, P95=69.308ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=1.822ms, P95=1.984ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=100, connectionAcquisitions=1, P50=56.511ms, P95=57.292ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

## Transaction rollback probe

- passed=True
- beforeCount=0
- insideTransactionCount=1
- afterRollbackCount=0
- committedWrites=0

## Interpretation boundary

These timings describe this shared local development database, pool, schema, and candidate selection only. They are not production capacity, latency SLO, or a cross-region benchmark.
