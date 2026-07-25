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


def configure_telemetry(app: Any) -> bool:
    """Enable tracing only when explicitly configured with an OTLP endpoint."""
    global _provider, _app, _httpx_instrumentor, _redis_instrumentor

    from app.config.settings import get_settings

    settings = get_settings()
    endpoint = settings.otel_otlp_endpoint.strip()
    if not settings.otel_enabled or not endpoint:
        return False
    if _provider is not None:
        return True

    try:
        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": "current",
                "deployment.environment": settings.app_env,
            }
        )
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            insecure=not endpoint.lower().startswith("https://"),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        _httpx_instrumentor = HTTPXClientInstrumentor()
        _httpx_instrumentor.instrument(tracer_provider=provider)
        _redis_instrumentor = RedisInstrumentor()
        _redis_instrumentor.instrument(tracer_provider=provider)

        _provider = provider
        _app = app
        logger.info("telemetry_enabled", endpoint=endpoint, service=settings.otel_service_name)
        return True
    except Exception as exc:
        logger.warning("telemetry_enable_failed", error=type(exc).__name__)
        return False


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
