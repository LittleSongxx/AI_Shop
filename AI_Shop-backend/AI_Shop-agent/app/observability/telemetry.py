from __future__ import annotations

from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = structlog.get_logger()

_provider: TracerProvider | None = None
_app: Any | None = None
_httpx_instrumentor: HTTPXClientInstrumentor | None = None
_redis_instrumentor: RedisInstrumentor | None = None


def get_tracer():
    return trace.get_tracer("aishop.agent")


def current_trace_id() -> str | None:
    """Return the active W3C trace id, never an unrelated application UUID."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}"


def current_span_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"{context.span_id:016x}"


def _telemetry_enabled() -> bool:
    from app.config.settings import get_settings

    settings = get_settings()
    endpoint = settings.otel_otlp_endpoint.strip()
    return bool(settings.otel_enabled and endpoint)


def _build_provider():
    """Create the TracerProvider from settings. Returns None on failure."""
    from app.config.settings import get_settings

    settings = get_settings()
    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.app_env,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=settings.otel_otlp_endpoint.strip(),
        insecure=not settings.otel_otlp_endpoint.strip().lower().startswith("https://"),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider


def _instrument_httpx_redis(provider) -> None:
    global _httpx_instrumentor, _redis_instrumentor

    _httpx_instrumentor = HTTPXClientInstrumentor()
    _httpx_instrumentor.instrument(tracer_provider=provider)
    _redis_instrumentor = RedisInstrumentor()
    _redis_instrumentor.instrument(tracer_provider=provider)


def configure_telemetry(app: Any) -> bool:
    """Enable tracing only when explicitly configured with an OTLP endpoint."""
    global _provider, _app

    if not _telemetry_enabled():
        return False
    if _provider is not None:
        return True

    try:
        provider = _build_provider()
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        _instrument_httpx_redis(provider)
        _provider = provider
        _app = app
        logger.info(
            "telemetry_enabled",
            endpoint=get_settings_otlp_endpoint(),
            service=get_settings_service_name(),
        )
        return True
    except Exception as exc:
        logger.warning("telemetry_enable_failed", error=type(exc).__name__)
        return False


def configure_worker_telemetry() -> bool:
    """Worker 进程没有 FastAPI app，只做 provider + httpx/redis 插桩。

    与 API 进程是独立进程，各自的全局状态互不干扰；同样遵守
    ``OTEL_ENABLED`` 为空则完全关闭的约定（P0-3：Worker 缺 telemetry
    导致任务、LLM、MQ、工具链路在 trace 里断链）。
    """
    global _provider

    if not _telemetry_enabled():
        return False
    if _provider is not None:
        return True
    try:
        provider = _build_provider()
        _instrument_httpx_redis(provider)
        _provider = provider
        logger.info(
            "worker_telemetry_enabled",
            endpoint=get_settings_otlp_endpoint(),
            service=get_settings_service_name(),
        )
        return True
    except Exception as exc:
        logger.warning("worker_telemetry_enable_failed", error=type(exc).__name__)
        return False


def get_settings_otlp_endpoint() -> str:
    from app.config.settings import get_settings

    return get_settings().otel_otlp_endpoint.strip()


def get_settings_service_name() -> str:
    from app.config.settings import get_settings

    return get_settings().otel_service_name


def shutdown_telemetry() -> None:
    global _provider, _app, _httpx_instrumentor, _redis_instrumentor

    if _app is not None:
        try:
            FastAPIInstrumentor().uninstrument_app(_app)
        except Exception:
            pass
    if _httpx_instrumentor is not None:
        try:
            _httpx_instrumentor.uninstrument()
        except Exception:
            pass
    if _redis_instrumentor is not None:
        try:
            _redis_instrumentor.uninstrument()
        except Exception:
            pass
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:
            pass
    _provider = None
    _app = None
    _httpx_instrumentor = None
    _redis_instrumentor = None
