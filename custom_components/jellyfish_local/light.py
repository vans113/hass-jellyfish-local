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
from .const import (
    ATTR_BRIGHTNESS_PCT,
    ATTR_COLORS,
    ATTR_DIRECTION,
    ATTR_EFFECT_BETWEEN,
    ATTR_FOLDER,
    ATTR_NAME,
    ATTR_PATTERN,
    ATTR_PATTERN_TYPE,
    ATTR_SPACING,
    ATTR_SPEED,
    DEFAULT_SAVE_FOLDER,
    SERVICE_ADJUST_PATTERN,
    SERVICE_APPLY_LIGHT_STRING,
    SERVICE_DELETE_PATTERN,
    SERVICE_RUN_PATTERN,
    SERVICE_SAVE_PATTERN,
)
from .coordinator import JellyFishCoordinator
from .entity import JellyFishEntity, JellyFishZoneEntity
from .patterns import (
    DIRECTIONS,
    EFFECTS_BETWEEN,
    MAX_COLORS,
    PATTERN_TYPES,
    build_config,
    color_triples,
    wire_speed_to_ui,
)

PARALLEL_UPDATES = 1  # one socket; send commands one at a time

RGB_SCHEMA = vol.All(vol.ExactSequence((cv.byte, cv.byte, cv.byte)), vol.Coerce(tuple))
LIGHT_STRING_SCHEMA = {
    vol.Required(ATTR_COLORS): vol.All(cv.ensure_list, [RGB_SCHEMA], vol.Length(min=1)),
    vol.Optional(ATTR_BRIGHTNESS_PCT, default=100): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=100)
    ),
}


_PATTERN_FIELDS = {
    vol.Optional(ATTR_PATTERN_TYPE): vol.In(PATTERN_TYPES),
    vol.Optional(ATTR_SPEED): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    vol.Optional(ATTR_DIRECTION): vol.In(DIRECTIONS),
    vol.Optional(ATTR_SPACING): vol.All(vol.Coerce(int), vol.Range(min=0, max=50)),
    vol.Optional(ATTR_EFFECT_BETWEEN): vol.In(EFFECTS_BETWEEN),
    vol.Optional(ATTR_BRIGHTNESS_PCT): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
}
COLORS_SCHEMA = vol.All(cv.ensure_list, [RGB_SCHEMA], vol.Length(min=1, max=MAX_COLORS))
RUN_PATTERN_SCHEMA = {vol.Required(ATTR_COLORS): COLORS_SCHEMA, **_PATTERN_FIELDS}
ADJUST_PATTERN_SCHEMA = {vol.Optional(ATTR_COLORS): COLORS_SCHEMA, **_PATTERN_FIELDS}
DELETE_PATTERN_SCHEMA = {
    vol.Required(ATTR_PATTERN): vol.All(cv.string, vol.Length(min=3)),
}
SAVE_PATTERN_SCHEMA = {
    vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1, max=60)),
    vol.Optional(ATTR_FOLDER, default=DEFAULT_SAVE_FOLDER): vol.All(
        cv.string, vol.Length(min=1, max=60)
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
    platform.async_register_entity_service(
        SERVICE_RUN_PATTERN, RUN_PATTERN_SCHEMA, "async_run_pattern"
    )
    platform.async_register_entity_service(
        SERVICE_ADJUST_PATTERN, ADJUST_PATTERN_SCHEMA, "async_adjust_pattern"
    )
    platform.async_register_entity_service(
        SERVICE_SAVE_PATTERN, SAVE_PATTERN_SCHEMA, "async_save_pattern"
    )
    platform.async_register_entity_service(
        SERVICE_DELETE_PATTERN, DELETE_PATTERN_SCHEMA, "async_delete_pattern"
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
        attrs: dict[str, Any] = {"zones": list(self._zones)}
        # Pick a representative zone to describe. For a single-zone light that's
        # the zone itself; for the "All zones" light, use the shared look when
        # every lit zone is playing the same thing, so the card can still draw
        # the roofline and name. If the zones disagree, expose only the list.
        if len(states) == 1:
            state = states[0]
        else:
            lit = [s for s in states if s.is_on]
            looks = {(s.file, tuple(s.colors or ())) for s in lit}
            if not lit or len(looks) != 1:
                return attrs
            state = lit[0]
        cfg = state.config
        if not cfg.get("type") and state.file:
            cfg = self.coordinator.client.data.pattern_configs.get(state.file, {})
        run = cfg.get("runData") or {}
        attrs.update(
            {
                "pattern_type": cfg.get("type") or state.pattern_type,
                "activated_by": state.activated_by,
                # For the card's editor ("edit what's playing").
                "colors": [
                    f"#{r:02x}{g:02x}{b:02x}" for r, g, b in color_triples(cfg.get("colors") or [])
                ],
                "speed": wire_speed_to_ui(run.get("speed")),
                "direction": cfg.get("direction"),
                "spacing": cfg.get("spaceBetweenPixels"),
            }
        )
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
            # Keep the current brightness when switching looks instead of
            # letting the pattern jump back to its own saved level. Only fall
            # back to the pattern's brightness when nothing is lit to read.
            current = self.brightness
            level = pct if pct is not None else (to_percent(current) if current else None)
            await self._command(client.apply_pattern(effect, zones, brightness=level))
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

    async def async_run_pattern(
        self,
        colors: list[tuple[int, int, int]],
        pattern_type: str = "Color",
        speed: int | None = None,
        direction: str = "Center",
        spacing: int = 2,
        effect_between: str = "No Color Transform",
        brightness_pct: int | None = None,
    ) -> None:
        """Run a pattern built in HA (not saved on the controller)."""
        if brightness_pct is None:
            brightness_pct = to_percent(self.brightness) if self.brightness else 100
        try:
            config = build_config(
                pattern_type,
                [list(c) for c in colors],
                brightness_pct,
                speed=speed if speed is not None else (50 if pattern_type != "Color" else None),
                direction=direction,
                spacing=spacing,
                effect_between=effect_between,
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        await self._command(self.coordinator.client.run_custom(config, self._zones))

    async def async_adjust_pattern(self, **changes: Any) -> None:
        """Change parts of whatever is running (speed, type, colors, ...)."""
        mapped = {
            "pattern_type": changes.get(ATTR_PATTERN_TYPE),
            "speed": changes.get(ATTR_SPEED),
            "direction": changes.get(ATTR_DIRECTION),
            "spacing": changes.get(ATTR_SPACING),
            "effect_between": changes.get(ATTR_EFFECT_BETWEEN),
            "brightness": changes.get(ATTR_BRIGHTNESS_PCT),
            "colors": [list(c) for c in changes[ATTR_COLORS]] if ATTR_COLORS in changes else None,
        }
        mapped = {k: v for k, v in mapped.items() if v is not None}
        if not mapped:
            raise ServiceValidationError("Nothing to change")
        await self._command(self.coordinator.client.adjust(self._zones, **mapped))

    async def async_save_pattern(self, name: str, folder: str = DEFAULT_SAVE_FOLDER) -> None:
        """Save what's running on the first zone as a named pattern on the controller."""
        await self._command(self.coordinator.client.save_running(name, folder, self._zones[0]))

    async def async_delete_pattern(self, pattern: str) -> None:
        """Delete a saved (non-factory) pattern from the controller."""
        await self._command(self.coordinator.async_delete_pattern(pattern))


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
