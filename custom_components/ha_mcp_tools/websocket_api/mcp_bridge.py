"""Home Assistant Core MCP connection bridge for the embedded HA-MCP server.

The bridge deliberately accepts config-entry IDs, never endpoint URLs or
credentials. It operates on the existing Core MCP coordinator so endpoint
configuration, authentication and re-authentication remain owned by Core.
All functions in this module run on Home Assistant's event loop.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

_LOGGER = logging.getLogger(__name__)
_MCP_DOMAIN = "mcp"


def _entry_state(entry: Any) -> str:
    """Return a config entry's state as a stable string."""
    state = getattr(entry, "state", None)
    return str(getattr(state, "value", state))


def _server_identity(coordinator: Any) -> dict[str, str | None]:
    """Return non-secret server identity fields from Core's last initialize."""
    initialized = getattr(coordinator, "server_info", None)
    server = getattr(initialized, "serverInfo", None)
    if server is None:
        server = getattr(initialized, "server_info", None)
    return {
        "name": getattr(server, "name", None),
        "version": getattr(server, "version", None),
    }


def _connection_record(entry: Any) -> dict[str, Any]:
    """Return a connection summary without endpoint or credential data."""
    state = _entry_state(entry)
    coordinator = getattr(entry, "runtime_data", None)
    server = (
        _server_identity(coordinator)
        if coordinator is not None
        else {
            "name": None,
            "version": None,
        }
    )
    data = getattr(coordinator, "data", None) if coordinator is not None else None
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "state": state,
        "server": server,
        "tool_count": len(data) if state == "loaded" and data is not None else None,
    }


def _do_mcp_connections(hass: HomeAssistant, params: dict[str, Any]) -> dict[str, Any]:
    """List Core MCP config entries without exposing URLs or credentials."""
    entries = hass.config_entries.async_entries(_MCP_DOMAIN)
    return {"connections": [_connection_record(entry) for entry in entries]}


def _get_loaded_connection(hass: HomeAssistant, entry_id: str) -> tuple[Any, Any]:
    """Resolve one loaded Core MCP entry and its runtime coordinator."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or getattr(entry, "domain", None) != _MCP_DOMAIN:
        raise HomeAssistantError("Home Assistant MCP connection was not found")

    if _entry_state(entry) != "loaded":
        raise HomeAssistantError("Home Assistant MCP connection is not loaded")

    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None or not callable(getattr(coordinator, "async_refresh", None)):
        raise HomeAssistantError("Home Assistant MCP connection is not ready")

    return entry, coordinator


async def _refresh_connection(coordinator: Any) -> None:
    """Refresh Core's cached MCP tool list and fail closed on discovery errors."""
    try:
        await coordinator.async_refresh()
    except Exception as err:
        raise HomeAssistantError(
            "Could not refresh the Home Assistant MCP connection"
        ) from err

    if not getattr(coordinator, "last_update_success", False):
        raise HomeAssistantError(
            "Home Assistant MCP tool discovery failed; connection is unavailable"
        )

    if getattr(coordinator, "data", None) is None:
        raise HomeAssistantError("Home Assistant MCP connection returned no tools")


def _tool_record(tool: Any) -> dict[str, Any]:
    """Serialize one Core MCP tool while preserving schema and safety hints."""
    name = str(getattr(tool, "name", "") or "")
    if not name:
        raise HomeAssistantError("Home Assistant MCP returned a tool without a name")

    try:
        # Strict encoding is intentional: never publish a broadened schema.
        input_schema = probatio.to_json_schema(tool.parameters, strict=True)
    except Exception as err:
        _LOGGER.warning(
            "Refusing to expose MCP tool %r because its parameter schema "
            "cannot be represented safely",
            name,
        )
        raise HomeAssistantError(
            f"Cannot safely expose the parameter schema for MCP tool {name!r}"
        ) from err

    annotations = getattr(tool, "annotations", None)
    return {
        "name": name,
        "title": getattr(tool, "title", None),
        "description": getattr(tool, "description", None),
        "input_schema": input_schema,
        "annotations": {
            "readOnlyHint": bool(getattr(annotations, "read_only", False)),
            "destructiveHint": bool(getattr(annotations, "destructive", True)),
            "idempotentHint": bool(getattr(annotations, "idempotent", False)),
            "openWorldHint": bool(getattr(annotations, "open_world", True)),
        },
    }


async def _mcp_list_tools_prep(
    hass: HomeAssistant, msg: dict[str, Any]
) -> dict[str, Any]:
    """Refresh and serialize tools from an existing Core MCP connection."""
    entry, coordinator = _get_loaded_connection(hass, msg["entry_id"])
    await _refresh_connection(coordinator)

    tools = [_tool_record(tool) for tool in coordinator.data]
    identity = _server_identity(coordinator)
    return {
        "payload": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "server": identity,
            "tools": tools,
        }
    }


def _do_mcp_list_tools(
    hass: HomeAssistant, params: dict[str, Any], *, payload: dict[str, Any]
) -> dict[str, Any]:
    """Return the refreshed tool catalogue."""
    return payload


async def _mcp_call_tool_prep(
    hass: HomeAssistant, msg: dict[str, Any]
) -> dict[str, Any]:
    """Validate and invoke one tool through Core's existing MCP tool wrapper."""
    entry, coordinator = _get_loaded_connection(hass, msg["entry_id"])
    await _refresh_connection(coordinator)

    tool_name = msg["tool_name"]
    tool = next(
        (candidate for candidate in coordinator.data if candidate.name == tool_name),
        None,
    )
    if tool is None:
        raise HomeAssistantError(
            f"MCP tool {tool_name!r} is not available on this connection"
        )

    tool_args = msg.get("tool_args") or {}
    try:
        validated_args = tool.parameters(dict(tool_args))
    except Exception as err:
        # Do not echo rejected arguments: an argument can contain a secret.
        raise HomeAssistantError(
            f"Arguments do not match the schema for MCP tool {tool_name!r}"
        ) from err

    context = llm.LLMContext(
        platform="ha_mcp_tools",
        context=msg.get("ha_mcp_context"),
        language=getattr(hass.config, "language", None),
        assistant="ha_mcp_tools",
        device_id=None,
    )
    tool_input = llm.ToolInput(
        tool_name=tool.name,
        tool_args=validated_args,
        external=True,
    )

    try:
        result = await tool.async_call(hass, tool_input, context)
    except Exception as err:
        # Core's wrapper still performs token refresh and starts re-auth when
        # needed. Keep transport exceptions (which may contain endpoint details)
        # out of the WebSocket response.
        raise HomeAssistantError(
            f"Call to upstream MCP tool {tool_name!r} failed"
        ) from err

    if isinstance(result, llm.ToolResult):
        result_data = result.data
        is_error = bool(result.error)
    elif isinstance(result, Mapping):
        result_data = dict(result)
        is_error = bool(result_data.get("isError", False))
    else:
        raise HomeAssistantError("Upstream MCP returned an unsupported tool result")

    return {
        "entry_id": entry.entry_id,
        "tool_name": tool_name,
        "result": result_data,
        "is_error": is_error,
    }


def _do_mcp_call_tool(
    hass: HomeAssistant, params: dict[str, Any], *, payload: dict[str, Any]
) -> dict[str, Any]:
    """Return the upstream tool result prepared by Core's MCP wrapper."""
    return payload
