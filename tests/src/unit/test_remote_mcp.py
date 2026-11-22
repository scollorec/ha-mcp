"""Unit tests for the embedded server's generic remote MCP provider."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from custom_components.ha_mcp_tools.const import (
    OPT_CORE_MCP_ENTRY_ID,
    OPT_CORE_MCP_NAMESPACE,
)
from custom_components.ha_mcp_tools.remote_mcp import (
    register_remote_mcp_if_configured,
    register_remote_mcp_upstream,
    remote_mcp_config_error,
    remote_mcp_config_snapshot,
)
from ha_mcp.providers.home_assistant_core import HomeAssistantCoreMCPProvider
from ha_mcp._vendor.fastmcp.server.providers.proxy import ProxyProvider

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

    with patch(
        "ha_mcp._vendor.fastmcp.server.providers.proxy.ProxyClient"
    ) as proxy_client:
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

        # Building the upstream client is local-only; it makes no HTTP request.
        provider.client_factory()
        transport = proxy_client.call_args.args[0]
        assert transport.url == ENDPOINT
        assert transport.headers == {"Authorization": "Bearer upstream-secret"}
        assert proxy_client.call_args.kwargs == {"forward_incoming_headers": False}


def test_core_mcp_selection_is_included_in_worker_snapshot() -> None:
    """Pass the Core config-entry identity to the embedded worker, not its URL."""
    config = remote_mcp_config_snapshot(
        {
            OPT_CORE_MCP_ENTRY_ID: "fedora-entry",
            OPT_CORE_MCP_NAMESPACE: "Fedora_Workstation",
        },
        {},
    )

    assert config == ("remote", "", "", "fedora-entry", "fedora_workstation")


def test_selected_core_mcp_entry_is_registered_without_a_second_url() -> None:
    """The worker setup attaches the provider using only the existing entry ID."""
    mcp = Mock()

    register_remote_mcp_if_configured(
        mcp,
        ("remote", "", "", "fedora-entry", "fedora_workstation"),
    )

    mcp.add_provider.assert_called_once()
    provider = mcp.add_provider.call_args.args[0]
    assert isinstance(provider, HomeAssistantCoreMCPProvider)
    assert mcp.add_provider.call_args.kwargs == {"namespace": "fedora_workstation"}
