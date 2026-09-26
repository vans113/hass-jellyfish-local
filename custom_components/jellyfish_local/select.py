"""Pattern picker per zone."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JellyFishConfigEntry
from .coordinator import JellyFishCoordinator
from .entity import JellyFishZoneEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyFishConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def add_new_zones() -> None:
        zones = [z for z in coordinator.client.data.zones if z not in added]
        added.update(zones)
        if zones:
            async_add_entities(JellyFishPatternSelect(coordinator, z) for z in zones)

    add_new_zones()
    entry.async_on_unload(coordinator.async_add_listener(add_new_zones))


class JellyFishPatternSelect(JellyFishZoneEntity, SelectEntity):
    """Choose which saved pattern a zone plays."""

    _attr_translation_key = "pattern"

    def __init__(self, coordinator: JellyFishCoordinator, zone: str) -> None:
        super().__init__(coordinator, zone, "pattern")
        self._attr_translation_placeholders = {"zone": zone}

    @property
    def options(self) -> list[str]:
        return list(self.coordinator.client.data.patterns)

    @property
    def current_option(self) -> str | None:
        state = self.zone_state
        if state and state.is_on and state.file in self.options:
            return state.file
        return None

    async def async_select_option(self, option: str) -> None:
        await self._command(self.coordinator.client.apply_pattern(option, [self.zone]))
