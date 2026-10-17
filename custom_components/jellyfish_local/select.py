"""Pattern picker per zone."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JellyFishConfigEntry
from .client import running_label
from .coordinator import JellyFishCoordinator
from .entity import JellyFishZoneEntity
from .patterns import DIRECTIONS, PATTERN_TYPES

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
            entities: list[SelectEntity] = []
            for z in zones:
                entities += [
                    JellyFishPatternSelect(coordinator, z),
                    JellyFishTweakSelect(
                        coordinator, z, "effect_type", "type", "pattern_type", PATTERN_TYPES
                    ),
                    JellyFishTweakSelect(
                        coordinator, z, "direction", "direction", "direction", DIRECTIONS
                    ),
                ]
            async_add_entities(entities)

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


class JellyFishTweakSelect(JellyFishZoneEntity, SelectEntity):
    """Change one setting of whatever is running (like the app's edit screen)."""

    def __init__(
        self,
        coordinator: JellyFishCoordinator,
        zone: str,
        key: str,
        config_key: str,
        change: str,
        options: list[str],
    ) -> None:
        super().__init__(coordinator, zone, key)
        self._attr_translation_key = key
        self._attr_translation_placeholders = {"zone": zone}
        self._attr_options = list(options)
        self._config_key = config_key
        self._change = change

    @property
    def current_option(self) -> str | None:
        state = self.zone_state
        if not state or not state.is_on:
            return None
        value = self.running_config.get(self._config_key)
        return value if value in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        await self._command(self.coordinator.client.adjust([self.zone], **{self._change: option}))
