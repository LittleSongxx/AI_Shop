# AI_Shop controlled after-sales Agent traces

- Source: persisted live Episodes and MySQL pending-action rows
- Simulation: no
- PII, credentials, action tokens, and business identifiers: redacted

## confirmed_cancel

- Run status: `SUCCEEDED`
- Intent: `CANCEL_ORDER`
- Episode verdict: `CANCEL_CONFIRMED`
- Pending states: `['CONFIRMED']`
- Token usage: `0 + 0`
- Latency / TTFT: `1067 ms / None ms`
- Selected event chain: `INTENT_DECISION -> INTENT_DECISION -> RAG_RETRIEVAL -> ORCHESTRATION_DECISION -> ACTION_PROPOSED -> TOOL_CALL -> GRAPH_END -> ACTION_CONFIRMED_BY_USER -> ACTION_TERMINAL`
