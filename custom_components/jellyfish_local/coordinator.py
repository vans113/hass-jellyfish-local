"""Coordinator: owns the controller connection and fans updates out to entities."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import ControllerData, JellyFishClient, JellyFishError
from .const import DOMAIN, LOGGER, METADATA_INTERVAL, POLL_INTERVAL

if TYPE_CHECKING:
    from . import JellyFishConfigEntry


class JellyFishCoordinator(DataUpdateCoordinator[ControllerData]):
    """Keeps one live connection and pushes controller updates to entities."""

    config_entry: JellyFishConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: JellyFishConfigEntry, client: JellyFishClient
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}",
            update_interval=POLL_INTERVAL,
            always_update=True,
        )
        self.client = client
        self._updating = False
        self._last_metadata = 0.0
        self._was_connected = False
        self._known_zones: set[str] = set()
        self._paused = False
        self._remove_listener = client.add_listener(self._handle_client_event)

    @property
    def unique_id(self) -> str:
        return self.config_entry.unique_id or self.client.host

    # ------------------------------------------------------------------ #
    async def async_pause(self) -> None:
        """Release the controller's single websocket slot and stop reconnecting.

        Used while a config flow needs to talk to the controller itself.
        """
        self._paused = True
        await self.client.disconnect()

    async def async_resume(self) -> None:
        self._paused = False
        await self.async_request_refresh()

    async def _async_update_data(self) -> ControllerData:
        if self._paused:
            raise UpdateFailed("Paused while the controller is being reconfigured")
        self._updating = True
        try:
            reconnected = not self.client.connected
            await self.client.connect()
            stale = time.monotonic() - self._last_metadata > METADATA_INTERVAL.total_seconds()
            if reconnected or stale:
                await self.async_refresh_metadata(notify=False)
            else:
                await self.client.refresh_zone_states()
        except JellyFishError as err:
            # A socket that stopped answering blocks the controller's only slot;
            # drop it so the next attempt starts clean.
            await self.client.disconnect()
            raise UpdateFailed(str(err)) from err
        finally:
            self._updating = False
        self._was_connected = True
        return self.client.data

    async def async_refresh_metadata(self, notify: bool = True) -> None:
        """Re-read name, firmware, zones, patterns and all zone states."""
        await self.client.refresh_all()
        self._last_metadata = time.monotonic()
        self._sync_device()
        self._sync_zones()
        if notify:
            self.async_set_updated_data(self.client.data)

    async def async_shutdown(self) -> None:
        """Close the websocket; the controller only allows one."""
        await super().async_shutdown()
        self._remove_listener()
        await self.client.disconnect()

    # ------------------------------------------------------------------ #
    @callback
    def _handle_client_event(self) -> None:
        """Push update or connection change from the client."""
        if self._updating:
            return  # the running refresh will publish the final result
        if self.client.connected:
            if self._was_connected:
                self.async_set_updated_data(self.client.data)
            return
        if self._was_connected:
            self._was_connected = False
            if self._paused:
                self.async_update_listeners()
                return
            LOGGER.info("JellyFish controller %s disconnected; reconnecting", self.client.host)
            self.async_update_listeners()  # entities go unavailable right away
            self.config_entry.async_create_background_task(
                self.hass, self.async_request_refresh(), "jellyfish_local reconnect"
            )

    @callback
    def _sync_device(self) -> None:
        """Keep name and firmware on the device page current."""
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.unique_id)})
        data = self.client.data
        if device and (device.sw_version != data.firmware):
            registry.async_update_device(device.id, sw_version=data.firmware)

    @callback
    def _sync_zones(self) -> None:
        """Remove entities for zones deleted on the controller."""
        zones = set(self.client.data.zones)
        removed = self._known_zones - zones
        self._known_zones = zones
        if not removed:
            return
        registry = er.async_get(self.hass)
        for entry in er.async_entries_for_config_entry(registry, self.config_entry.entry_id):
            for zone in removed:
                if (
                    entry.unique_id.startswith(f"{self.unique_id}_zone_{zone}_")
                    or entry.unique_id == f"{self.unique_id}_zone_{zone}"
                ):
                    LOGGER.info("Zone %s no longer exists; removing %s", zone, entry.entity_id)
                    registry.async_remove(entry.entity_id)
