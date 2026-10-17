"""JellyFish Local: local-only control of JellyFish Lighting controllers."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .client import JellyFishClient
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import JellyFishCoordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
]

type JellyFishConfigEntry = ConfigEntry[JellyFishCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Things shared by every controller: the card and its websocket API."""
    from . import websocket
    from .frontend import async_register_card

    websocket.async_register(hass)
    await async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: JellyFishConfigEntry) -> bool:
    """Connect to the controller and set up entities."""
    client = JellyFishClient(entry.data[CONF_HOST], async_get_clientsession(hass))
    coordinator = JellyFishCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_shutdown()
        raise
    entry.runtime_data = coordinator

    data = client.data
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.unique_id)},
        name=data.name or entry.title,
        manufacturer=MANUFACTURER,
        model=MODEL,
        sw_version=data.firmware,
    )

    async def _on_stop(_: Event) -> None:
        # Free the controller's single websocket slot before HA exits so it
        # accepts us straight away after a restart.
        await client.disconnect()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: JellyFishConfigEntry) -> bool:
    """Unload entities and close the websocket."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
