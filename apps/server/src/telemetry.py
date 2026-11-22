"""OpenTelemetry traces and metrics of Xavier processes, exported over OTLP."""

import functools
import time
from collections.abc import Awaitable, Callable

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter as GrpcMetricExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter as HttpMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpSpanExporter,
)
from opentelemetry.metrics import Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from src.config import TelemetrySettings

tracer = trace.get_tracer("xavier")
_meter = metrics.get_meter("xavier")

TURN_DURATION = _meter.create_histogram(
    "xavier.turn.duration", unit="s", description="Agent turns by final status."
)
TURN_RETRIES = _meter.create_counter(
    "xavier.turn.retries", description="Turn attempts after the first one."
)
TOOL_CALLS = _meter.create_counter(
    "xavier.tool.calls", description="Executed tool calls by tool and outcome."
)
LLM_TOKENS = _meter.create_counter(
    "xavier.llm.tokens", unit="{token}", description="Model tokens by model and kind."
)
LLM_COST = _meter.create_counter("xavier.llm.cost", unit="USD", description="Model cost by model.")
WORKSPACE_ACTIVATION = _meter.create_histogram(
    "xavier.workspace.activation.duration", unit="s", description="Workspace activations."
)
COMPUTER_START = _meter.create_histogram(
    "xavier.computer.start.duration", unit="s", description="Cold Computer starts."
)
EXECUTION_DURATION = _meter.create_histogram(
    "xavier.execution.duration", unit="s", description="Execute runs."
)
CHECKPOINT_DURATION = _meter.create_histogram(
    "xavier.checkpoint.duration", unit="s", description="Workspace checkpoints."
)
CHECKPOINT_SIZE = _meter.create_histogram(
    "xavier.checkpoint.size", unit="By", description="Workspace size at checkpoint."
)
OBSERVE_ERRORS = _meter.create_counter(
    "xavier.observe.poll.errors", description="Observe resources that could not be observed."
)


def configure_telemetry(settings: TelemetrySettings, process: str) -> None:
    """Export traces and metrics of ``process`` when an OTLP endpoint is configured."""
    if settings.otlp_endpoint is None:
        return
    traces, metrics_exporter = _exporters(settings)
    resource = Resource.create({SERVICE_NAME: f"{settings.service_name}-{process}"})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(traces))
    trace.set_tracer_provider(tracer_provider)
    reader = PeriodicExportingMetricReader(metrics_exporter)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))


def _exporters(settings: TelemetrySettings) -> tuple[GrpcSpanExporter | HttpSpanExporter, object]:
    """Build the span and metric exporters for the configured collector."""
    endpoint = settings.otlp_endpoint or ""
    token = None if settings.api_key is None else settings.api_key.get_secret_value()
    if endpoint.startswith(("http://", "https://")):
        base = endpoint.rstrip("/")
        headers = {} if token is None else {"authorization": token}
        return (
            HttpSpanExporter(endpoint=f"{base}/v1/traces", headers=headers),
            HttpMetricExporter(endpoint=f"{base}/v1/metrics", headers=headers),
        )
    headers_grpc = None if token is None else (("authorization", token),)
    return (
        GrpcSpanExporter(endpoint=endpoint, headers=headers_grpc),
        GrpcMetricExporter(endpoint=endpoint, headers=headers_grpc),
    )


def traced[**P, R](
    name: str, duration: Histogram | None = None
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Run the coroutine function in span ``name`` and record its duration with its outcome."""

    def decorate(function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(function)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            started = time.monotonic()
            outcome = "error"
            with tracer.start_as_current_span(name):
                try:
                    result = await function(*args, **kwargs)
                    outcome = "ok"
                    return result
                finally:
                    if duration is not None:
                        duration.record(time.monotonic() - started, {"outcome": outcome})

        return wrapper

    return decorate
