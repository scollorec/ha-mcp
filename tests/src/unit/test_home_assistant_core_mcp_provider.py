"""Tests for selecting and registering an existing Home Assistant Core MCP connection."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from ha_mcp.providers.home_assistant_core import (
    HomeAssistantCoreMCPProvider,
    register_home_assistant_core_mcp,
)


def test_provider_registers_one_existing_core_entry_under_a_namespace() -> None:
    """The configured Core connection is composed under the requested namespace."""
    mcp = Mock()

    provider = register_home_assistant_core_mcp(
        mcp,
        entry_id="fedora-entry",
        namespace="fedora_workstation",
    )

    assert isinstance(provider, HomeAssistantCoreMCPProvider)
    assert provider.entry_id == "fedora-entry"
    assert provider.cache_ttl == 15.0
    mcp.add_provider.assert_called_once_with(provider, namespace="fedora_workstation")


@pytest.mark.parametrize("namespace", ["", "Fedora", "bad namespace", "1bad", "x" * 33])
def test_provider_rejects_invalid_namespace(namespace: str) -> None:
    with pytest.raises(ValueError, match="namespace"):
        register_home_assistant_core_mcp(
            Mock(),
            entry_id="fedora-entry",
            namespace=namespace,
        )


def test_provider_requires_an_existing_connection_id() -> None:
    with pytest.raises(ValueError, match="entry ID"):
        register_home_assistant_core_mcp(
            Mock(),
            entry_id="",
            namespace="fedora",
        )
