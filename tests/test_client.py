"""Tests for the standalone async client against the fake controller."""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from custom_components.jellyfish_local.client import (
    JellyFishClient,
    JellyFishConnectionError,
    JellyFishTimeoutError,
)

from .fake_controller import FakeController


@pytest.fixture
async def fake():
    controller = FakeController()
    await controller.start()
    yield controller
    await controller.stop()


@pytest.fixture
async def client(fake):
    async with aiohttp.ClientSession() as session:
        c = JellyFishClient("127.0.0.1", session, port=fake.port, timeout=2)
        yield c
        await c.disconnect()


async def test_refresh_all(client, fake):
    await client.connect()
    await client.refresh_all()
    d = client.data
    assert d.name == "Front House"
    assert d.hostname == "JellyFish-A1B2"
    assert d.firmware == "2.5.3"
    assert set(d.zones) == {"Front", "Back"}
    # folders are dropped, names are folder/name, sorted
    assert d.patterns == ["Christmas/Candy Cane", "Everyday/Warm White", "Halloween/Spooky"]
    assert not d.zone_states["Front"].is_on


async def test_color_sends_app_format_and_reads_back(client, fake):
    await client.connect()
    await client.refresh_all()
    await client.apply_color((255, 0, 0), 40, ["Front"])
    cmd = fake.sent_commands()[-1]["runPattern"]
    assert cmd["state"] == 1 and cmd["zoneName"] == ["Front"] and cmd["file"] == ""
    data = json.loads(cmd["data"])
    assert data["type"] == "Color" and data["colors"] == [255, 0, 0]
    assert data["runData"]["brightness"] == 40
    # new-firmware fake doesn't echo, so the client must have asked for state
    state = client.data.zone_states["Front"]
    assert state.is_on and state.rgb == (255, 0, 0) and state.brightness == 40
    assert not client.data.zone_states["Back"].is_on


async def test_pattern_with_brightness_keeps_pattern(client, fake):
    await client.connect()
    await client.refresh_all()
    await client.apply_pattern("Christmas/Candy Cane", ["Back"], brightness=25)
    cmd = fake.sent_commands()[-1]["runPattern"]
    assert cmd["file"] == "Christmas/Candy Cane"
    data = json.loads(cmd["data"])
    assert data["type"] == "Chase" and data["runData"]["brightness"] == 25
    assert data["runData"]["speed"] == 10  # rest of the pattern untouched
    state = client.data.zone_states["Back"]
    assert state.file == "Christmas/Candy Cane" and state.brightness == 25


async def test_pattern_without_brightness_sends_file_only(client, fake):
    await client.connect()
    await client.refresh_all()
    await client.apply_pattern("Halloween/Spooky", ["Front", "Back"])
    cmd = fake.sent_commands()[-1]["runPattern"]
    assert cmd["file"] == "Halloween/Spooky" and cmd["data"] == ""
    assert cmd["zoneName"] == ["Front", "Back"]


async def test_turn_on_resumes_and_off(client, fake):
    await client.connect()
    await client.refresh_all()
    await client.apply_pattern("Halloween/Spooky", ["Front"])
    await client.turn_off(["Front"])
    assert not client.data.zone_states["Front"].is_on
    await client.turn_on(["Front"])
    state = client.data.zone_states["Front"]
    assert state.is_on and state.file == "Halloween/Spooky"


async def test_light_string(client, fake):
    await client.connect()
    await client.refresh_all()
    await client.apply_light_string([(255, 0, 0), (0, 255, 0)], 70, ["Front"])
    cmd = fake.sent_commands()[-1]["runPattern"]
    data = json.loads(cmd["data"])
    assert cmd["state"] == 3 and data["type"] == "Soffit"
    assert data["colors"] == [0, 0, 0, 255, 0, 0, 0, 255, 0]
    assert data["colorPos"] == [-1, 0, 1]


