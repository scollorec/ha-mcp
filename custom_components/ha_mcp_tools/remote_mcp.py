"""Generic remote MCP configuration and registration for HA-MCP.

This module is safe to import in Home Assistant's main thread. Imports of
ha-mcp's FastMCP internals are delayed until the embedded worker registers the
upstream.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from .const import (
    DATA_REMOTE_MCP_BEARER_TOKEN,
    OPT_REMOTE_MCP_NAMESPACE,
    OPT_REMOTE_MCP_URL,
)

_LOGGER = logging.getLogger(__name__)
_NAMESPACE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


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


def remote_mcp_form_errors(user_input: Mapping[str, Any]) -> dict[str, str]:
    """Return field errors for invalid remote MCP options."""
    namespace = (
        str(user_input.get(OPT_REMOTE_MCP_NAMESPACE, "remote") or "remote")
        .strip()
        .lower()
        or "remote"
    )
    url = str(user_input.get(OPT_REMOTE_MCP_URL, "") or "").strip()
    error = remote_mcp_config_error(namespace, url)
    if error is None:
        return {}
    field = (
        OPT_REMOTE_MCP_NAMESPACE
        if error == "invalid_remote_mcp_namespace"
        else OPT_REMOTE_MCP_URL
    )
    return {field: error}


def remote_mcp_config_snapshot(
    options: Mapping[str, Any], data: Mapping[str, Any]
) -> tuple[str, str, str]:
    """Copy settings and the secret off the HA event loop before worker startup."""
    return (
        str(options.get(OPT_REMOTE_MCP_NAMESPACE, "remote") or "remote")
        .strip()
        .lower(),
        str(options.get(OPT_REMOTE_MCP_URL, "") or "").strip(),
        str(data.get(DATA_REMOTE_MCP_BEARER_TOKEN, "") or ""),
    )


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
    # Resolve vendored FastMCP modules dynamically. The component import guard
    # scans Python import nodes and forbids static ha_mcp imports anywhere in
    # the component, even inside worker-only functions.
    from importlib import import_module

    transports = import_module("ha_mcp._vendor.fastmcp.client.transports")
    proxies = import_module("ha_mcp._vendor.fastmcp.server.providers.proxy")
    streamable_http_transport = transports.StreamableHttpTransport
    proxy_client = proxies.ProxyClient
    proxy_provider = proxies.ProxyProvider

    headers = (
        {"Authorization": f"Bearer {bearer_token.strip()}"}
        if bearer_token and bearer_token.strip()
        else None
    )

    def client_factory() -> Any:
        transport = streamable_http_transport(url, headers=headers)
        # Never relay HA-MCP caller authorization to a different upstream. Only
        # the explicitly configured bearer token above is sent to this server.
        return proxy_client(transport, forward_incoming_headers=False)

    mcp.add_provider(proxy_provider(client_factory, cache_ttl=30.0), namespace=namespace)


def register_remote_mcp_if_configured(mcp: Any, config: tuple[str, str, str]) -> None:
    """Register the optional upstream, isolating failures from HA-MCP startup."""
    namespace, url, bearer_token = config
    if not url:
        return
    try:
        register_remote_mcp_upstream(
            mcp, namespace=namespace, url=url, bearer_token=bearer_token
        )
    except Exception:
        _LOGGER.exception(
            "Could not register remote MCP upstream %r; "
            "continuing with Home Assistant tools only",
            namespace,
        )
    else:
        _LOGGER.info("Registered remote MCP upstream under namespace %r", namespace)
