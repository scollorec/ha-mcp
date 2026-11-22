"""Generic remote MCP upstream registration for the embedded HA-MCP server.

The component imports this module from the embedded worker thread only. It has
no module-level dependency on ha-mcp, so config-flow and HA startup imports stay
lightweight.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_NAMESPACE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}\\Z")


def remote_mcp_config_error(namespace: str, url: str) -> str | None:
    """Return a field error for an invalid optional remote MCP upstream."""
    if not url:
        return None
    if not _NAMESPACE_RE.fullmatch(namespace):
        return "invalid_remote_mcp_namespace"
    try:
        parsed = urlsplit(url)
        # Accessing port validates malformed port strings and IPv6 brackets.
        _ = parsed.port
    except ValueError:
        return "invalid_remote_mcp_url"
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return "invalid_remote_mcp_url"
    return None


def register_remote_mcp_upstream(
    mcp: Any,
    *,
    namespace: str,
    url: str,
    bearer_token: str = "",
) -> None:
    """Attach one remote Streamable HTTP MCP server under a tool namespace.

    Registration is lazy: no network request is made while HA-MCP starts. The
    provider opens an upstream MCP session only when a client lists or invokes
    its components. Upstream transport errors are therefore isolated to those
    proxied operations and do not take down HA-MCP's own server.
    """
    error = remote_mcp_config_error(namespace, url)
    if error:
        raise ValueError(error)

    # Deliberately imported here, in the already-running embedded worker. The
    # vendored FastMCP classes must match the ha-mcp package that built server.mcp.
    from ha_mcp._vendor.fastmcp.client.transports import StreamableHttpTransport
    from ha_mcp._vendor.fastmcp.server.providers.proxy import ProxyClient, ProxyProvider

    headers = (
        {"Authorization": f"Bearer {bearer_token.strip()}"}
        if bearer_token and bearer_token.strip()
        else None
    )

    def client_factory() -> ProxyClient:
        transport = StreamableHttpTransport(url, headers=headers)
        # Never relay HA-MCP caller authorization to a different upstream. Only
        # the explicitly configured bearer token above is sent to this server.
        return ProxyClient(transport, forward_incoming_headers=False)

    mcp.add_provider(ProxyProvider(client_factory, cache_ttl=30.0), namespace=namespace)
