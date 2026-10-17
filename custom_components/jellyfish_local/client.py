"""Async client for the JellyFish Lighting controller.

The controller speaks JSON over a plain websocket on port 9000:

* requests:  {"cmd": "toCtlrGet", "get": [["<key>", *args]]}
* commands:  {"cmd": "toCtlrSet", "<key>": {...}}
* responses and push updates: {"cmd": "fromCtlr", "<key>": ...}

Some fields ("data", "jsonData") carry JSON encoded *as a string* inside the
outer JSON. The controller accepts only one websocket at a time, so this client
keeps exactly one connection and always closes it cleanly.

This module has no Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import contextlib
from dataclasses import dataclass, field
import json
import logging
from typing import Any

import aiohttp

from .patterns import build_config, describe_colors, merge_config

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 9000
DEFAULT_TIMEOUT = 10.0

KEY_NAME = "ctlrName"
KEY_HOSTNAME = "hostName"
KEY_VERSION = "version"
KEY_ZONES = "zones"
KEY_ZONE_STATE = "runPattern"
KEY_PATTERN_LIST = "patternFileList"
KEY_PATTERN_DATA = "patternFileData"

STATE_OFF = 0
STATE_ON = 1
STATE_LIGHT_STRING = 3

ACTIVATED_BY = "network_command"
DEFAULT_COLOR = (255, 180, 110)  # warm white


class JellyFishError(Exception):
    """Base error for the JellyFish client."""


class JellyFishConnectionError(JellyFishError):
    """Raised when the controller can't be reached or the connection drops."""


class JellyFishTimeoutError(JellyFishError):
    """Raised when the controller doesn't answer in time."""


RGB = tuple[int, int, int]


@dataclass
class ZoneState:
    """What a zone is currently doing."""

    state: int = STATE_OFF
    file: str | None = None
    pattern_type: str | None = None
    colors: list[int] = field(default_factory=list)
    brightness: int | None = None
    activated_by: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def is_on(self) -> bool:
        """Return True when the zone is lit."""
        return self.state != STATE_OFF

    @property
    def rgb(self) -> RGB | None:
        """Return the color when the zone shows one solid color."""
        if self.pattern_type == "Color" and len(self.colors) == 3:
            return (self.colors[0], self.colors[1], self.colors[2])
        return None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ZoneState:
        """Build from a runPattern payload."""
        data = _decode_embedded(payload.get("data"))
        run = data.get("runData") if isinstance(data, dict) else None
        brightness = run.get("brightness") if isinstance(run, dict) else None
        return cls(
            state=int(payload.get("state") or 0),
            file=payload.get("file") or None,
            pattern_type=data.get("type") if isinstance(data, dict) else None,
            colors=list(data.get("colors") or []) if isinstance(data, dict) else [],
            brightness=int(brightness) if brightness is not None else None,
            activated_by=payload.get("activatedBy") or None,
            config=data if isinstance(data, dict) else {},
        )


@dataclass
class ControllerData:
    """Everything the client knows about the controller."""

    name: str | None = None
    hostname: str | None = None
    firmware: str | None = None
    firmware_update_available: bool = False
    zones: dict[str, dict[str, Any]] = field(default_factory=dict)
    patterns: list[str] = field(default_factory=list)
    zone_states: dict[str, ZoneState] = field(default_factory=dict)
    pattern_configs: dict[str, dict[str, Any]] = field(default_factory=dict)


def custom_label(file: str = "", colors: list[int] | None = None) -> str:
    """Display name for something playing that isn't a saved pattern.

    The JellyFish app runs ad-hoc looks as e.g. "_JF-CUSTOM/Custom Color" and HA
    runs them with no file at all; neither has a name, so describe the colors.
    """
    if colors:
        return f"Custom: {describe_colors(colors)}"
    return f"Custom: {file.rpartition('/')[2] or file or 'Custom Color'}"


def running_label(state: ZoneState | None, patterns: list[str]) -> str | None:
    """What a lit zone is showing, as offered in effect/option lists."""
    if state is None or not state.is_on:
        return None
    if state.file and state.file in patterns:
        return state.file
    if state.file or state.config.get("type"):
        return custom_label(state.file or "", state.colors)
    return None


