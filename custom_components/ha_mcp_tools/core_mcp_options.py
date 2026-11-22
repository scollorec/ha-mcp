"""Options for reusing an existing Home Assistant Core MCP connection."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import OPT_CORE_MCP_ENTRY_ID, OPT_CORE_MCP_NAMESPACE

_NAMESPACE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


def _default_namespace(title: str) -> str:
    """Derive a valid, short namespace from a connection title."""
    namespace = re.sub(r"[^a-z0-9_]+", "_", title.lower()).strip("_")
    if not namespace or not namespace[0].isalpha():
        namespace = f"mcp_{namespace}"
    return namespace[:32].rstrip("_") or "mcp"


def core_mcp_option_fields(
    hass: HomeAssistant,
    suggested_values: Mapping[str, Any],
    form_values: Mapping[str, Any],
) -> dict[Any, Any]:
    """Build the selector for Core MCP config entries and their namespace."""
    entries = sorted(
        hass.config_entries.async_entries("mcp"),
        key=lambda entry: (entry.title.casefold(), entry.entry_id),
    )
    options = [SelectOptionDict(value="", label="Disabled")]
    known_ids: set[str] = set()
    for entry in entries:
        known_ids.add(entry.entry_id)
        state = getattr(entry.state, "value", entry.state)
        options.append(
            SelectOptionDict(
                value=entry.entry_id,
                label=f"{entry.title} ({state})",
            )
        )

    selected_entry_id = str(form_values.get(OPT_CORE_MCP_ENTRY_ID, "") or "").strip()
    if selected_entry_id and selected_entry_id not in known_ids:
        options.append(
            SelectOptionDict(
                value=selected_entry_id,
                label=f"Unavailable connection ({selected_entry_id})",
            )
        )

    selected_entry = next(
        (entry for entry in entries if entry.entry_id == selected_entry_id),
        None,
    )
    default_namespace = (
        _default_namespace(selected_entry.title)
        if selected_entry is not None
        else "remote"
    )
    namespace = (
        str(
            form_values.get(OPT_CORE_MCP_NAMESPACE, default_namespace)
            or default_namespace
        )
        .strip()
        .lower()
    )

    return {
        vol.Optional(
            OPT_CORE_MCP_ENTRY_ID,
            default=selected_entry_id,
        ): SelectSelector(
            SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
        ),
        vol.Optional(
            OPT_CORE_MCP_NAMESPACE,
            default=namespace,
            description={
                "suggested_value": suggested_values.get(
                    OPT_CORE_MCP_NAMESPACE, namespace
                )
            },
        ): str,
    }


def core_mcp_form_errors(
    hass: HomeAssistant, user_input: Mapping[str, Any]
) -> dict[str, str]:
    """Validate the selected Core MCP entry and tool namespace."""
    entry_id = str(user_input.get(OPT_CORE_MCP_ENTRY_ID, "") or "").strip()
    if not entry_id:
        return {}

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != "mcp":
        return {OPT_CORE_MCP_ENTRY_ID: "invalid_core_mcp_entry"}

    namespace = str(user_input.get(OPT_CORE_MCP_NAMESPACE, "remote") or "").strip()
    if not _NAMESPACE_RE.fullmatch(namespace.lower()):
        return {OPT_CORE_MCP_NAMESPACE: "invalid_core_mcp_namespace"}
    return {}


def normalize_core_mcp_options(cleaned: dict[str, Any]) -> dict[str, Any]:
    """Normalize the optional Core MCP connection settings."""
    entry_id = str(cleaned.get(OPT_CORE_MCP_ENTRY_ID, "") or "").strip()
    if not entry_id:
        cleaned.pop(OPT_CORE_MCP_ENTRY_ID, None)
        cleaned.pop(OPT_CORE_MCP_NAMESPACE, None)
        return cleaned

    namespace = str(cleaned.get(OPT_CORE_MCP_NAMESPACE, "remote") or "remote")
    cleaned[OPT_CORE_MCP_ENTRY_ID] = entry_id
    cleaned[OPT_CORE_MCP_NAMESPACE] = namespace.strip().lower()
    return cleaned


