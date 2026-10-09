"""Exercise native 0.143.0 precedence and excluded request context in real factories."""

import asyncio
import importlib
import os
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.telemetry import TelemetryConfig
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import SpanKind

TARGETS = ["netbox"]
pytestmark = pytest.mark.suite_sdk


def application(target: str, config: TelemetryConfig, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("PROXMOX_API_MODE", "mock")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("PROXMOX_MOCK_SERVICE", target)
    module_name, factory_name = ("netbox_sdk.mock.app", "create_mock_app")
    return getattr(importlib.import_module(module_name), factory_name)(telemetry=config)


def auto_configuration(environment: str | None, explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return (environment or "").lower() == "true"


def explicit_config(explicit: bool | None) -> TelemetryConfig:
    return {} if explicit is None else {"auto_configure": explicit}


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("environment", [None, "", "false", "true", "TRUE"])
@pytest.mark.parametrize("explicit", [None, False, True])
def test_native_auto_configuration_precedence(
    target: str,
    environment: str | None,
    explicit: bool | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    for name in ("OTEL_SERVICE_NAME", "OTEL_EXPORTER_OTLP_ENDPOINT", "FASTAPI_OTEL_AUTO_CONFIGURE"):
        monkeypatch.delenv(name, raising=False)
    if environment is not None:
        monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", environment)
    config = explicit_config(explicit)
    original = dict(config)
    app = application(target, config, monkeypatch)
    expected = auto_configuration(environment, explicit)
    assert app._telemetry["auto_configure"] is expected
    assert config == original
    assert "OTEL_SERVICE_NAME" not in os.environ
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ
    assert os.getenv("FASTAPI_OTEL_AUTO_CONFIGURE") == environment


@pytest.mark.parametrize(
    "environment,explicit,expected",
    [
        (None, None, False),
        ("", None, False),
        ("false", None, False),
        ("true", None, True),
        ("TRUE", None, True),
        ("true", False, False),
        ("false", True, True),
    ],
)
def test_privacy_export_predicate_matches_native(
    environment: str | None,
    explicit: bool | None,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("netbox_sdk.mock.telemetry")
    monkeypatch.delenv("FASTAPI_OTEL_AUTO_CONFIGURE", raising=False)
    if environment is not None:
        monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", environment)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    config = explicit_config(explicit)
    assert module._environment_export(config, "TRACES") is expected
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    assert not module._environment_export(config, "TRACES")
    monkeypatch.delenv("OTEL_TRACES_EXPORTER")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    assert not module._environment_export(config, "TRACES")


async def exercise_context(app: FastAPI, fail: bool) -> list[Any]:
    from fastapi.telemetry._api import _REQUEST_TELEMETRY_KEY

    observations: list[Any] = []

    @app.get("/excluded-native-context")
    async def excluded() -> dict[str, bool]:
        observations.append(otel_context.get_value(_REQUEST_TELEMETRY_KEY))
        if fail:
            raise RuntimeError("The nested excluded-request failure is intentional.")
        return {"excluded": True}

    @app.get("/parent-native-context")
    async def parent() -> dict[str, int]:
        inherited = otel_context.get_value(_REQUEST_TELEMETRY_KEY)
        assert inherited is not None
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://local.test") as client:
            response = await client.get("/excluded-native-context")
        assert otel_context.get_value(_REQUEST_TELEMETRY_KEY) is inherited
        return {"nested_status": response.status_code}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://local.test") as client:
        response = await client.get("/parent-native-context")
    assert response.json() == {"nested_status": 500 if fail else 200}
    return observations


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("fail", [False, True])
def test_excluded_nested_context_restoration(
    target: str,
    fail: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.telemetry._api import _REQUEST_TELEMETRY_KEY

    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    original = otel_context.get_value(_REQUEST_TELEMETRY_KEY)
    config: TelemetryConfig = {
        "auto_configure": False,
        "tracer_provider": provider,
        "operation_spans": True,
        "exclude": lambda scope: scope["path"] == "/excluded-native-context",
    }
    try:
        app = application(target, config, monkeypatch)
        assert asyncio.run(exercise_context(app, fail)) == [None]
        assert otel_context.get_value(_REQUEST_TELEMETRY_KEY) is original
        assert config["tracer_provider"] is provider
        spans = exporter.get_finished_spans()
        assert_native_parent_spans(spans)
    finally:
        provider.shutdown()


def assert_native_parent_spans(spans: Any) -> None:
    """Verify every enabled native child and its originating parent endpoint."""
    assert [span.name for span in spans] == [
        "fastapi.dependencies",
        "fastapi.endpoint",
        "fastapi.serialization",
        "GET /parent-native-context",
    ]
    parent_span = spans[-1]
    assert parent_span.kind is SpanKind.SERVER
    assert parent_span.parent is None
    for child in spans[:-1]:
        assert child.kind is SpanKind.INTERNAL
        assert child.context.trace_id == parent_span.context.trace_id
        assert child.parent.span_id == parent_span.context.span_id
    assert spans[1].attributes["code.function.name"] == (
        f"{__name__}.exercise_context.<locals>.parent"
    )


@pytest.mark.parametrize("target", TARGETS)
def test_sdk_disable_preserves_caller_owned_providers(
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    spans = InMemorySpanExporter()
    logs = InMemoryLogRecordExporter()
    reader = InMemoryMetricReader()
    tracer = TracerProvider(sampler=ALWAYS_ON)
    tracer.add_span_processor(SimpleSpanProcessor(spans))
    logger = LoggerProvider()
    logger.add_log_record_processor(SimpleLogRecordProcessor(logs))
    meter = MeterProvider(metric_readers=[reader])
    config: TelemetryConfig = {
        "auto_configure": True,
        "tracer_provider": tracer,
        "logger_provider": logger,
        "meter_provider": meter,
    }
    original = dict(config)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    try:
        app = application(target, config, monkeypatch)
        assert config == original
        assert {
            key: app._telemetry[key] for key in ("auto_configure", "tracing", "metrics", "logs")
        } == {
            "auto_configure": False,
            "tracing": False,
            "metrics": False,
            "logs": False,
        }
        disabled_routes(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            assert client.get("/sdk-disabled-success").status_code == 200
            assert client.get("/sdk-disabled-error").status_code == 500
        assert_no_native_signals(spans, logs, reader)
        assert_providers_still_owned(tracer, logger, meter, spans, logs, reader)
    finally:
        tracer.shutdown()
        logger.shutdown()
        meter.shutdown()


def disabled_routes(app: FastAPI) -> None:
    @app.get("/sdk-disabled-success")
    async def success() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/sdk-disabled-error")
    async def error() -> None:
        raise RuntimeError("This disabled-signal request failure is intentional.")


def assert_no_native_signals(spans: Any, logs: Any, reader: Any) -> None:
    assert spans.get_finished_spans() == ()
    assert logs.get_finished_logs() == ()
    assert reader.get_metrics_data() is None


def assert_providers_still_owned(
    tracer: Any,
    logger: Any,
    meter: Any,
    spans: Any,
    logs: Any,
    reader: Any,
) -> None:
    with tracer.get_tracer("caller").start_as_current_span("caller-still-owned"):
        pass
    logger.get_logger("caller").emit(body="caller-still-owned")
    meter.get_meter("caller").create_counter("caller_still_owned").add(1)
    assert [span.name for span in spans.get_finished_spans()] == ["caller-still-owned"]
    assert len(logs.get_finished_logs()) == 1
    assert reader.get_metrics_data() is not None


@pytest.mark.parametrize("signal", ["TRACES", "LOGS"])
def test_signal_specific_empty_endpoint_is_not_enabled(
    signal: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("netbox_sdk.mock.telemetry")
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    monkeypatch.delenv(f"OTEL_{signal}_EXPORTER", raising=False)
    monkeypatch.setenv(f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT", "")
    assert not module._environment_export({}, signal)
    monkeypatch.setenv(f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT", "http://127.0.0.1:4318")
    assert module._environment_export({}, signal)
