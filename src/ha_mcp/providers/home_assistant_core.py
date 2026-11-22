"""FastMCP provider for an existing Home Assistant Core MCP connection."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping
from typing import Any

from pydantic import PrivateAttr

from ha_mcp._vendor import mcp_types
from ha_mcp._vendor.fastmcp.exceptions import ToolError
from ha_mcp._vendor.fastmcp.server.providers.base import Provider
from ha_mcp._vendor.fastmcp.tools.base import Tool, ToolResult
from ha_mcp._vendor.fastmcp.utilities.tasks import TaskConfig
from ha_mcp._vendor.fastmcp.utilities.versions import VersionSpec
from ha_mcp.client.websocket_client import WebSocketManager

_LOGGER = logging.getLogger(__name__)

_MCP_LIST_TOOLS_COMMAND = "ha_mcp_tools/mcp_list_tools"
_MCP_CALL_TOOL_COMMAND = "ha_mcp_tools/mcp_call_tool"
_BRIDGE_COMMAND_TIMEOUT_SECONDS = 45.0
_TOOL_CACHE_TTL_SECONDS = 15.0
_NAMESPACE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


class HomeAssistantCoreMCPTool(Tool):
    """A FastMCP tool forwarded through Home Assistant's existing MCP client."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")
    _entry_id: str = PrivateAttr()
    _backend_name: str = PrivateAttr()

    def __init__(self, *, entry_id: str, backend_name: str, **kwargs: Any) -> None:
        """Bind the upstream config entry and native tool name."""
        super().__init__(**kwargs)
        self._entry_id = entry_id
        self._backend_name = backend_name

    async def run(
        self,
        arguments: dict[str, Any],
        context: Any | None = None,
    ) -> ToolResult:
        """Invoke the native tool through the HA-MCP component bridge."""
        try:
            client = await WebSocketManager().get_client()
            response = await client.send_command(
                _MCP_CALL_TOOL_COMMAND,
                entry_id=self._entry_id,
                tool_name=self._backend_name,
                tool_args=arguments,
                _wait_timeout=_BRIDGE_COMMAND_TIMEOUT_SECONDS,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Home Assistant Core MCP invocation failed for tool %r",
                self._backend_name,
                exc_info=True,
            )
            raise ToolError(
                f"Home Assistant Core MCP could not invoke {self._backend_name!r}"
            ) from err

        bridge_result = response.get("result")
        if not isinstance(bridge_result, Mapping):
            raise ToolError("Home Assistant Core MCP returned an invalid call result")

        raw_result = bridge_result.get("result")
        if not isinstance(raw_result, Mapping):
            raise ToolError("Home Assistant Core MCP returned an invalid tool payload")

        try:
            call_result = mcp_types.CallToolResult.model_validate(dict(raw_result))
        except Exception as err:  # noqa: BLE001
            raise ToolError(
                "Home Assistant Core MCP returned an unsupported tool result"
            ) from err

        return ToolResult.from_mcp_result(call_result)


class HomeAssistantCoreMCPProvider(Provider):
    """Dynamically expose tools from one selected Core MCP config entry."""

    def __init__(
        self,
        entry_id: str,
        *,
        cache_ttl: float = _TOOL_CACHE_TTL_SECONDS,
    ) -> None:
        """Initialize the provider with a Core config-entry identity only."""
        super().__init__()
        if not entry_id:
            raise ValueError("A Home Assistant Core MCP entry ID is required")
        self.entry_id = entry_id
        self.cache_ttl = cache_ttl
        self._cache: list[Tool] | None = None
        self._cache_time = 0.0
        self._cache_lock = asyncio.Lock()

    async def _fetch_tools(self) -> list[Tool]:
        """Refresh the selected Core connection's tool catalogue."""
        try:
            client = await WebSocketManager().get_client()
            response = await client.send_command(
                _MCP_LIST_TOOLS_COMMAND,
                entry_id=self.entry_id,
                _wait_timeout=_BRIDGE_COMMAND_TIMEOUT_SECONDS,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Could not discover tools from Home Assistant MCP entry %s",
                self.entry_id,
                exc_info=True,
            )
            raise ToolError(
                "Home Assistant Core MCP tool discovery is unavailable"
            ) from err

        payload = response.get("result")
        if not isinstance(payload, Mapping):
            raise ToolError("Home Assistant Core MCP returned an invalid tool catalogue")

        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, list):
            raise ToolError("Home Assistant Core MCP returned no valid tool list")

        tools: list[Tool] = []
        for record in raw_tools:
            if not isinstance(record, Mapping):
                raise ToolError("Home Assistant Core MCP returned an invalid tool record")

            name = record.get("name")
            input_schema = record.get("input_schema")
            if not isinstance(name, str) or not name:
                raise ToolError("Home Assistant Core MCP returned a tool without a name")
            if not isinstance(input_schema, dict):
                raise ToolError(
                    f"Home Assistant Core MCP returned an invalid schema for {name!r}"
                )

            try:
                annotations = mcp_types.ToolAnnotations.model_validate(
                    record.get("annotations") or {}
                )
                tools.append(
                    HomeAssistantCoreMCPTool(
                        entry_id=self.entry_id,
                        backend_name=name,
                        name=name,
                        title=record.get("title"),
                        description=record.get("description"),
                        parameters=input_schema,
                        annotations=annotations,
                    )
                )
            except Exception as err:  # noqa: BLE001
                raise ToolError(
                    f"Home Assistant Core MCP returned invalid metadata for {name!r}"
                ) from err

        return tools

    async def _get_cached_tools(self, *, force_refresh: bool = False) -> list[Tool]:
        """Return a bounded metadata cache, serializing concurrent refreshes."""
        async with self._cache_lock:
            is_fresh = (
                self._cache is not None
                and time.monotonic() - self._cache_time < self.cache_ttl
            )
            if is_fresh and not force_refresh:
                return list(self._cache or [])

            tools = await self._fetch_tools()
            self._cache = tools
            self._cache_time = time.monotonic()
            return list(tools)

    async def _list_tools(self) -> list[Tool]:
        """List tools for FastMCP discovery."""
        return await self._get_cached_tools()

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """Resolve a cached tool; the HA component revalidates it before calling."""
        tools = await self._get_cached_tools()
        for tool in tools:
            if tool.name == name and (
                version is None or version.matches(tool.version)
            ):
                return tool
        return None


def register_home_assistant_core_mcp(
    mcp: Any,
    *,
    entry_id: str,
    namespace: str,
) -> HomeAssistantCoreMCPProvider:
    """Register one selected Core MCP entry under a collision-safe namespace."""
    normalized_namespace = namespace.strip().lower()
    if not entry_id:
        raise ValueError("A Home Assistant Core MCP entry ID is required")
    if not _NAMESPACE_RE.fullmatch(normalized_namespace):
        raise ValueError("Invalid Home Assistant Core MCP namespace")

    provider = HomeAssistantCoreMCPProvider(entry_id)
    mcp.add_provider(provider, namespace=normalized_namespace)
    _LOGGER.info(
        "Registered Home Assistant Core MCP entry %s under namespace %r",
        entry_id,
        normalized_namespace,
    )
    return provider
