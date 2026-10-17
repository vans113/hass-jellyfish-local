"""Websocket API used by the JellyFish card."""

from __future__ import annotations

import contextlib
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
import voluptuous as vol

from .client import JellyFishError, running_label
from .const import DOMAIN
from .patterns import DIRECTIONS, EFFECTS_BETWEEN, MAX_COLORS, PATTERN_TYPES


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_catalog)


def _coordinator_for(hass: HomeAssistant, entity_id: str):
    entry_id = None
    if reg_entry := er.async_get(hass).async_get(entity_id):
        entry_id = reg_entry.config_entry_id
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        if entry_id is None or entry.entry_id == entry_id:
            return entry.runtime_data
    return None


@websocket_api.websocket_command(
    {
        vol.Required("type"): "jellyfish_local/catalog",
        vol.Required("entity_id"): str,
        vol.Optional("with_colors", default=True): bool,
    }
)
@websocket_api.async_response
async def ws_catalog(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Pattern catalog (grouped by folder, with colors) for the controller of an entity."""
    coordinator = _coordinator_for(hass, msg["entity_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No JellyFish controller for that entity")
        return
    client = coordinator.client
    if msg["with_colors"]:
        # On failure send what we have; swatches fill in on the next request.
        with contextlib.suppress(JellyFishError):
            await client.get_pattern_configs()
    data = client.data
    folders: dict[str, list[dict[str, Any]]] = {}
    for key in data.patterns:
        folder, _, name = key.rpartition("/")
        cfg = data.pattern_configs.get(key) or {}
        folders.setdefault(folder, []).append(
            {
                "key": key,
                "name": name,
                "type": cfg.get("type"),
                "colors": cfg.get("colors") or [],
            }
        )
    connection.send_result(
        msg["id"],
        {
            "folders": [{"name": f, "patterns": p} for f, p in folders.items()],
            "zones": {
                zone: {"running": running_label(data.zone_states.get(zone), data.patterns)}
                for zone in data.zones
            },
            "types": [t for t in PATTERN_TYPES],
            "directions": DIRECTIONS,
            "effects_between": EFFECTS_BETWEEN,
            "max_colors": MAX_COLORS,
        },
    )
