"""Speed and spacing sliders for whatever a zone is running."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JellyFishConfigEntry
from .coordinator import JellyFishCoordinator
from .entity import JellyFishZoneEntity
from .patterns import SPEED_MAX, SPEED_MIN, wire_speed_to_ui

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
        entities: list[NumberEntity] = []
        for z in zones:
            entities += [JellyFishSpeed(coordinator, z), JellyFishSpacing(coordinator, z)]
        if entities:
            async_add_entities(entities)

    add_new_zones()
    entry.async_on_unload(coordinator.async_add_listener(add_new_zones))


class _Tweak(JellyFishZoneEntity, NumberEntity):
    _change: str

    def __init__(self, coordinator: JellyFishCoordinator, zone: str, key: str) -> None:
        super().__init__(coordinator, zone, key)
        self._attr_translation_key = key
        self._attr_translation_placeholders = {"zone": zone}

    async def async_set_native_value(self, value: float) -> None:
        await self._command(
            self.coordinator.client.adjust([self.zone], **{self._change: int(value)})
        )


class JellyFishSpeed(_Tweak):
    """1 = slowest, 100 = fastest (the controller counts the other way)."""

    _attr_native_min_value = SPEED_MIN
    _attr_native_max_value = SPEED_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _change = "speed"

    def __init__(self, coordinator: JellyFishCoordinator, zone: str) -> None:
        super().__init__(coordinator, zone, "speed")

    @property
    def native_value(self) -> float | None:
        state = self.zone_state
        if not state or not state.is_on:
            return None
        run = self.running_config.get("runData") or {}
        return wire_speed_to_ui(run.get("speed"))


class JellyFishSpacing(_Tweak):
    """Space between colour blocks (the app's 'space between pixels')."""

    _attr_native_min_value = 0
    _attr_native_max_value = 50
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _change = "spacing"

    def __init__(self, coordinator: JellyFishCoordinator, zone: str) -> None:
        super().__init__(coordinator, zone, "spacing")

    @property
    def native_value(self) -> float | None:
        state = self.zone_state
        if not state or not state.is_on:
            return None
        value = self.running_config.get("spaceBetweenPixels")
        return float(value) if isinstance(value, (int, float)) else None
