from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    cached_input_per_1m: float = 0.0


@dataclass(frozen=True)
class TelemetryConfig:
    enabled: bool = False
    service_name: str = "llm-reviewer"
    environment: str = "dev"
    otlp_endpoint: str = ""
    otlp_protocol: str = "grpc"
    export_interval_seconds: int = 30
    emit_finding_events: bool = True
    emit_outcome_sync: bool = True
    pricing: dict[str, ModelPricing] = field(default_factory=lambda: {"default": ModelPricing()})

    def price_for(self, model: str | None) -> ModelPricing:
        if model and model in self.pricing:
            return self.pricing[model]
        return self.pricing.get("default", ModelPricing())


def telemetry_config_from_dict(data: dict[str, Any]) -> TelemetryConfig:
    raw = data.get("telemetry") or {}
    if not isinstance(raw, dict):
        raise ValueError("telemetry must be a table")
    protocol = str(raw.get("otlp_protocol", "grpc"))
    if protocol != "grpc":
        raise ValueError("otlp_protocol must be grpc")

    pricing = _parse_pricing(raw.get("pricing") or {})
    return TelemetryConfig(
        enabled=bool(raw.get("enabled", False)),
        service_name=str(raw.get("service_name", "llm-reviewer")),
        environment=str(raw.get("environment", "dev")),
        otlp_endpoint=str(raw.get("otlp_endpoint", "")),
        otlp_protocol=protocol,
        export_interval_seconds=_positive_int(raw.get("export_interval_seconds", 30), "export_interval_seconds"),
        emit_finding_events=bool(raw.get("emit_finding_events", True)),
        emit_outcome_sync=bool(raw.get("emit_outcome_sync", True)),
        pricing=pricing,
    )


def _parse_pricing(raw: dict[str, Any]) -> dict[str, ModelPricing]:
    pricing = {
        str(model): ModelPricing(
            input_per_1m=_non_negative_float(values.get("input_per_1m", 0.0), f"pricing.{model}.input_per_1m"),
            output_per_1m=_non_negative_float(values.get("output_per_1m", 0.0), f"pricing.{model}.output_per_1m"),
            cached_input_per_1m=_non_negative_float(
                values.get("cached_input_per_1m", 0.0),
                f"pricing.{model}.cached_input_per_1m",
            ),
        )
        for model, values in raw.items()
        if isinstance(values, dict)
    }
    pricing.setdefault("default", ModelPricing())
    return pricing


def _positive_int(value: object, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive integer") from None
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _non_negative_float(value: object, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a non-negative number") from None
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return parsed
