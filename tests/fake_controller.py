"""A fake JellyFish controller websocket server for tests.

Behaves like the real controller as far as the integration can tell:
answers toCtlrGet requests, applies toCtlrSet runPattern commands, and only
serves one websocket at a time. By default it does NOT echo state after a set
command (newer firmware behaviour); set `echo_on_set = True` for old firmware.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

from aiohttp import WSMsgType, web

PATTERN_CONFIGS: dict[str, dict[str, Any]] = {
    "Christmas/Candy Cane": {
        "type": "Chase",
        "colors": [255, 0, 0, 255, 255, 255],
        "runData": {
            "speed": 10,
            "brightness": 80,
            "effect": "No Effect",
            "effectValue": 0,
            "rgbAdj": [100, 100, 100],
        },
        "direction": "Left",
    },
    "Halloween/Spooky": {
        "type": "Stacker",
        "colors": [255, 100, 0, 128, 0, 128],
        "runData": {
            "speed": 5,
            "brightness": 100,
            "effect": "Lightning",
            "effectValue": 3,
            "rgbAdj": [100, 100, 100],
        },
        "direction": "Right",
    },
    "Everyday/Warm White": {
        "type": "Color",
        "colors": [255, 180, 110],
        "runData": {
            "speed": 0,
            "brightness": 100,
            "effect": "No Effect",
            "effectValue": 0,
            "rgbAdj": [100, 100, 100],
        },
        "direction": "Center",
    },
}


class FakeController:
    """In-process fake of a JellyFish controller."""

    def __init__(self) -> None:
        self.name = "Front House"
        self.hostname = "JellyFish-A1B2"
        self.firmware = {"ver": "2.5.3", "details": "", "isUpdate": False}
        self.zones: dict[str, Any] = {
            "Front": {
                "numPixels": 120,
                "portMap": [
                    {
                        "ctlrName": "JellyFish-A1B2",
                        "phyPort": 1,
                        "phyStartIdx": 0,
                        "phyEndIdx": 119,
                        "zoneRGBStartIdx": 0,
                    }
                ],
            },
            "Back": {
                "numPixels": 80,
                "portMap": [
                    {
                        "ctlrName": "JellyFish-A1B2",
                        "phyPort": 2,
                        "phyStartIdx": 0,
                        "phyEndIdx": 79,
                        "zoneRGBStartIdx": 0,
                    }
                ],
            },
        }
        self.pattern_configs = copy.deepcopy(PATTERN_CONFIGS)
        self.states: dict[str, dict[str, Any]] = {
            zone: {
                "state": 0,
                "zoneName": [zone],
                "file": "",
                "id": "",
                "data": "",
                "activatedBy": "manual",
            }
            for zone in self.zones
        }
        self.echo_on_set = False
        self.ignore_requests = False
        self.received: list[dict[str, Any]] = []
        self.connections = 0
        self.max_seen_concurrent = 0
        self._active: web.WebSocketResponse | None = None
        self._runner: web.AppRunner | None = None
        self.port = 0

    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        await self.drop()
        if self._runner:
            await self._runner.cleanup()

    async def drop(self) -> None:
        """Close the active connection from the controller side."""
        if self._active is not None and not self._active.closed:
            await self._active.close()

    @property
    def connected(self) -> bool:
        return self._active is not None and not self._active.closed

    def sent_commands(self) -> list[dict[str, Any]]:
        return [m for m in self.received if m.get("cmd") == "toCtlrSet"]

    async def push_state(self, zone: str, **changes: Any) -> None:
        """Simulate the JellyFish app changing a zone."""
        self.states[zone].update(changes)
        await self._send({"cmd": "fromCtlr", "runPattern": self.states[zone]})

    # ------------------------------------------------------------------ #
    async def _handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if self.connected:
            # Real controller: a second socket just hangs. Closing is close enough.
            await ws.close()
            return ws
        self._active = ws
        self.connections += 1
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                payload = json.loads(msg.data)
                self.received.append(payload)
                if self.ignore_requests:
                    continue
                await self._dispatch(payload)
        finally:
            if self._active is ws:
                self._active = None
        return ws

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.connected:
            assert self._active is not None
            await self._active.send_str(json.dumps(payload))

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        if payload.get("cmd") == "toCtlrGet":
            for key, *args in payload.get("get", []):
                await self._answer(key, args)
        elif payload.get("cmd") == "toCtlrSet" and "patternFileData" in payload:
            pf = payload["patternFileData"]
            self.pattern_configs[f"{pf['folders']}/{pf['name']}"] = json.loads(pf["jsonData"])
        elif payload.get("cmd") == "toCtlrSet" and "runPattern" in payload:
            run = payload["runPattern"]
            for zone in run["zoneName"]:
                new = dict(run)
                new["zoneName"] = [zone]
                if not run["file"] and not run["data"]:
                    # plain on/off only flips the state; "on" resumes what was showing
                    new = dict(
                        self.states[zone], state=run["state"], activatedBy=run["activatedBy"]
                    )
                self.states[zone] = new
                if self.echo_on_set:
                    await self._send({"cmd": "fromCtlr", "runPattern": self.states[zone]})
        await asyncio.sleep(0)

    async def _answer(self, key: str, args: list[str]) -> None:
        if key == "ctlrName":
            await self._send({"cmd": "fromCtlr", "ctlrName": self.name})
        elif key == "hostName":
            await self._send({"cmd": "fromCtlr", "hostName": self.hostname})
        elif key == "version":
            await self._send({"cmd": "fromCtlr", "version": self.firmware})
        elif key == "zones":
            await self._send({"cmd": "fromCtlr", "save": True, "zones": self.zones})
        elif key == "patternFileList":
            folders = sorted({p.split("/")[0] for p in self.pattern_configs})
            items = [{"folders": f, "name": "", "readOnly": False} for f in folders]
            items += [
                {"folders": p.split("/")[0], "name": p.split("/")[1], "readOnly": True}
                for p in self.pattern_configs
            ]
            await self._send({"cmd": "fromCtlr", "patternFileList": items})
        elif key == "runPattern":
            for zone in args or list(self.zones):
                if zone in self.states:
                    await self._send({"cmd": "fromCtlr", "runPattern": self.states[zone]})
        elif key == "patternFileData":
            for i in range(0, len(args), 2):
                name = f"{args[i]}/{args[i + 1]}"
                if name in self.pattern_configs:
                    await self._send(
                        {
                            "cmd": "fromCtlr",
                            "patternFileData": {
                                "folders": args[i],
                                "name": args[i + 1],
                                "jsonData": json.dumps(self.pattern_configs[name]),
                            },
                        }
                    )