async def test_validation(client, fake):
    await client.connect()
    await client.refresh_all()
    with pytest.raises(ValueError):
        await client.apply_color((300, 0, 0), 50, ["Front"])
    with pytest.raises(ValueError):
        await client.apply_color((1, 2, 3), 101, ["Front"])
    with pytest.raises(ValueError):
        await client.turn_on(["Garage"])
    with pytest.raises(ValueError):
        await client.apply_pattern("Nope/Nope", ["Front"])


async def test_push_updates_notify(client, fake):
    await client.connect()
    await client.refresh_all()
    calls = []
    remove = client.add_listener(lambda: calls.append(1))
    await fake.push_state("Back", state=1, file="Everyday/Warm White")
    for _ in range(50):
        if client.data.zone_states["Back"].is_on:
            break
        await asyncio.sleep(0.01)
    assert client.data.zone_states["Back"].file == "Everyday/Warm White"
    assert calls
    remove()


async def test_connection_drop_and_reconnect(client, fake):
    await client.connect()
    await client.refresh_all()
    lost = asyncio.Event()
    client.add_listener(lambda: None if client.connected else lost.set())
    await fake.drop()
    await asyncio.wait_for(lost.wait(), 2)
    assert not client.connected
    # a command transparently reconnects
    await client.turn_on(["Front"])
    assert client.connected and fake.connections == 2


async def test_disconnect_frees_the_single_slot(fake):
    async with aiohttp.ClientSession() as session:
        first = JellyFishClient("127.0.0.1", session, port=fake.port, timeout=2)
        await first.connect()
        await first.disconnect()
        for _ in range(50):
            if not fake.connected:
                break
            await asyncio.sleep(0.01)
        second = JellyFishClient("127.0.0.1", session, port=fake.port, timeout=2)
        await second.connect()
        await second.refresh_info()
        assert second.data.name == "Front House"
        await second.disconnect()


async def test_timeout(client, fake):
    await client.connect()
    fake.ignore_requests = True
    with pytest.raises(JellyFishTimeoutError):
        await client.refresh_info()


async def test_unreachable():
    async with aiohttp.ClientSession() as session:
        c = JellyFishClient("127.0.0.1", session, port=1, timeout=1)
        with pytest.raises(JellyFishConnectionError):
            await c.connect()


async def test_set_brightness_preserves_each_zone(client, fake):
    await client.connect()
    await client.refresh_all()
    await client.apply_pattern("Christmas/Candy Cane", ["Front"])
    await client.apply_light_string([(1, 2, 3), (4, 5, 6)], 90, ["Back"])
    await client.set_brightness(30)
    front, back = client.data.zone_states["Front"], client.data.zone_states["Back"]
    assert front.file == "Christmas/Candy Cane" and front.brightness == 30
    assert back.pattern_type == "Soffit" and back.brightness == 30
    assert back.colors == [0, 0, 0, 1, 2, 3, 4, 5, 6] and back.state == 3


async def test_set_brightness_on_unknown_zone_uses_warm_white(client, fake):
    await client.connect()
    await client.refresh_all()
    await client.set_brightness(50, ["Front"])
    assert client.data.zone_states["Front"].rgb == (255, 180, 110)


async def test_run_custom_and_adjust(client, fake):
    from custom_components.jellyfish_local.patterns import build_config

    await client.connect()
    await client.refresh_all()
    cfg = build_config("Chase", [[255, 0, 0], [0, 0, 255]], 80, speed=100, direction="Left")
    await client.run_custom(cfg, ["Front"])
    sent = json.loads(fake.sent_commands()[-1]["runPattern"]["data"])
    assert sent["type"] == "Chase" and sent["runData"]["speed"] == 1  # UI 100 = fastest = wire 1
    assert set(sent) >= {
        "spaceBetweenPixels",
        "effectBetweenPixels",
        "numOfLeds",
        "skip",
        "direction",
    }
    await client.adjust(["Front"], speed=1, pattern_type="Paint")
    sent = json.loads(fake.sent_commands()[-1]["runPattern"]["data"])
    assert sent["type"] == "Paint" and sent["runData"]["speed"] == 100
    assert sent["colors"] == [255, 0, 0, 0, 0, 255] and sent["runData"]["brightness"] == 80


