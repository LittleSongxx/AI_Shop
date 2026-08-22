# AI Shop DB batch benchmark

- Benchmark: db-benchmark-v6-isolated-20260822
- Created: 2026-08-21T19:05:34.263Z
- Scope: real local database reads plus a rollback-only write probe
- Dedicated benchmark database: True
- Production SLO claim: none

## Measurements

### Candidate count 1
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.907ms, P95=1.854ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=0.72ms, P95=0.873ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.683ms, P95=0.707ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.658ms, P95=0.662ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 10
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=5.859ms, P95=6.906ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=10, connectionAcquisitions=1, P50=8.282ms, P95=8.338ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=0.749ms, P95=0.872ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=10, connectionAcquisitions=1, P50=5.69ms, P95=5.71ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 50
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=15.959ms, P95=17.451ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=50, connectionAcquisitions=1, P50=38.319ms, P95=38.759ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=1.516ms, P95=1.641ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=50, connectionAcquisitions=1, P50=29.385ms, P95=29.624ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

### Candidate count 100
- batchOfferSnapshot: roundTrips=1, connectionAcquisitions=1, P50=18.272ms, P95=20.552ms, errorRate=0.0
- nPlusOneOfferSnapshot: roundTrips=100, connectionAcquisitions=1, P50=71.724ms, P95=74.13ms, errorRate=0.0
- batchDecisionFeature: roundTrips=1, connectionAcquisitions=1, P50=2.086ms, P95=2.533ms, errorRate=0.0
- nPlusOneDecisionFeature: roundTrips=100, connectionAcquisitions=1, P50=62.665ms, P95=63.831ms, errorRate=0.0
- resultEquivalence: offerSnapshot=True, decisionFeature=True, method=ORDER_INDEPENDENT_CANONICAL_ROW_SHA256

## Transaction rollback probe

- passed=True
- beforeCount=0
- insideTransactionCount=1
- afterRollbackCount=0
- committedWrites=0

## Interpretation boundary

These timings describe this shared local development database, pool, schema, and candidate selection only. They are not production capacity, latency SLO, or a cross-region benchmark.
