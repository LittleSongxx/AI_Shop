# AI Shop DB batch benchmark

- Benchmark: db-batch-nplus1-equivalent-20260821
- Created: 2026-08-21T07:36:12.346Z
- Scope: real local database reads plus a rollback-only write probe
- Dedicated benchmark database: False
- Production SLO claim: none

## Measurements

### Candidate count 1
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.835ms, P95=3.371ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.648ms, P95=0.707ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.609ms, P95=0.622ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.62ms, P95=0.669ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 10
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=3.535ms, P95=18.316ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=10, connectionAcquisitions=1, P50=6.536ms, P95=6.548ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.707ms, P95=0.743ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=10, connectionAcquisitions=1, P50=5.63ms, P95=5.741ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 50
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=9.023ms, P95=14.192ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=50, connectionAcquisitions=1, P50=31.712ms, P95=32.2ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=1.295ms, P95=1.354ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=50, connectionAcquisitions=1, P50=27.11ms, P95=27.364ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 100
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=8.622ms, P95=8.783ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=100, connectionAcquisitions=1, P50=59.278ms, P95=59.765ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=1.757ms, P95=1.882ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=100, connectionAcquisitions=1, P50=57.008ms, P95=57.779ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

## Transaction rollback probe

- passed=True
- beforeCount=0
- insideTransactionCount=1
- afterRollbackCount=0
- committedWrites=0

## Interpretation boundary

These timings describe this shared local development database, pool, schema, and candidate selection only. They are not production capacity, latency SLO, or a cross-region benchmark.
