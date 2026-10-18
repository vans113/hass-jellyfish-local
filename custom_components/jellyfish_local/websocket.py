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
    websocket_api.async_register_command(hass, ws_set_favorites)
    websocket_api.async_register_command(hass, ws_delete_pattern)


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
                "read_only": key in data.read_only,
                "deletable": client.is_deletable(key),
            }
        )
    favorites = list(coordinator.config_entry.options.get("favorites", []))
    connection.send_result(
        msg["id"],
        {
            "folders": [{"name": f, "patterns": p} for f, p in folders.items()],
            "favorites": favorites,
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): "jellyfish_local/set_favorites",
        vol.Required("entity_id"): str,
        vol.Required("favorites"): [str],
    }
)
@websocket_api.async_response
async def ws_set_favorites(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Save the card's favorite pattern keys for a controller."""
    coordinator = _coordinator_for(hass, msg["entity_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No JellyFish controller for that entity")
        return
    # Keep only real patterns, de-duplicated, order preserved, capped.
    known = set(coordinator.client.data.patterns)
    seen: set[str] = set()
    favorites = [k for k in msg["favorites"] if k in known and not (k in seen or seen.add(k))][:60]
    entry = coordinator.config_entry
    hass.config_entries.async_update_entry(entry, options={**entry.options, "favorites": favorites})
    connection.send_result(msg["id"], {"favorites": favorites})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "jellyfish_local/delete_pattern",
        vol.Required("entity_id"): str,
        vol.Required("pattern"): str,
    }
)
@websocket_api.async_response
async def ws_delete_pattern(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete a saved pattern from the controller (the card's hold-to-delete)."""
    coordinator = _coordinator_for(hass, msg["entity_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No JellyFish controller for that entity")
        return
    try:
        favorites = await coordinator.async_delete_pattern(msg["pattern"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_pattern", str(err))
        return
    except JellyFishError as err:
        connection.send_error(msg["id"], "controller_error", str(err))
        return
    connection.send_result(msg["id"], {"deleted": msg["pattern"], "favorites": favorites})