def _decode_embedded(value: Any) -> Any:
    """Decode a JSON-in-a-string field; '' or None become {}."""
    if isinstance(value, str):
        if not value:
            return {}
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value or {}


def _run_data(brightness: int) -> dict[str, Any]:
    return {
        "speed": 0,
        "brightness": brightness,
        "effect": "No Effect",
        "effectValue": 0,
        "rgbAdj": [100, 100, 100],
    }


def _pattern_config(
    pattern_type: str, colors: list[int], brightness: int, color_pos: list[int]
) -> dict[str, Any]:
    """Pattern config in the exact field order the JellyFish app uses."""
    return build_config(pattern_type, colors, brightness, color_pos=color_pos)


def _split_pattern(pattern: str) -> tuple[str, str]:
    folders, _, name = pattern.rpartition("/")
    return folders, name


def _check_brightness(brightness: int) -> int:
    if not 0 <= brightness <= 100:
        raise ValueError(f"brightness must be 0-100, got {brightness}")
    return int(brightness)


def _check_rgb(rgb: Iterable[int]) -> list[int]:
    values = [int(v) for v in rgb]
    if len(values) != 3 or any(not 0 <= v <= 255 for v in values):
        raise ValueError(f"invalid RGB color {rgb!r}")
    return values


class _Waiter:
    """Resolves when a response for `key` (and every zone in `zones`) arrives."""

    def __init__(self, key: str, zones: Iterable[str] | None = None) -> None:
        self.key = key
        self.pending = set(zones or ())
        self.future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        # Mark exceptions as retrieved even if nobody is awaiting any more.
        self.future.add_done_callback(lambda f: f.cancelled() or f.exception())

    def feed(self, key: str, zones: Iterable[str] = ()) -> None:
        if key != self.key or self.future.done():
            return
        self.pending.difference_update(zones)
        if not self.pending:
            self.future.set_result(None)


