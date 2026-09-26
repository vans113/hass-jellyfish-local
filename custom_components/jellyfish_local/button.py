"""Button to re-read zones and patterns from the controller."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JellyFishConfigEntry
from .entity import JellyFishEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyFishConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([JellyFishRefreshButton(entry.runtime_data, "refresh")])


class JellyFishRefreshButton(JellyFishEntity, ButtonEntity):
    """Pick up new patterns or zones made in the JellyFish app."""

    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        await self._command(self.coordinator.async_refresh_metadata())
