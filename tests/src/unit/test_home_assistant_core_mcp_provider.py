"""Tests for the Home Assistant Core MCP FastMCP provider."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ha_mcp._vendor.fastmcp.tools.base import ToolResult
from ha_mcp.providers import home_assistant_core as core_mcp


def _tool_record() -> dict[str, object]:
    return {
        "name": "system_info",
        "title": "System information",
        "description": "Read Fedora workstation runtime details.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }


def _install_ws_client(monkeypatch: pytest.MonkeyPatch, client: object) -> AsyncMock:
    manager = SimpleNamespace(get_client=AsyncMock(return_value=client))
    monkeypatch.setattr(core_mcp, "WebSocketManager", lambda: manager)
    return manager.get_client


def test_provider_registers_one_existing_core_entry_under_a_namespace() -> None:
    mcp = Mock()

    provider = core_mcp.register_home_assistant_core_mcp(
        mcp,
        entry_id="fedora-entry",
        namespace="fedora_workstation",
    )

    assert isinstance(provider, core_mcp.HomeAssistantCoreMCPProvider)
    mcp.add_provider.assert_called_once_with(provider, namespace="fedora_workstation")


@pytest.mark.parametrize("namespace", ["", "Fedora", "bad namespace", "1bad", "x" * 33])
def test_provider_rejects_invalid_namespace(namespace: str) -> None:
    with pytest.raises(ValueError, match="namespace"):
        core_mcp.register_home_assistant_core_mcp(
            Mock(),
            entry_id="fedora-entry",
            namespace=namespace,
        )


async def test_provider_discovers_tools_through_the_ha_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(
        send_command=AsyncMock(
            return_value={"success": True, "result": {"tools": [_tool_record()]}}
        )
    )
    get_client = _install_ws_client(monkeypatch, client)
    provider = core_mcp.HomeAssistantCoreMCPProvider("fedora-entry")

    tools = await provider._list_tools()

    get_client.assert_awaited_once_with()
    client.send_command.assert_awaited_once_with(
        "ha_mcp_tools/mcp_list_tools",
        entry_id="fedora-entry",
        _wait_timeout=45.0,
    )
    assert len(tools) == 1
    assert tools[0].name == "system_info"
    assert tools[0].title == "System information"
    assert tools[0].parameters == _tool_record()["input_schema"]
    assert tools[0].annotations.read_only_hint is True
    assert tools[0].annotations.destructive_hint is False


async def test_forwarded_tool_calls_use_the_selected_entry_and_preserve_mcp_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_result = {
        "content": [{"type": "text", "text": "Fedora 44"}],
        "isError": False,
    }
    client = SimpleNamespace(
        send_command=AsyncMock(
            return_value={
                "success": True,
                "result": {
                    "entry_id": "fedora-entry",
                    "tool_name": "system_info",
                    "result": raw_result,
                    "is_error": False,
                },
            }
        )
    )
    _install_ws_client(monkeypatch, client)
    tool = core_mcp.HomeAssistantCoreMCPTool(
        entry_id="fedora-entry",
        backend_name="system_info",
        name="system_info",
        title="System information",
        parameters={"type": "object", "properties": {}},
    )

    result = await tool.run({})

    client.send_command.assert_awaited_once_with(
        "ha_mcp_tools/mcp_call_tool",
        entry_id="fedora-entry",
        tool_name="system_info",
        tool_args={},
        _wait_timeout=45.0,
    )
    assert isinstance(result, ToolResult)
    assert result.content[0].text == "Fedora 44"
    assert result.is_error is False


async def test_forwarded_tool_call_rejects_invalid_bridge_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(
        send_command=AsyncMock(
            return_value={"success": True, "result": {"result": {"unexpected": True}}}
        )
    )
    _install_ws_client(monkeypatch, client)
    tool = core_mcp.HomeAssistantCoreMCPTool(
        entry_id="fedora-entry",
        backend_name="system_info",
        name="system_info",
        parameters={"type": "object", "properties": {}},
    )

    with pytest.raises(Exception, match="unsupported tool result"):
        await tool.run({})


async def test_provider_cache_is_reused_until_its_short_ttl_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(
        send_command=AsyncMock(
            return_value={"success": True, "result": {"tools": [_tool_record()]}}
        )
    )
    _install_ws_client(monkeypatch, client)
    provider = core_mcp.HomeAssistantCoreMCPProvider(
        "fedora-entry",
        cache_ttl=60,
    )

    first = await provider._list_tools()
    second = await provider._get_tool("system_info")

    assert second is first[0]
    client.send_command.assert_awaited_once()
