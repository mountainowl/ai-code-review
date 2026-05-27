from __future__ import annotations

from contextlib import contextmanager
import sys
import re
from typing import Any, Iterator

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from llm_reviewer.telemetry.config import TelemetryConfig
from llm_reviewer.telemetry.cost import TokenUsage


METRIC_ATTRIBUTE_KEYS = frozenset(
    {
        "repo",
        "model",
        "prompt_version",
        "review_mode",
        "status",
        "dry_run",
        "finding_type",
        "severity",
        "category",
        "skip_reason",
        "error_type",
        "component",
        "operation",
        "outcome",
        "reviewer",
    }
)

_CONFIGURED = False
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")
_ENUM_VALUES = {
    "finding_type": {"issue", "suggestion", "question", "unknown"},
    "severity": {"blocking", "non-blocking", "unknown"},
}


def metric_attrs(**attrs: object) -> dict[str, str | bool | int | float]:
    out: dict[str, str | bool | int | float] = {}
    for key, value in attrs.items():
        if key not in METRIC_ATTRIBUTE_KEYS or value is None:
            continue
        if isinstance(value, bool | int | float):
            out[key] = value
        elif isinstance(value, str):
            out[key] = _safe_label_value(key, value)
    return out


def _safe_label_value(key: str, value: str) -> str:
    normalized = value.strip()
    allowed = _ENUM_VALUES.get(key)
    if allowed is not None:
        lowered = normalized.lower()
        return lowered if lowered in allowed else "unknown"
    if not _SAFE_LABEL.match(normalized):
        return "unknown"
    return normalized


class ReviewTelemetry:
    def __init__(self, config: TelemetryConfig):
        self.config = config
        self.enabled = config.enabled
        self.tracer = trace.get_tracer("llm_reviewer")
        self.meter = metrics.get_meter("llm_reviewer")
        self._runs = _safe_instrument(self.meter.create_counter, "llm_review.runs")
        self._findings = _safe_instrument(self.meter.create_counter, "llm_review.findings")
        self._tokens = _safe_instrument(self.meter.create_counter, "llm_review.tokens")
        self._cost = _safe_instrument(self.meter.create_counter, "llm_review.cost.usd")
        self._failures = _safe_instrument(self.meter.create_counter, "llm_review.failures")
        self._review_duration = _safe_instrument(
            self.meter.create_histogram,
            "llm_review.latency.review_seconds",
        )
        self._queue_latency = _safe_instrument(
            self.meter.create_histogram,
            "llm_review.latency.queue_seconds",
        )

    @classmethod
    def from_config(cls, config: TelemetryConfig) -> "ReviewTelemetry":
        configure_otel(config)
        return cls(config)

    @contextmanager
    def span(self, name: str, **attrs: object) -> Iterator[Span | None]:
        if not self.enabled:
            yield None
            return
        span = None
        try:
            span = self.tracer.start_span(name)
            for key, value in attrs.items():
                if value is not None and _safe_span_value(value):
                    span.set_attribute(key, value)
        except Exception:
            span = None
        try:
            yield span
        except Exception:
            if span is not None:
                try:
                    sys_exc = sys.exc_info()[1]
                    span.record_exception(sys_exc)
                    span.set_status(Status(StatusCode.ERROR, str(sys_exc)))
                except Exception:
                    pass
            raise
        finally:
            if span is not None:
                try:
                    span.end()
                except Exception:
                    pass

    def add_event(self, span: Span | None, name: str, **attrs: object) -> None:
        if not self.enabled or span is None:
            return
        try:
            span.add_event(name, {key: value for key, value in attrs.items() if _safe_span_value(value)})
        except Exception:
            return

    def record_review_done(
        self,
        *,
        repo: str,
        model: str,
        status: str,
        review_mode: str,
        dry_run: bool,
        duration_seconds: float,
        tokens: TokenUsage,
        cost_usd: float,
    ) -> None:
        attrs = metric_attrs(repo=repo, model=model, status=status, review_mode=review_mode, dry_run=dry_run)
        self._add(self._runs, 1, attrs)
        self._record(self._review_duration, duration_seconds, attrs)
        self._add_token("input", tokens.input, attrs)
        self._add_token("output", tokens.output, attrs)
        self._add_token("cached", tokens.cached, attrs)
        self._add_token("total", tokens.total, attrs)
        self._add(self._cost, cost_usd, attrs)

    def record_finding(self, *, repo: str, status: str, finding: dict[str, Any], dry_run: bool) -> None:
        attrs = metric_attrs(
            repo=repo,
            status=status,
            dry_run=dry_run,
            finding_type=finding.get("type"),
            severity=finding.get("severity"),
            category=finding.get("category"),
        )
        self._add(self._findings, 1, attrs)

    def record_failure(self, *, repo: str, error_type: str, operation: str) -> None:
        self._add(self._failures, 1, metric_attrs(repo=repo, error_type=error_type, operation=operation))

    def record_queue_latency(self, *, repo: str, seconds: float) -> None:
        self._record(self._queue_latency, seconds, metric_attrs(repo=repo))

    def _add_token(self, kind: str, value: int | None, attrs: dict[str, str | bool | int | float]) -> None:
        if value is None:
            return
        enriched = dict(attrs)
        enriched["operation"] = kind
        self._add(self._tokens, value, enriched)

    def _add(self, counter: Any, value: int | float, attrs: dict[str, str | bool | int | float]) -> None:
        if not self.enabled or counter is None:
            return
        try:
            counter.add(value, attrs)
        except Exception:
            return

    def _record(self, histogram: Any, value: int | float, attrs: dict[str, str | bool | int | float]) -> None:
        if not self.enabled or histogram is None:
            return
        try:
            histogram.record(value, attrs)
        except Exception:
            return


def configure_otel(config: TelemetryConfig) -> None:
    global _CONFIGURED
    if _CONFIGURED or not config.enabled:
        return
    try:
        resource = Resource.create(
            {
                "service.name": config.service_name,
                "deployment.environment.name": config.environment,
            }
        )
        if config.otlp_endpoint:
            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=config.otlp_endpoint, timeout=5))
            )
            trace.set_tracer_provider(tracer_provider)
            exporter = OTLPMetricExporter(endpoint=config.otlp_endpoint, timeout=5)
            reader = PeriodicExportingMetricReader(
                exporter,
                export_interval_millis=config.export_interval_seconds * 1000,
            )
            metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
        else:
            trace.set_tracer_provider(TracerProvider(resource=resource))
        _CONFIGURED = True
    except Exception:
        _CONFIGURED = True


def _safe_instrument(factory: Any, *args: object) -> Any:
    try:
        return factory(*args)
    except Exception:
        return None


def _safe_span_value(value: object) -> bool:
    return isinstance(value, bool | int | float | str)
