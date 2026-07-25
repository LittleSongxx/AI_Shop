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
    """Circuit breaker with single-probe half-open recovery.

    All public methods are synchronous and internally guarded by a lock, so they
    are safe to call from coroutines as well as from worker threads. The
    half-open state admits exactly one trial request: letting the full load
    through the moment the recovery window elapses is what re-opens a breaker
    against a backend that was only just coming back.
    """

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
        """Advance OPEN -> HALF_OPEN once the recovery window has elapsed.

        Caller must hold the lock.
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
        """Return True when the call may proceed.

        In HALF_OPEN only one probe is admitted at a time. A probe that never
        reports back (caller crashed before record_success/record_failure) is
        reclaimed after probe_timeout so the breaker cannot wedge shut.
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
        """Give back a half-open probe slot without recording an outcome.

        For callers that were admitted but then aborted before actually hitting
        the dependency (missing input, upstream returned nothing). Without this
        the slot would stay reserved until probe_timeout reclaims it.
        """
        with self._lock:
            self._probe_inflight = False
            self._probe_started_at = 0

    def record_failure(self) -> None:

        with self._lock:
            self._last_failure_time = time.time()
            self._probe_inflight = False
            self._probe_started_at = 0

            if self._state == CircuitState.HALF_OPEN:
                # A failed probe means the dependency is still down: re-open
                # immediately rather than waiting for the threshold again.
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
