"""Optional OTLP tracing; credentials and enterprise content never become span attributes."""

from opentelemetry import trace


def configure_tracing(enabled: bool) -> None:
    if not enabled:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "enterprise-agent"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def request_span():
    return trace.get_tracer("enterprise-agent").start_as_current_span("http_request")
