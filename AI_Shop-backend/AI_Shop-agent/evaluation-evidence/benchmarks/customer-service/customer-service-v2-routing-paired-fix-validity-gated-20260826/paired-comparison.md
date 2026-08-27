# Customer-service routing fix — paired comparison

- Status: `DEVELOPMENT_FIX_EFFECT_CONFIRMED_LABEL_GATE_BLOCKED`
- Cases: `120` paired cases
- Development fix validated: `true`
- Release/final eligible: `false / false`

## Binary paired outcomes

| Metric | Before | After | Delta | Improved / regressed | Exact McNemar p |
|---|---:|---:|---:|---:|---:|
| intentAccuracy | 0.741667 | 0.975 | 0.233333 | 28 / 0 | 1e-08 |
| highRiskRecall | 0.733333 | 1.0 | 0.266667 | 4 / 0 | 0.125 |
| handoffRecall | 0.6875 | 1.0 | 0.3125 | 10 / 0 | 0.00195312 |
| criticalHandoffSuccess | 0.727273 | 1.0 | 0.272727 | 3 / 0 | 0.25 |
| slotExactMatch | 0.457143 | 0.871429 | 0.414286 | 37 / 8 | 1.537e-05 |

## Paired aggregate bootstrap

- `intentMacroF1`: `0.71724` → `0.962857` (Δ `0.245617`, 95% CI `0.180408`–`0.334601`)
- `slotEntitySpanF1`: `0.77392` → `0.982481` (Δ `0.20856`, 95% CI `0.136297`–`0.2963`)

## Interpretation

- The routing change materially improved this exact exposed 120-case development set.
- McNemar tests reflect paired binary changes; small safety denominators may remain statistically inconclusive even with zero observed misses.
- The result cannot estimate unseen generalization because implementation was informed by these cases.
- Label consistency and reviewer-provenance gates remain open, so no release-quality claim is permitted.
