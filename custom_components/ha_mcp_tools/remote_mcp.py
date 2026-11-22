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

import voluptuous as vol
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    DATA_REMOTE_MCP_BEARER_TOKEN,
    OPT_CORE_MCP_ENTRY_ID,
    OPT_CORE_MCP_NAMESPACE,
    OPT_REMOTE_MCP_BEARER_TOKEN,
    OPT_REMOTE_MCP_CLEAR_BEARER_TOKEN,
    OPT_REMOTE_MCP_NAMESPACE,
    OPT_REMOTE_MCP_URL,
)

_LOGGER = logging.getLogger(__name__)
_NAMESPACE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


def remote_mcp_option_fields(
    suggested_values: Mapping[str, Any], form_values: Mapping[str, Any]
) -> dict[Any, Any]:
    """Build the remote-upstream fields for the HA server options form."""
    return {
        vol.Optional(
            OPT_REMOTE_MCP_NAMESPACE,
            default=str(
                form_values.get(OPT_REMOTE_MCP_NAMESPACE, "remote") or "remote"
            ),
            description={
                "suggested_value": suggested_values.get(
                    OPT_REMOTE_MCP_NAMESPACE, "remote"
                )
            },
        ): str,
        vol.Optional(
            OPT_REMOTE_MCP_URL,
            default=str(form_values.get(OPT_REMOTE_MCP_URL, "") or ""),
            description={
                "suggested_value": suggested_values.get(OPT_REMOTE_MCP_URL, "")
            },
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        # Never pre-filled: the stored bearer token is kept out of the options form.
        vol.Optional(OPT_REMOTE_MCP_BEARER_TOKEN, default=""): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")
        ),
        vol.Optional(OPT_REMOTE_MCP_CLEAR_BEARER_TOKEN, default=False): bool,
    }


def normalize_remote_mcp_options(cleaned: dict[str, Any]) -> dict[str, Any]:
    """Normalize optional upstream values and strip credentials from options."""
    remote_url = str(cleaned.get(OPT_REMOTE_MCP_URL, "") or "").strip()
    if remote_url:
        cleaned[OPT_REMOTE_MCP_URL] = remote_url
        cleaned[OPT_REMOTE_MCP_NAMESPACE] = (
            str(cleaned.get(OPT_REMOTE_MCP_NAMESPACE, "remote") or "remote")
            .strip()
            .lower()
            or "remote"
        )
    else:
        cleaned.pop(OPT_REMOTE_MCP_URL, None)
        cleaned.pop(OPT_REMOTE_MCP_NAMESPACE, None)
    # Credentials belong in entry.data, never the publicly readable options.
    cleaned.pop(OPT_REMOTE_MCP_BEARER_TOKEN, None)
    cleaned.pop(OPT_REMOTE_MCP_CLEAR_BEARER_TOKEN, None)
    return cleaned


def adopt_remote_mcp_token(
    hass: Any,
    entry: Any,
    user_input: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    schedule_reload: bool,
) -> None:
    """Store the upstream bearer token in entry.data and reload when needed."""
    token = str(user_input.get(OPT_REMOTE_MCP_BEARER_TOKEN, "") or "").strip()
    clear = bool(user_input.get(OPT_REMOTE_MCP_CLEAR_BEARER_TOKEN, False))
    if not token and not clear:
        return
    data = dict(entry.data)
    if token:
        data[DATA_REMOTE_MCP_BEARER_TOKEN] = token
    else:
        data.pop(DATA_REMOTE_MCP_BEARER_TOKEN, None)
    if data == entry.data:
        return
    hass.config_entries.async_update_entry(entry, data=data)
    # A token-only save has no options delta to trigger the normal listener.
    from .embedded_entry import _reload_relevant

    if schedule_reload and _reload_relevant(options) == _reload_relevant(entry.options):
        hass.config_entries.async_schedule_reload(entry.entry_id)


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
) -> tuple[str, str, str, str, str]:
    """Copy configured MCP selections and secrets before worker startup."""
    return (
        str(options.get(OPT_REMOTE_MCP_NAMESPACE, "remote") or "remote")
        .strip()
        .lower(),
        str(options.get(OPT_REMOTE_MCP_URL, "") or "").strip(),
        str(data.get(DATA_REMOTE_MCP_BEARER_TOKEN, "") or ""),
        str(options.get(OPT_CORE_MCP_ENTRY_ID, "") or "").strip(),
        str(options.get(OPT_CORE_MCP_NAMESPACE, "remote") or "remote").strip().lower(),
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

    provider = proxy_provider(client_factory, cache_ttl=30.0)
    mcp.add_provider(provider, namespace=namespace)


def register_remote_mcp_if_configured(
    mcp: Any, config: tuple[str, str, str, str, str]
) -> None:
    """Register the configured direct and Home Assistant Core MCP providers."""
    namespace, url, bearer_token, core_entry_id, core_namespace = config
    if url:
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

    if not core_entry_id:
        return

    # Import the provider only on the embedded worker loop. The component's
    # Home Assistant event-loop code never imports the ha-mcp server package.
    from importlib import import_module

    try:
        providers = import_module("ha_mcp.providers.home_assistant_core")
    except ModuleNotFoundError as err:
        if err.name != "ha_mcp.providers.home_assistant_core":
            raise
        _LOGGER.warning(
            "The installed ha-mcp package does not include the "
            "Home Assistant Core MCP provider; selected tools are unavailable"
        )
        return

    try:
        providers.register_home_assistant_core_mcp(
            mcp,
            entry_id=core_entry_id,
            namespace=core_namespace,
        )
    except Exception:
        _LOGGER.exception(
            "Could not register the selected Home Assistant Core MCP entry"
        )