class JellyFishClient:
    """One persistent websocket connection to a JellyFish controller."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.data = ControllerData()
        self._session = session
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._waiters: list[_Waiter] = []
        self._listeners: list[Callable[[], None]] = []
        self._closing = False

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #
    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call `callback` on every push update or connection change."""
        self._listeners.append(callback)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(callback)

        return remove

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # pragma: no cover - listener bugs must not kill the reader
                _LOGGER.exception("JellyFish listener failed")

    async def connect(self) -> None:
        """Open the websocket if it isn't already open."""
        async with self._connect_lock:
            if self.connected:
                return
            await self._close_ws()
            self._closing = False
            _LOGGER.debug("Connecting to JellyFish controller at %s", self.url)
            try:
                async with asyncio.timeout(self.timeout):
                    self._ws = await self._session.ws_connect(
                        self.url, autoping=True, max_msg_size=0
                    )
            except (TimeoutError, aiohttp.ClientError, OSError) as err:
                self._ws = None
                raise JellyFishConnectionError(
                    f"Cannot connect to JellyFish controller at {self.url}: {err}"
                ) from err
            self._reader = asyncio.create_task(
                self._read_loop(self._ws), name=f"jellyfish-reader-{self.host}"
            )
        self._notify()

    async def disconnect(self) -> None:
        """Close the websocket and stop the reader."""
        self._closing = True
        async with self._connect_lock:
            await self._close_ws()
        self._notify()

    async def _close_ws(self) -> None:
        ws, reader = self._ws, self._reader
        self._ws, self._reader = None, None
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        self._fail_waiters(JellyFishConnectionError("Connection closed"))

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._handle_message(msg.data)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            _LOGGER.exception("JellyFish reader crashed")
        finally:
            if self._ws is ws:
                self._ws = None
                self._reader = None
                self._fail_waiters(JellyFishConnectionError("Connection lost"))
                if not self._closing:
                    _LOGGER.warning("Lost connection to JellyFish controller at %s", self.url)
                    self._notify()

    def _fail_waiters(self, err: Exception) -> None:
        for waiter in self._waiters:
            if not waiter.future.done():
                waiter.future.set_exception(err)
        self._waiters.clear()

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #
    def _handle_message(self, raw: str) -> None:
        _LOGGER.debug("Received: %s", raw)
        try:
            msg = json.loads(raw)
        except ValueError:
            _LOGGER.debug("Ignoring non-JSON message: %s", raw)
            return
        if not isinstance(msg, dict) or msg.get("cmd") != "fromCtlr":
            return

        data = self.data
        handled_key: str | None = None
        zones_seen: list[str] = []

        if KEY_NAME in msg:
            data.name = msg[KEY_NAME]
            handled_key = KEY_NAME
        elif KEY_HOSTNAME in msg:
            data.hostname = msg[KEY_HOSTNAME]
            handled_key = KEY_HOSTNAME
        elif KEY_VERSION in msg:
            version = msg[KEY_VERSION]
            if isinstance(version, dict):
                data.firmware = version.get("ver")
                data.firmware_update_available = bool(version.get("isUpdate"))
            else:
                data.firmware = str(version)
            handled_key = KEY_VERSION
        elif KEY_ZONES in msg:
            zones = msg[KEY_ZONES] or {}
            data.zones = dict(zones)
            for gone in set(data.zone_states) - set(zones):
                data.zone_states.pop(gone, None)
            handled_key = KEY_ZONES
        elif KEY_PATTERN_LIST in msg:
            names = []
            for item in msg[KEY_PATTERN_LIST] or []:
                if item.get("name"):  # entries without a name are folders
                    names.append(f"{item.get('folders', '')}/{item['name']}")
            data.patterns = sorted(names, key=str.casefold)
            handled_key = KEY_PATTERN_LIST
        elif KEY_ZONE_STATE in msg:
            payload = msg[KEY_ZONE_STATE] or {}
            state = ZoneState.from_payload(payload)
            zones_seen = list(payload.get("zoneName") or [])
            for zone in zones_seen:
                previous = data.zone_states.get(zone)
                if state.brightness is None and previous is not None:
                    # Pattern files often come back without run data; keep the
                    # last brightness we saw so the slider doesn't jump.
                    state.brightness = previous.brightness
                data.zone_states[zone] = state
            handled_key = KEY_ZONE_STATE
        elif KEY_PATTERN_DATA in msg:
            payload = msg[KEY_PATTERN_DATA] or {}
            if payload.get("name"):
                key = f"{payload.get('folders', '')}/{payload['name']}"
                data.pattern_configs[key] = _decode_embedded(payload.get("jsonData"))
                zones_seen = [key]
                if key not in data.patterns:
                    data.patterns = sorted([*data.patterns, key], key=str.casefold)
            handled_key = KEY_PATTERN_DATA

        if handled_key is None:
            return
        for waiter in list(self._waiters):
            waiter.feed(handled_key, zones_seen)
        self._waiters = [w for w in self._waiters if not w.future.done()]
        self._notify()

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self.connected:
            await self.connect()
        assert self._ws is not None
        raw = json.dumps(payload, separators=(",", ":"))
        _LOGGER.debug("Sending: %s", raw)
        try:
            await self._ws.send_str(raw)
        except (aiohttp.ClientError, ConnectionError, RuntimeError) as err:
            await self._close_ws()
            raise JellyFishConnectionError(f"Send failed: {err}") from err

    async def _request(self, key: str, *args: str, expect: Iterable[str] | None = None) -> None:
        """Send a get request and wait until the answer has been processed."""
        waiter = _Waiter(key, expect)
        self._waiters.append(waiter)
        try:
            await self._send({"cmd": "toCtlrGet", "get": [[key, *args]]})
            async with asyncio.timeout(self.timeout):
                await waiter.future
        except TimeoutError as err:
            raise JellyFishTimeoutError(f"Controller did not answer '{key}' request") from err
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    async def refresh_info(self) -> None:
        """Name, hostname and firmware version."""
        await self._request(KEY_NAME)
        await self._request(KEY_HOSTNAME)
        await self._request(KEY_VERSION)

    async def refresh_zones(self) -> None:
        await self._request(KEY_ZONES)

    async def refresh_patterns(self) -> None:
        await self._request(KEY_PATTERN_LIST)

    async def refresh_zone_states(self, zones: Iterable[str] | None = None) -> None:
        zone_list = list(zones) if zones else list(self.data.zones)
        if not zone_list:
            return
        await self._request(KEY_ZONE_STATE, *zone_list, expect=zone_list)

    async def refresh_all(self) -> None:
        await self.refresh_info()
        await self.refresh_zones()
        await self.refresh_patterns()
        await self.refresh_zone_states()

    async def get_pattern_config(self, pattern: str) -> dict[str, Any]:
        """Fetch (and cache) the stored config for a pattern file."""
        folders, name = _split_pattern(pattern)
        await self._request(KEY_PATTERN_DATA, folders, name, expect=[pattern])
        return self.data.pattern_configs.get(pattern, {})

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #
    def _zones(self, zones: Iterable[str] | None) -> list[str]:
        zone_list = list(zones) if zones else list(self.data.zones)
        unknown = [z for z in zone_list if self.data.zones and z not in self.data.zones]
        if unknown:
            raise ValueError(f"Unknown zone(s): {', '.join(unknown)}")
        if not zone_list:
            raise ValueError("No zones to control")
        return zone_list

    async def _run(
        self,
        zones: list[str],
        state: int,
        file: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        await self._send(
            {
                "cmd": "toCtlrSet",
                "runPattern": {
                    "state": state,
                    "zoneName": zones,
                    "file": file,
                    "id": "",
                    "data": json.dumps(data, separators=(",", ":")) if data else "",
                    "activatedBy": ACTIVATED_BY,
                },
            }
        )
        # Newer firmware doesn't always echo the new state, so ask for it.
        try:
            await self.refresh_zone_states(zones)
        except JellyFishTimeoutError:
            _LOGGER.debug("No state confirmation for %s; will pick it up on next poll", zones)

    async def turn_on(self, zones: Iterable[str] | None = None) -> None:
        """Turn zones on, resuming whatever they last showed."""
        await self._run(self._zones(zones), STATE_ON)

    async def turn_off(self, zones: Iterable[str] | None = None) -> None:
        await self._run(self._zones(zones), STATE_OFF)

    async def apply_color(
        self, rgb: RGB, brightness: int = 100, zones: Iterable[str] | None = None
    ) -> None:
        """Set zones to one solid color."""
        config = _pattern_config("Color", _check_rgb(rgb), _check_brightness(brightness), [-1])
        await self._run(self._zones(zones), STATE_ON, data=config)

    async def apply_light_string(
        self,
        colors: list[RGB],
        brightness: int = 100,
        zones: Iterable[str] | None = None,
    ) -> None:
        """Paint a repeating sequence of colors along the zones."""
        if not colors:
            raise ValueError("colors must not be empty")
        flat = [0, 0, 0]
        positions = [-1]
        for index, rgb in enumerate(colors):
            flat.extend(_check_rgb(rgb))
            positions.append(index)
        config = _pattern_config("Soffit", flat, _check_brightness(brightness), positions)
        await self._run(self._zones(zones), STATE_LIGHT_STRING, data=config)

    async def apply_pattern(
        self,
        pattern: str,
        zones: Iterable[str] | None = None,
        brightness: int | None = None,
    ) -> None:
        """Play a saved pattern, optionally at a specific brightness."""
        if self.data.patterns and pattern not in self.data.patterns:
            raise ValueError(f"Unknown pattern: {pattern}")
        zone_list = self._zones(zones)
        data: dict[str, Any] | None = None
        if brightness is not None:
            _check_brightness(brightness)
            config = dict(self.data.pattern_configs.get(pattern) or {})
            if not config:
                config = dict(await self.get_pattern_config(pattern))
            if config:
                run = dict(config.get("runData") or _run_data(brightness))
                run["brightness"] = brightness
                config["runData"] = run
                data = config
        await self._run(zone_list, STATE_ON, file=pattern, data=data)

    async def set_brightness(self, brightness: int, zones: Iterable[str] | None = None) -> None:
        """Change brightness without changing what each zone is showing.

        Patterns are re-applied at the new brightness, solid colors and custom
        light strings are re-sent with the new brightness, and a zone with
        nothing known about it gets warm white.
        """
        _check_brightness(brightness)
        groups: dict[str, list[str]] = {}
        plan: dict[str, tuple[str, Any]] = {}
        for zone in self._zones(zones):
            state = self.data.zone_states.get(zone) or ZoneState()
            if state.file and state.file in self.data.patterns:
                key = f"file:{state.file}"
                plan[key] = ("file", state.file)
            elif state.config.get("type"):
                key = "config:" + json.dumps(state.config, sort_keys=True)
                plan[key] = ("config", (state.config, state.state or STATE_ON))
            else:
                key = "default"
                plan[key] = ("default", None)
            groups.setdefault(key, []).append(zone)

        for key, zone_list in groups.items():
            kind, value = plan[key]
            if kind == "file":
                await self.apply_pattern(value, zone_list, brightness=brightness)
            elif kind == "config":
                config, run_state = value
                config = dict(config)
                run = dict(config.get("runData") or _run_data(brightness))
                run["brightness"] = brightness
                config["runData"] = run
                await self._run(zone_list, run_state, data=config)
            else:
                await self.apply_color(DEFAULT_COLOR, brightness, zone_list)

    # ------------------------------------------------------------------ #
    # Custom patterns (what the app's editor does)
    # ------------------------------------------------------------------ #
    async def run_custom(self, config: dict[str, Any], zones: Iterable[str] | None = None) -> None:
        """Run a full pattern config that isn't saved on the controller."""
        await self._run(self._zones(zones), STATE_ON, data=config)

    async def _base_config(self, zone: str) -> tuple[dict[str, Any], str]:
        """The config a zone is running now, and the saved file name (if any)."""
        state = self.data.zone_states.get(zone) or ZoneState()
        file = state.file if state.file in self.data.patterns else ""
        if state.config.get("type"):
            return dict(state.config), file
        if file:
            return dict(await self.get_pattern_config(file)), file
        return build_config("Color", list(DEFAULT_COLOR), state.brightness or 100), ""

    async def adjust(self, zones: Iterable[str] | None = None, **changes: Any) -> None:
        """Tweak whatever is running: type, speed, direction, spacing, colors, brightness."""
        groups: dict[str, tuple[dict[str, Any], str, list[str]]] = {}
        for zone in self._zones(zones):
            base, file = await self._base_config(zone)
            new = merge_config(base, **changes)
            key = json.dumps([new, file], sort_keys=True)
            groups.setdefault(key, (new, file, []))[2].append(zone)
        for config, file, zone_list in groups.values():
            # A saved pattern keeps its name while running with changes, like the app.
            await self._run(zone_list, STATE_ON, file=file, data=config)

    async def save_pattern(self, name: str, folder: str, config: dict[str, Any]) -> str:
        """Store a pattern on the controller so it shows up by name everywhere."""
        name, folder = name.strip(), folder.strip()
        if not name or not folder or "/" in name or "/" in folder:
            raise ValueError("pattern name and folder are required and can't contain '/'")
        key = f"{folder}/{name}"
        await self._send(
            {
                "cmd": "toCtlrSet",
                "patternFileData": {
                    "folders": folder,
                    "name": name,
                    "jsonData": json.dumps(config, separators=(",", ":")),
                },
            }
        )
        self.data.pattern_configs[key] = config
        try:
            await self.refresh_patterns()
        except JellyFishTimeoutError:
            _LOGGER.debug("Pattern list not refreshed after save; will catch up later")
        if key not in self.data.patterns:
            self.data.patterns = sorted([*self.data.patterns, key], key=str.casefold)
        return key

    async def save_running(self, name: str, folder: str, zone: str) -> str:
        base, _ = await self._base_config(self._zones([zone])[0])
        return await self.save_pattern(name, folder, base)

    async def get_pattern_configs(
        self, patterns: Iterable[str] | None = None, chunk: int = 20
    ) -> dict[str, dict[str, Any]]:
        """Fetch stored configs (colors etc.) for many patterns, a few per request."""
        wanted = [p for p in (patterns or self.data.patterns) if p not in self.data.pattern_configs]
        for i in range(0, len(wanted), chunk):
            part = wanted[i : i + chunk]
            args: list[str] = []
            for pattern in part:
                args.extend(_split_pattern(pattern))
            try:
                await self._request(KEY_PATTERN_DATA, *args, expect=part)
            except JellyFishTimeoutError:
                _LOGGER.debug("Some pattern configs didn't arrive: %s", part)
        return {
            p: self.data.pattern_configs[p]
            for p in (patterns or self.data.patterns)
            if p in self.data.pattern_configs
        }
