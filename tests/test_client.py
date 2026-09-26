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
