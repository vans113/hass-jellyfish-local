"""Base entities for JellyFish Local."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import JellyFishError, ZoneState
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import JellyFishCoordinator

T = TypeVar("T")


class JellyFishEntity(CoordinatorEntity[JellyFishCoordinator]):
    """Anything that belongs to the controller device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: JellyFishCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_{key}"
        data = coordinator.client.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.unique_id)},
            name=data.name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=data.firmware,
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.client.connected

    async def _command(self, action: Awaitable[T]) -> T:
        """Run a client call, translate errors, and publish the new state."""
        try:
            result = await action
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except JellyFishError as err:
            raise HomeAssistantError(f"JellyFish controller error: {err}") from err
        self.coordinator.async_set_updated_data(self.coordinator.client.data)
        return result


class JellyFishZoneEntity(JellyFishEntity):
    """An entity tied to one zone."""

    def __init__(self, coordinator: JellyFishCoordinator, zone: str, key: str) -> None:
        super().__init__(coordinator, f"zone_{zone}_{key}" if key else f"zone_{zone}")
        self.zone = zone

    @property
    def zone_state(self) -> ZoneState | None:
        return self.coordinator.client.data.zone_states.get(self.zone)

    @property
    def available(self) -> bool:
        data = self.coordinator.client.data
        return super().available and self.zone in data.zones and self.zone in data.zone_states
