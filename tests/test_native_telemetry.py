"""Actual native telemetry and explicit public export policy."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.suite_sdk


@pytest.mark.parametrize(
    "provider_mode,mode",
    [
        ("global", "default"),
        ("global", "enabled"),
        ("explicit", "enabled"),
        ("global", "disabled"),
        ("explicit", "disabled"),
        ("global", "empty"),
        ("global", "signals"),
        ("global", "logs-only"),
        ("global", "override"),
    ],
)
def test_native_export_contract(provider_mode, mode):
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OTEL_")
    }
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("native_telemetry_probe.py")),
            "netbox_sdk.mock.app",
            mode,
            provider_mode,
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OTLP export contract passed" in result.stdout


def test_public_factory_preserves_unconfigured_environment(monkeypatch):
    from netbox_sdk.mock.app import create_mock_app

    for name in list(os.environ):
        if name.startswith("OTEL_"):
            monkeypatch.delenv(name)
    create_mock_app()
    assert not any(name.startswith("OTEL_") for name in os.environ)
