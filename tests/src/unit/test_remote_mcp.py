"""Unit tests for the embedded server's generic remote MCP provider."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import Mock

import pytest

from custom_components.ha_mcp_tools.remote_mcp import (
    register_remote_mcp_upstream,
    remote_mcp_config_error,
)
from ha_mcp._vendor.fastmcp.server.providers.proxy import ProxyClient, ProxyProvider

ENDPOINT = "http://192.168.1.55:8765/mcp"


@pytest.mark.parametrize("namespace", ["fedora", "remote", "dev_tools", "a1"])
def test_valid_remote_mcp_namespace(namespace: str) -> None:
    assert remote_mcp_config_error(namespace, ENDPOINT) is None


@pytest.mark.parametrize("namespace", ["", "Fedora", "bad name", "1remote", "x" * 33])
def test_invalid_remote_mcp_namespace(namespace: str) -> None:
    assert remote_mcp_config_error(namespace, ENDPOINT) == (
        "invalid_remote_mcp_namespace"
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/mcp",
        "http:///mcp",
        "http://user:password@example.com/mcp",
        "http://example.com/mcp#fragment",
        "http://example.com:bad-port/mcp",
    ],
)
def test_invalid_remote_mcp_endpoint(url: str) -> None:
    assert remote_mcp_config_error("remote", url) == "invalid_remote_mcp_url"


def test_blank_endpoint_disables_upstream() -> None:
    assert remote_mcp_config_error("not a namespace", "") is None


def test_provider_is_registered_under_namespace_and_uses_explicit_auth_only() -> None:
    mcp = Mock()

    register_remote_mcp_upstream(
        mcp,
        namespace="fedora",
        url=ENDPOINT,
        bearer_token="upstream-secret",
    )

    mcp.add_provider.assert_called_once()
    provider = mcp.add_provider.call_args.args[0]
    assert isinstance(provider, ProxyProvider)
    assert mcp.add_provider.call_args.kwargs == {"namespace": "fedora"}

    # Constructing the proxy client is local-only; it does not contact the URL.
    client = cast(ProxyClient[Any], provider.client_factory())
    assert client.transport.url == ENDPOINT
    assert client.transport.headers == {"Authorization": "Bearer upstream-secret"}
    assert client._transport_options.forward_incoming_headers is False
