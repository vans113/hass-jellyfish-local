"""Connectivity sensor for the controller."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JellyFishConfigEntry
from .entity import JellyFishEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyFishConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([JellyFishConnectedSensor(entry.runtime_data, "connected")])


class JellyFishConnectedSensor(JellyFishEntity, BinarySensorEntity):
    """On while HA holds a live connection to the controller."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True  # reporting "disconnected" is the whole point

    @property
    def is_on(self) -> bool:
        return self.coordinator.client.connected
