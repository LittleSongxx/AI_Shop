import threading
import time
from enum import Enum

import structlog

from app.harness.metrics.runtime_sensors import CIRCUIT_STATE

logger = structlog.get_logger()

DEFAULT_PROBE_TIMEOUT = 30.0


class CircuitState(str, Enum):

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe circuit breaker with single-probe half-open recovery."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.probe_timeout = probe_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._probe_inflight = False
        self._probe_started_at: float = 0
        self._lock = threading.Lock()

    def _resolve_state(self) -> CircuitState:
        """Advance OPEN to HALF_OPEN after the recovery window.

        The caller must hold ``self._lock``.
        """
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._probe_inflight = False
                self._probe_started_at = 0
                logger.info("circuit_half_open", breaker=self.name)
        return self._state

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._resolve_state()

    def allow_request(self) -> bool:
        """Return whether a request may proceed.

        A half-open breaker admits one probe. A caller that never records an
        outcome cannot wedge the breaker because stale probes are reclaimed.
        """
        with self._lock:
            state = self._resolve_state()
            self._set_metric(state)

            if state == CircuitState.OPEN:
                return False

            if state == CircuitState.HALF_OPEN:
                now = time.time()
                probe_stale = (
                    self._probe_inflight
                    and now - self._probe_started_at >= self.probe_timeout
                )
                if probe_stale:
                    logger.warning("circuit_probe_reclaimed", breaker=self.name)
                elif self._probe_inflight:
                    return False
                self._probe_inflight = True
                self._probe_started_at = now
                return True

            return True

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            if self._state != CircuitState.CLOSED:
                logger.info("circuit_closed", breaker=self.name)
            self._state = CircuitState.CLOSED
            self._probe_inflight = False
            self._probe_started_at = 0
            self._set_metric(self._state)

    def release_probe(self) -> None:
        """Release an admitted probe that did not reach the dependency."""
        with self._lock:
            self._probe_inflight = False
            self._probe_started_at = 0

    def record_failure(self) -> None:
        with self._lock:
            self._last_failure_time = time.time()
            self._probe_inflight = False
            self._probe_started_at = 0

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("circuit_reopened", breaker=self.name)
                self._set_metric(self._state)
                return

            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "circuit_open",
                    breaker=self.name,
                    failures=self._failure_count,
                )
            self._set_metric(self._state)

    def _set_metric(self, state: CircuitState) -> None:
        value = {
            CircuitState.CLOSED: 0,
            CircuitState.OPEN: 1,
            CircuitState.HALF_OPEN: 2,
        }[state]
        CIRCUIT_STATE.labels(breaker=self.name).set(value)

    async def sync_to_redis(self, redis_client, key: str) -> None:
        with self._lock:
            mapping = {
                "state": self._state.value,
                "failures": str(self._failure_count),
                "last_failure": str(self._last_failure_time),
            }
        await redis_client.hset(key, mapping=mapping)

    async def load_from_redis(self, redis_client, key: str) -> None:
        data = await redis_client.hgetall(key)
        if not data:
            return
        with self._lock:
            self._state = CircuitState(data.get(b"state", b"closed").decode())
            self._failure_count = int(data.get(b"failures", b"0"))
            self._last_failure_time = float(data.get(b"last_failure", b"0"))
            self._probe_inflight = False
            self._probe_started_at = 0


class CircuitBreakerRegistry:
    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_or_create(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
    ) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name, failure_threshold, recovery_timeout
                )
            return self._breakers[name]


circuit_registry = CircuitBreakerRegistry()