async def test_adjust_saved_pattern_fetches_its_config(client, fake):
    await client.connect()
    await client.refresh_all()
    await client.apply_pattern("Halloween/Spooky", ["Back"])
    await client.adjust(["Back"], direction="Left")
    cmd = fake.sent_commands()[-1]["runPattern"]
    data = json.loads(cmd["data"])
    assert cmd["file"] == "Halloween/Spooky"
    assert data["type"] == "Stacker" and data["direction"] == "Left"
    assert data["colors"] == [255, 100, 0, 128, 0, 128]


async def test_save_pattern(client, fake):
    from custom_components.jellyfish_local.patterns import build_config

    await client.connect()
    await client.refresh_all()
    await client.run_custom(build_config("Color", [[1, 2, 3]], 50), ["Front"])
    key = await client.save_running("Wife Pick", "Home Assistant", "Front")
    assert key == "Home Assistant/Wife Pick"
    assert fake.pattern_configs[key]["colors"] == [1, 2, 3]
    assert key in client.data.patterns
    with pytest.raises(ValueError):
        await client.save_pattern("bad/name", "x", {})


async def test_batch_pattern_configs(client, fake):
    await client.connect()
    await client.refresh_all()
    configs = await client.get_pattern_configs(chunk=2)
    assert set(configs) == set(fake.pattern_configs)
    assert configs["Christmas/Candy Cane"]["type"] == "Chase"
    sent = [
        m
        for m in fake.received
        if m.get("cmd") == "toCtlrGet" and m["get"][0][0] == "patternFileData"
    ]
    assert len(sent) == 2  # 3 patterns in chunks of 2


def test_color_names():
    from custom_components.jellyfish_local.patterns import (
        describe_colors,
        ui_speed_to_wire,
        wire_speed_to_ui,
    )

    assert describe_colors([255, 201, 80, 255, 49, 0]) == "Warm White + Red Orange"
    assert (
        describe_colors([0, 166, 0, 105, 0, 201, 255, 128, 0, 0, 0, 0]) == "Green + Purple + Orange"
    )
    assert describe_colors([0, 0, 0]) == "Custom Color"
    assert ui_speed_to_wire(100) == 1 and wire_speed_to_ui(1) == 100


async def test_delete_pattern(client, fake):
    from custom_components.jellyfish_local.patterns import build_config

    # A stock color the controller leaves writable, like the real box does.
    fake.pattern_configs["Colors/Red"] = {"type": "Color", "colors": [255, 0, 0]}
    await client.connect()
    await client.refresh_all()
    assert "Christmas/Candy Cane" in client.data.read_only
    assert "Colors/Red" not in client.data.read_only
    assert not client.is_deletable("Colors/Red")  # stock folder: protected anyway
    with pytest.raises(ValueError):
        await client.delete_pattern("Colors/Red")
    assert "Colors/Red" in fake.pattern_configs
    key = await client.save_pattern("Mine", "Custom", build_config("Color", [[1, 2, 3]], 50))
    assert key in client.data.patterns
    await client.delete_pattern(key)
    sent = fake.sent_commands()[-1]
    assert sent["patternFileDelete"] == {"folders": "Custom", "name": "Mine"}
    assert key not in client.data.patterns and key not in fake.pattern_configs
    with pytest.raises(ValueError):
        await client.delete_pattern("Christmas/Candy Cane")  # factory pattern
    with pytest.raises(ValueError):
        await client.delete_pattern("Nope/Nope")
    assert "Christmas/Candy Cane" in fake.pattern_configs
