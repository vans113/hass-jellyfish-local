"""Pattern picker per zone."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JellyFishConfigEntry
from .client import running_label
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
    def _running(self) -> str | None:
        return running_label(self.zone_state, self.coordinator.client.data.patterns)

    @property
    def options(self) -> list[str]:
        patterns = list(self.coordinator.client.data.patterns)
        running = self._running
        if running and running not in patterns:
            # Something made in the JellyFish app; show it instead of "unknown".
            return [running, *patterns]
        return patterns

    @property
    def current_option(self) -> str | None:
        return self._running

    async def async_select_option(self, option: str) -> None:
        if option == self._running:
            return  # already playing
        await self._command(self.coordinator.client.apply_pattern(option, [self.zone]))
