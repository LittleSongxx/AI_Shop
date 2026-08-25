# Customer-Service HTTP v14 Closure

- Run：`customer-service-http-v14-20260825`
- Observation：60/60 HTTP executions completed; the immutable package is [customer-service-http-v14-20260825](../../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v14-20260825/).
- Dataset SHA-256：`112dfd6ba7546b7cbad317597d944e3ab4dc02627d4ca6018733031d8eddc527`
- Observation report SHA-256：`53989861835a498018b8ca16c2719446e053638591162ed53abafbcb8d45d349`
- Package `SHA256SUMS` SHA-256：`d366f934097f3c825ad5d2db7cd5d97741ba6121ea399f36301a02347a0240ae`

## Preflight

The complete regression preflight passed. It includes Gateway, Agent API/Worker/MCP readiness with one source fingerprint, Elasticsearch, Redis, MySQL, product catalog, embedding, rerank, LLM, and the explicit `LOCAL_EVALUATION_ONLY` fixture boundary. The saved JSON is [preflight-regression-20260825-r2.json](preflight-regression-20260825-r2.json), SHA-256 `7e99bb01b7d3ccaeeecb5402537fa86564e3734a2fa39aa4cfb758fe4ec297bc`.

Source fingerprint: `89b00a9384938c12d53d7eedc3705debe51c23cf43a4bec7c744555e2f88dc91` (183 files). Nineteen order snapshots were provisioned per case and cleaned; the post-run order, stock, and Agent mutable-row checks were all zero.

## Observation

The live run used the real HTTP Agent path and re-executed Provider calls. It records:

- HTTP execution `60/60`; intent Macro-F1 `1.000000`; high-risk recall `1.000000`; handoff recall `1.000000`; critical handoff miss rate `0`.
- Local full-stack latency P50/P95/P99 `1021.515/12175.641/15439.877 ms`; these are not production SLOs.
- Runtime diagnostic only: verifier observed/pass `45/39`, safe degradation `6`, clarification `5`, fixture cleanup failures `0`.
- Usage `87243/4739` input/output tokens, 21 Provider calls, one missing usage record; cost remains `null` with status `MISSING_USAGE`.
- Two safety behavior-contract violations remain: `cs-gold-v1-019` and `cs-gold-v1-055`. Both are preserved in `badcases.jsonl`; they are not silently counted as answer correctness.
- HTTP slot F1/EM is unavailable across the redaction boundary. The rule pre-router values remain diagnostic and are not HTTP answer scores.

These figures do not establish answer correctness, citation grounding, CSAT/FCR, online success, or production capacity.

## Review Gate

Fresh randomized reviewer-A and reviewer-B sheets were exported and independently hash-bound to the observation (`db08fed5...e9f74` and `51d31ffd...c985f`). Both are intentionally blank and contain no expected labels or self-judgment. The standard `review-package` command rejected them with `labels are incomplete`, so no fake sealed review, agreement rate, or third-person adjudication was produced.

The current state is therefore `EXECUTED_PENDING_INDEPENDENT_HUMAN_REVIEW`, with review coverage `0/60`, `answerCorrectness=null`, `citationGroundingSupport=null`, and `releaseGateEligible=false`. A real reviewer must complete each sheet before the CLI can seal, compare, export disagreement-only adjudication, and merge a final report.

For tooling diagnostics only, two independent model-assisted reviewers and a separate model-assisted adjudicator were run against the same redacted observation. They reached `54/60` case-level agreement and produced diagnostic values of answer `56/60`, citation support `28/33`, handoff `57/60`, unsafe `0/60`, and joint quality `51/60`. This is recorded separately in [customer-service-http-v14-model-assisted-review-20260825.md](customer-service-http-v14-model-assisted-review-20260825.md); it does not change this closure's pending-human-review status or release-gate eligibility.
