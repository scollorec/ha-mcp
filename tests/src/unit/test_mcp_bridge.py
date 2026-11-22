"""Tests for the Home Assistant Core MCP WebSocket bridge."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import probatio
import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from custom_components.ha_mcp_tools.websocket_api.mcp_bridge import (
    _do_mcp_connections,
    _mcp_call_tool_prep,
    _mcp_list_tools_prep,
)


def _entry(coordinator: object, *, entry_id: str = "mcp-entry", state: str = "loaded"):
    return SimpleNamespace(
        entry_id=entry_id,
        domain="mcp",
        title="Fedora workstation",
        state=state,
        runtime_data=coordinator,
    )


def _install_entry(
    hass: HomeAssistant, entry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hass.config_entries,
        "async_get_entry",
        lambda entry_id: entry if entry_id == entry.entry_id else None,
    )


async def test_connections_list_contains_metadata_but_not_endpoint_or_secret(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = SimpleNamespace(
        server_info=SimpleNamespace(
            serverInfo=SimpleNamespace(name="fedora_workstation", version="1.30.0")
        ),
        data=[object(), object()],
    )
    entry = _entry(coordinator)
    monkeypatch.setattr(
        hass.config_entries,
        "async_entries",
        lambda domain: [entry] if domain == "mcp" else [],
    )

    result = _do_mcp_connections(hass, {})

    assert result == {
        "connections": [
            {
                "entry_id": "mcp-entry",
                "title": "Fedora workstation",
                "state": "loaded",
                "server": {"name": "fedora_workstation", "version": "1.30.0"},
                "tool_count": 2,
            }
        ]
    }
    serialized = repr(result).lower()
    assert "url" not in serialized
    assert "token" not in serialized


async def test_list_tools_refreshes_core_and_serializes_schema_and_annotations(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = SimpleNamespace(
        name="system_info",
        title=None,
        description="Report operating system and Python runtime.",
        parameters=probatio.Schema({}),
        annotations=llm.ToolAnnotations(
            read_only=True,
            destructive=False,
            idempotent=False,
            open_world=False,
        ),
    )
    coordinator = SimpleNamespace(
        data=[tool],
        server_info=SimpleNamespace(
            serverInfo=SimpleNamespace(name="fedora_workstation", version="1.30.0")
        ),
        async_refresh=AsyncMock(),
        last_update_success=True,
    )
    entry = _entry(coordinator)
    _install_entry(hass, entry, monkeypatch)

    result = await _mcp_list_tools_prep(hass, {"entry_id": "mcp-entry"})

    coordinator.async_refresh.assert_awaited_once()
    assert result["payload"]["entry_id"] == "mcp-entry"
    assert result["payload"]["server"] == {
        "name": "fedora_workstation",
        "version": "1.30.0",
    }
    assert result["payload"]["tools"] == [
        {
            "name": "system_info",
            "title": None,
            "description": "Report operating system and Python runtime.",
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
    ]


async def test_list_tools_fails_closed_when_refresh_fails(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = SimpleNamespace(
        data=[],
        async_refresh=AsyncMock(),
        last_update_success=False,
    )
    entry = _entry(coordinator)
    _install_entry(hass, entry, monkeypatch)

    with pytest.raises(HomeAssistantError, match="discovery failed"):
        await _mcp_list_tools_prep(hass, {"entry_id": "mcp-entry"})


async def test_call_tool_uses_core_wrapper_and_validated_arguments(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = SimpleNamespace(
        name="inspect_command",
        parameters=probatio.Schema(
            {
                probatio.Required("executable"): str,
                probatio.Optional("profile", default=None): probatio.Any(str, None),
            }
        ),
        async_call=AsyncMock(
            return_value=llm.ToolResult(
                data={"content": [{"type": "text", "text": "ok"}]}
            )
        ),
    )
    coordinator = SimpleNamespace(
        data=[tool],
        async_refresh=AsyncMock(),
        last_update_success=True,
    )
    entry = _entry(coordinator)
    _install_entry(hass, entry, monkeypatch)
    caller_context = Context()

    result = await _mcp_call_tool_prep(
        hass,
        {
            "entry_id": "mcp-entry",
            "tool_name": "inspect_command",
            "tool_args": {"executable": "/usr/bin/python"},
            "ha_mcp_context": caller_context,
        },
    )

    coordinator.async_refresh.assert_awaited_once()
    assert result == {
        "entry_id": "mcp-entry",
        "tool_name": "inspect_command",
        "result": {"content": [{"type": "text", "text": "ok"}]},
        "is_error": False,
    }
    called_input = tool.async_call.await_args.args[1]
    called_context = tool.async_call.await_args.args[2]
    assert called_input.tool_name == "inspect_command"
    assert called_input.tool_args == {
        "executable": "/usr/bin/python",
        "profile": None,
    }
    assert called_input.external is True
    assert called_context.context is caller_context


async def test_call_tool_rejects_invalid_arguments_before_upstream_call(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = SimpleNamespace(
        name="write_file",
        parameters=probatio.Schema(
            {probatio.Required("path"): str, probatio.Required("content"): str}
        ),
        async_call=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        data=[tool],
        async_refresh=AsyncMock(),
        last_update_success=True,
    )
    entry = _entry(coordinator)
    _install_entry(hass, entry, monkeypatch)

    with pytest.raises(HomeAssistantError, match="Arguments do not match"):
        await _mcp_call_tool_prep(
            hass,
            {
                "entry_id": "mcp-entry",
                "tool_name": "write_file",
                "tool_args": {"path": "/tmp/test"},
            },
        )

    tool.async_call.assert_not_awaited()


async def test_connection_must_be_a_loaded_core_mcp_entry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = SimpleNamespace(
        data=[],
        async_refresh=AsyncMock(),
        last_update_success=True,
    )
    entry = _entry(coordinator, state="not_loaded")
    _install_entry(hass, entry, monkeypatch)

    with pytest.raises(HomeAssistantError, match="not loaded"):
        await _mcp_list_tools_prep(hass, {"entry_id": "mcp-entry"})

    entry.domain = "not_mcp"
    with pytest.raises(HomeAssistantError, match="not found"):
        await _mcp_list_tools_prep(hass, {"entry_id": "mcp-entry"})
