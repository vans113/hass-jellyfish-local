"""Diagnostics download for JellyFish Local."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import JellyFishConfigEntry

TO_REDACT = {CONF_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: JellyFishConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = asdict(coordinator.client.data)
    data.pop("pattern_configs", None)  # large and not useful for debugging
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "connected": coordinator.client.connected,
        "last_update_success": coordinator.last_update_success,
        "controller": data,
    }
