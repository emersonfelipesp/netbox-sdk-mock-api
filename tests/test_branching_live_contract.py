"""Hermetic coverage of the live branching helper's real SDK construction."""

import socket
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import test_branching_live

from netbox_sdk.branching import BranchingClient
from netbox_sdk.client import ApiResponse, NetBoxApiClient

pytestmark = pytest.mark.suite_sdk


async def test_live_branching_helper_uses_supported_factory_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def refuse_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("The helper contract regression must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    monkeypatch.setattr(socket, "create_connection", refuse_network)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("NETBOX_URL", "http://localhost:8080")
    monkeypatch.setenv("NETBOX_TOKEN", "synthetic-ci-token")
    request = AsyncMock(return_value=ApiResponse(status=200, text="{}"))
    monkeypatch.setattr(NetBoxApiClient, "request", request)

    branching = await test_branching_live._client_or_skip()

    assert isinstance(branching, BranchingClient)
    assert isinstance(branching._client, NetBoxApiClient)
    assert branching._client.config.base_url == "http://localhost:8080"
    assert branching._client.config.token_secret == "synthetic-ci-token"
    request.assert_awaited_once_with("GET", "/api/plugins/branching/")
