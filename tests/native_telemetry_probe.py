import importlib
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi import Body, WebSocket
from fastapi.testclient import TestClient
from opentelemetry import _logs, metrics, trace
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

os.environ["FASTAPI_OTEL_AUTO_CONFIGURE"] = "true"
payloads = {}


class Collector(BaseHTTPRequestHandler):
    def do_POST(self):
        payloads.setdefault(self.path, []).append(
            self.rfile.read(int(self.headers["Content-Length"]))
        )
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
if sys.argv[2] not in {"disabled", "default"}:
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"http://127.0.0.1:{server.server_port}"
if sys.argv[2] == "override":
    os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = (
        f"http://127.0.0.1:{server.server_port}/custom/traces"
    )
if sys.argv[2] == "logs-only":
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
    os.environ["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] = (
        f"http://127.0.0.1:{server.server_port}/v1/logs"
    )
if sys.argv[2] == "empty":
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
if sys.argv[2] == "signals":
    os.environ["OTEL_TRACES_EXPORTER"] = "none"
    os.environ["OTEL_METRICS_EXPORTER"] = "none"
os.environ["OTEL_SERVICE_NAME"] = "telemetry-test"
if sys.argv[2] == "disabled":
    os.environ["OTEL_SDK_DISABLED"] = "true"
module = importlib.import_module(sys.argv[1])
factory = module.create_mock_app
before = (trace.get_tracer_provider(), metrics.get_meter_provider(), _logs.get_logger_provider())
providers = (
    {
        "auto_configure": True,
        "tracer_provider": TracerProvider(),
        "meter_provider": MeterProvider(),
        "logger_provider": LoggerProvider(),
    }
    if sys.argv[3] == "explicit"
    else None
)
app = factory(telemetry=providers)
secret = "otel-private-canary-credential"


@app.get("/_test/{number}")
async def number(number: int):
    return {"number": number}


@app.post("/_body")
async def body(number: int = Body()):
    return {"number": number}


@app.get("/_failure")
async def failure():
    raise RuntimeError(secret)


@app.websocket("/_ws")
async def socket(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_text("ready")
    raise RuntimeError(secret)


for _ in range(2):
    with TestClient(app, raise_server_exceptions=False) as client:
        assert (
            client.get(
                "/_test/1?token=" + secret, headers={"Authorization": "Bearer " + secret}
            ).status_code
            == 200
        )
        assert client.get("/_test/" + secret).status_code == 422
        assert client.get("/_failure").status_code == 500
        assert client.post("/_body", json=secret).status_code == 422
        try:
            with client.websocket_connect("/_ws") as connection:
                assert connection.receive_text() == "ready"
        except RuntimeError as error:
            assert str(error) == secret
if providers is not None:
    assert before == (
        trace.get_tracer_provider(),
        metrics.get_meter_provider(),
        _logs.get_logger_provider(),
    )
    with (
        providers["tracer_provider"]
        .get_tracer("ownership")
        .start_as_current_span("caller-provider-alive") as span
    ):
        assert span.is_recording() == (sys.argv[2] != "disabled")
    for name in ("tracer_provider", "meter_provider", "logger_provider"):
        assert providers[name].force_flush()
        providers[name].shutdown()
server.shutdown()
server.server_close()
if sys.argv[2] in {"disabled", "empty", "default"}:
    assert not payloads, payloads
elif sys.argv[2] in {"signals", "logs-only"}:
    assert set(payloads) == {"/v1/logs"}
    assert all(secret.encode() not in payload for batch in payloads.values() for payload in batch)
else:
    trace_path = "/custom/traces" if sys.argv[2] == "override" else "/v1/traces"
    decoders = {
        trace_path: ExportTraceServiceRequest,
        "/v1/metrics": ExportMetricsServiceRequest,
        "/v1/logs": ExportLogsServiceRequest,
    }
    assert set(payloads) == set(decoders), payloads.keys()
    assert all(
        secret.encode() not in payload for batches in payloads.values() for payload in batches
    )
    spans = []
    logs = []
    metric_names = set()
    for path, decoder in decoders.items():
        for payload in payloads[path]:
            decoded = decoder.FromString(payload)
            assert decoded.ListFields()
            if path == trace_path:
                spans.extend(
                    span
                    for resource in decoded.resource_spans
                    for scope in resource.scope_spans
                    for span in scope.spans
                )
            if path == "/v1/metrics":
                metric_names.update(
                    metric.name
                    for resource in decoded.resource_metrics
                    for scope in resource.scope_metrics
                    for metric in scope.metrics
                )
            if path == "/v1/logs":
                logs.extend(
                    record
                    for resource in decoded.resource_logs
                    for scope in resource.scope_logs
                    for record in scope.log_records
                )
    assert sum(span.name == "GET /_test/{number}" for span in spans) == 4
    assert sum(span.name == "WS /_ws" for span in spans) == 2
    assert len(logs) == 8
    assert "http.server.request.duration" in metric_names
    assert any(
        attribute.key == "exception.type" for record in logs for attribute in record.attributes
    )
print("OTLP export contract passed")
