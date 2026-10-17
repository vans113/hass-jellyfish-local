"""Lights: one per zone, plus an 'All zones' light when there's more than one."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    EFFECT_OFF,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
import voluptuous as vol

from . import JellyFishConfigEntry
from .client import ZoneState, running_label
from .const import ATTR_BRIGHTNESS_PCT, ATTR_COLORS, SERVICE_APPLY_LIGHT_STRING
from .coordinator import JellyFishCoordinator
from .entity import JellyFishEntity, JellyFishZoneEntity

PARALLEL_UPDATES = 1  # one socket; send commands one at a time

RGB_SCHEMA = vol.All(vol.ExactSequence((cv.byte, cv.byte, cv.byte)), vol.Coerce(tuple))
LIGHT_STRING_SCHEMA = {
    vol.Required(ATTR_COLORS): vol.All(cv.ensure_list, [RGB_SCHEMA], vol.Length(min=1)),
    vol.Optional(ATTR_BRIGHTNESS_PCT, default=100): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=100)
    ),
}


def to_percent(value: int) -> int:
    """HA brightness 1-255 -> JellyFish 1-100 (never rounds a lit light to 0)."""
    return max(1, min(100, round(value * 100 / 255)))


def to_ha(percent: int) -> int:
    """JellyFish 0-100 -> HA 0-255."""
    return max(0, min(255, round(percent * 255 / 100)))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyFishConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    added: set[str] = set()
    all_zones_added = False

    @callback
    def add_new_zones() -> None:
        nonlocal all_zones_added
        zones = list(coordinator.client.data.zones)
        new = [JellyFishZoneLight(coordinator, z) for z in zones if z not in added]
        added.update(zones)
        if len(zones) > 1 and not all_zones_added:
            new.append(JellyFishAllZonesLight(coordinator))
            all_zones_added = True
        if new:
            async_add_entities(new)

    add_new_zones()
    entry.async_on_unload(coordinator.async_add_listener(add_new_zones))

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_APPLY_LIGHT_STRING, LIGHT_STRING_SCHEMA, "async_apply_light_string"
    )


class _JellyFishLight(LightEntity):
    """Shared light behaviour for one or more zones."""

    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    _attr_supported_features = LightEntityFeature.EFFECT

    coordinator: JellyFishCoordinator

    @property
    def _zones(self) -> list[str]:
        raise NotImplementedError

    @property
    def _states(self) -> list[ZoneState]:
        states = self.coordinator.client.data.zone_states
        return [states[z] for z in self._zones if z in states]

    @property
    def is_on(self) -> bool:
        return any(s.is_on for s in self._states)

    @property
    def brightness(self) -> int | None:
        values = [s.brightness for s in self._states if s.is_on and s.brightness is not None]
        return to_ha(max(values)) if values else None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        colors = {s.rgb for s in self._states if s.is_on}
        if len(colors) == 1:
            return colors.pop()
        return None

    @property
    def _running(self) -> set[str]:
        patterns = self.coordinator.client.data.patterns
        return {label for s in self._states if (label := running_label(s, patterns))}

    @property
    def effect(self) -> str | None:
        running = self._running
        if len(running) == 1 and len({s.file for s in self._states if s.is_on}) == 1:
            return running.pop()
        return EFFECT_OFF if self.is_on else None

    @property
    def effect_list(self) -> list[str]:
        patterns = self.coordinator.client.data.patterns
        custom = sorted(r for r in self._running if r not in patterns)
        return [EFFECT_OFF, *custom, *patterns]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        states = self._states
        if len(states) != 1:
            return {}
        attrs: dict[str, Any] = {
            "pattern_type": states[0].pattern_type,
            "activated_by": states[0].activated_by,
        }
        if len(self._zones) == 1:
            zone = self.coordinator.client.data.zones.get(self._zones[0]) or {}
            attrs["pixels"] = zone.get("numPixels")
            attrs["outputs"] = [
                {
                    "port": pm.get("phyPort"),
                    "pixels": abs(pm.get("phyEndIdx", 0) - pm.get("phyStartIdx", 0)) + 1,
                }
                for pm in zone.get("portMap") or []
            ]
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        client = self.coordinator.client
        zones = self._zones
        pct = to_percent(kwargs[ATTR_BRIGHTNESS]) if ATTR_BRIGHTNESS in kwargs else None
        effect = kwargs.get(ATTR_EFFECT)
        rgb = kwargs.get(ATTR_RGB_COLOR)

        if effect and effect != EFFECT_OFF and effect not in client.data.patterns:
            # An app-made custom pattern can't be re-sent by name; it can only
            # be the one already playing, so just resume/adjust it.
            if effect not in self._running:
                raise ServiceValidationError(f"Unknown pattern: {effect}")
            if pct is not None:
                await self._command(client.set_brightness(pct, zones))
            elif not self.is_on:
                await self._command(client.turn_on(zones))
        elif effect and effect != EFFECT_OFF:
            await self._command(client.apply_pattern(effect, zones, brightness=pct))
        elif rgb is not None:
            current = self.brightness
            level = pct or (to_percent(current) if current else 100)
            await self._command(client.apply_color(tuple(rgb), level, zones))
        elif effect == EFFECT_OFF:
            # "no effect": show the current (or warm white) color as a solid
            color = self.rgb_color or (255, 180, 110)
            current = self.brightness
            level = pct or (to_percent(current) if current else 100)
            await self._command(client.apply_color(color, level, zones))
        elif pct is not None:
            await self._command(client.set_brightness(pct, zones))
        else:
            await self._command(client.turn_on(zones))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(self.coordinator.client.turn_off(self._zones))

    async def async_apply_light_string(
        self, colors: list[tuple[int, int, int]], brightness_pct: int = 100
    ) -> None:
        await self._command(
            self.coordinator.client.apply_light_string(list(colors), brightness_pct, self._zones)
        )


class JellyFishZoneLight(_JellyFishLight, JellyFishZoneEntity):
    """One zone of lights."""

    _attr_translation_key = None

    def __init__(self, coordinator: JellyFishCoordinator, zone: str) -> None:
        super().__init__(coordinator, zone, "")
        self._attr_name = zone

    @property
    def _zones(self) -> list[str]:
        return [self.zone]


class JellyFishAllZonesLight(_JellyFishLight, JellyFishEntity):
    """Every zone on the controller at once."""

    _attr_translation_key = "all_zones"

    def __init__(self, coordinator: JellyFishCoordinator) -> None:
        super().__init__(coordinator, "all_zones")

    @property
    def _zones(self) -> list[str]:
        return list(self.coordinator.client.data.zones)
