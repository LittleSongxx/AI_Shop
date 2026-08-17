"""Runtime resilience primitives used by the Agent serving path.

The circuit breaker is the shared provider-failure boundary. MCP performs one
session rebuild only for a lost transport session, while durable worker retries
are owned by the task lease/backoff state machine. There is intentionally no
generic tool retry decorator: replaying an unknown write could duplicate a
refund, review, or support action.
"""
