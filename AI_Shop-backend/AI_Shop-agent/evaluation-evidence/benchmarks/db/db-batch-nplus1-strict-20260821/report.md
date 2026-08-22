# AI Shop DB batch benchmark

- Benchmark: db-batch-nplus1-strict-20260821
- Created: 2026-08-21T07:34:35.699Z
- Scope: real local database reads plus a rollback-only write probe
- Dedicated benchmark database: False
- Production SLO claim: none

## Measurements

### Candidate count 1
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.764ms, P95=0.856ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.607ms, P95=0.708ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.661ms, P95=0.795ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.603ms, P95=0.622ms, errorRate=0.0

### Candidate count 10
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.755ms, P95=1.282ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=10, connectionAcquisitions=1, P50=5.849ms, P95=5.912ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.736ms, P95=0.792ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=10, connectionAcquisitions=1, P50=5.449ms, P95=5.574ms, errorRate=0.0

### Candidate count 50
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=1.165ms, P95=1.965ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=50, connectionAcquisitions=1, P50=28.334ms, P95=29.957ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=1.132ms, P95=1.315ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=50, connectionAcquisitions=1, P50=28.946ms, P95=30.618ms, errorRate=0.0

### Candidate count 100
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=1.937ms, P95=2.146ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=100, connectionAcquisitions=1, P50=58.198ms, P95=59.822ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=2.096ms, P95=2.849ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=100, connectionAcquisitions=1, P50=52.973ms, P95=57.316ms, errorRate=0.0

## Transaction rollback probe

- passed=True
- beforeCount=0
- insideTransactionCount=1
- afterRollbackCount=0
- committedWrites=0

## Interpretation boundary

These timings describe this shared local development database, pool, schema, and candidate selection only. They are not production capacity, latency SLO, or a cross-region benchmark.
