"""Pattern building blocks shared by the client, services, entities and card.

No Home Assistant imports, so it can be unit-tested on its own.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

# Effect types the controller understands (from the JellyFish API docs).
PATTERN_TYPES = ["Color", "Chase", "Paint", "Stacker", "Sequence", "Multi-Paint"]
DIRECTIONS = ["Left", "Center", "Right"]
EFFECTS_BETWEEN = ["No Color Transform", "Repeat", "Progression", "Fade", "Fill with Black"]

MAX_COLORS = 30  # controller limit (90 values)

# Speed on the wire: 1 is fastest, bigger is slower. Users get the opposite
# (1 = slowest, 100 = fastest) because that's what a slider should mean.
SPEED_MIN, SPEED_MAX = 1, 100


def ui_speed_to_wire(ui: int) -> int:
    ui = max(SPEED_MIN, min(SPEED_MAX, int(ui)))
    return SPEED_MAX + SPEED_MIN - ui


def wire_speed_to_ui(wire: int | None) -> int | None:
    if wire is None or wire <= 0:
        return None
    return max(SPEED_MIN, min(SPEED_MAX, SPEED_MAX + SPEED_MIN - int(wire)))


def check_rgb(rgb: Iterable[int]) -> list[int]:
    values = [int(v) for v in rgb]
    if len(values) != 3 or any(not 0 <= v <= 255 for v in values):
        raise ValueError(f"invalid RGB color {list(rgb)!r}")
    return values


def check_brightness(brightness: int) -> int:
    if not 0 <= int(brightness) <= 100:
        raise ValueError(f"brightness must be 0-100, got {brightness}")
    return int(brightness)


def run_data(brightness: int, speed: int = 0) -> dict[str, Any]:
    return {
        "speed": speed,
        "brightness": brightness,
        "effect": "No Effect",
        "effectValue": 0,
        "rgbAdj": [100, 100, 100],
    }


def build_config(
    pattern_type: str,
    colors: Sequence[Sequence[int]] | Sequence[int],
    brightness: int = 100,
    *,
    speed: int | None = None,
    direction: str = "Center",
    spacing: int = 2,
    effect_between: str = "No Color Transform",
    skip: int = 2,
    num_leds: int = 1,
    color_pos: list[int] | None = None,
) -> dict[str, Any]:
    """Full pattern config, every field present (the controller ignores partial ones).

    `colors` is either a list of RGB triples or an already-flat list of ints.
    `speed` is in UI units (1 slowest .. 100 fastest); None means static.
    """
    if pattern_type not in PATTERN_TYPES and pattern_type != "Soffit":
        raise ValueError(f"unknown pattern type {pattern_type!r}")
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction {direction!r}")
    if effect_between not in EFFECTS_BETWEEN:
        raise ValueError(f"unknown effect {effect_between!r}")
    flat = flatten_colors(colors)
    if not flat:
        raise ValueError("at least one color is required")
    if len(flat) > MAX_COLORS * 3:
        raise ValueError(f"at most {MAX_COLORS} colors")
    wire_speed = 0 if speed is None or pattern_type == "Color" else ui_speed_to_wire(speed)
    return {
        "type": pattern_type,
        "colors": flat,
        "runData": run_data(check_brightness(brightness), wire_speed),
        "direction": direction,
        "spaceBetweenPixels": max(0, int(spacing)),
        "numOfLeds": max(1, int(num_leds)),
        "skip": max(1, int(skip)),
        "effectBetweenPixels": effect_between,
        "colorPos": color_pos if color_pos is not None else [-1],
        "cursor": -1,
        "ledOnPos": {},
        "soffitZone": "",
    }


def flatten_colors(colors: Sequence[Sequence[int]] | Sequence[int]) -> list[int]:
    if colors and isinstance(colors[0], (list, tuple)):
        flat: list[int] = []
        for rgb in colors:  # type: ignore[union-attr]
            flat.extend(check_rgb(rgb))  # type: ignore[arg-type]
        return flat
    values = [int(v) for v in colors]  # type: ignore[arg-type]
    if len(values) % 3:
        raise ValueError("flat color list length must be a multiple of 3")
    for i in range(0, len(values), 3):
        check_rgb(values[i : i + 3])
    return values


def color_triples(flat: Sequence[int]) -> list[tuple[int, int, int]]:
    return [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat) - len(flat) % 3, 3)]


def merge_config(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """Return a copy of `base` with UI-level changes applied, keeping every field."""
    cfg = dict(base or {})
    run = dict(cfg.get("runData") or run_data(100))
    if (v := changes.get("pattern_type")) is not None:
        if v not in PATTERN_TYPES:
            raise ValueError(f"unknown pattern type {v!r}")
        cfg["type"] = v
    if (v := changes.get("direction")) is not None:
        if v not in DIRECTIONS:
            raise ValueError(f"unknown direction {v!r}")
        cfg["direction"] = v
    if (v := changes.get("effect_between")) is not None:
        if v not in EFFECTS_BETWEEN:
            raise ValueError(f"unknown effect {v!r}")
        cfg["effectBetweenPixels"] = v
    if (v := changes.get("spacing")) is not None:
        cfg["spaceBetweenPixels"] = max(0, int(v))
    if (v := changes.get("colors")) is not None:
        cfg["colors"] = flatten_colors(v)
    if (v := changes.get("speed")) is not None:
        run["speed"] = ui_speed_to_wire(v)
    if (v := changes.get("brightness")) is not None:
        run["brightness"] = check_brightness(v)
    cfg["runData"] = run
    # Make sure every field exists, otherwise the controller drops the command.
    defaults = build_config("Color", [255, 255, 255])
    for key, value in defaults.items():
        cfg.setdefault(key, value)
    if not cfg.get("colors"):
        cfg["colors"] = defaults["colors"]
    return cfg


# --------------------------------------------------------------------------- #
# Color names, for describing unsaved custom patterns ("Warm White + Orange").
# --------------------------------------------------------------------------- #
_NAMED_COLORS: list[tuple[str, tuple[int, int, int]]] = [
    ("Red", (255, 0, 0)),
    ("Crimson", (180, 0, 30)),
    ("Red Orange", (255, 50, 0)),
    ("Orange", (255, 100, 0)),
    ("Amber", (255, 160, 0)),
    ("Gold", (255, 200, 40)),
    ("Yellow", (255, 255, 0)),
    ("Lime", (160, 255, 0)),
    ("Green", (0, 255, 0)),
    ("Mint", (60, 255, 150)),
    ("Teal", (0, 160, 140)),
    ("Turquoise", (0, 230, 210)),
    ("Cyan", (0, 255, 255)),
    ("Sky Blue", (80, 180, 255)),
    ("Blue", (0, 0, 255)),
    ("Indigo", (75, 0, 200)),
    ("Purple", (140, 0, 255)),
    ("Magenta", (255, 0, 255)),
    ("Pink", (255, 90, 170)),
    ("Hot Pink", (255, 20, 120)),
    ("Warm White", (255, 190, 110)),
    ("Soft White", (255, 220, 170)),
    ("White", (255, 255, 255)),
    ("Cool White", (210, 230, 255)),
]


def color_name(rgb: Sequence[int]) -> str:
    r, g, b = (int(v) for v in rgb[:3])
    if max(r, g, b) < 25:
        return "Off"
    # Compare on normalised colour (brightness doesn't change the name),
    # weighting green like the eye does.
    peak = max(r, g, b)
    nr, ng, nb = (v * 255 / peak for v in (r, g, b))
    best, best_d = "Custom", float("inf")
    for name, (cr, cg, cb) in _NAMED_COLORS:
        cpeak = max(cr, cg, cb)
        cr, cg, cb = (v * 255 / cpeak for v in (cr, cg, cb))
        d = 2 * (nr - cr) ** 2 + 4 * (ng - cg) ** 2 + 3 * (nb - cb) ** 2
        if d < best_d:
            best, best_d = name, d
    return best


def describe_colors(flat: Sequence[int], limit: int = 3) -> str:
    """'Warm White + Orange', ignoring black spacer colors and repeats."""
    names: list[str] = []
    for rgb in color_triples(flat):
        name = color_name(rgb)
        if name != "Off" and name not in names:
            names.append(name)
    if not names:
        return "Custom Color"
    if len(names) > limit:
        return " + ".join(names[:limit]) + f" +{len(names) - limit}"
    return " + ".join(names)
