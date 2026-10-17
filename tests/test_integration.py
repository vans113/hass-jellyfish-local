"""Home Assistant level tests for JellyFish Local, run against the fake controller."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_RGB_COLOR,
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.jellyfish_local import client as client_mod
from custom_components.jellyfish_local.const import DOMAIN

from .fake_controller import FakeController

FRONT = "light.front_house_front"
BACK = "light.front_house_back"
ALL = "light.front_house_all_zones"
SELECT_FRONT = "select.front_house_front_pattern"
CONNECTED = "binary_sensor.front_house_connectivity"
REFRESH = "button.front_house_refresh_patterns_and_zones"


@pytest.fixture
async def fake():
    controller = FakeController()
    await controller.start()
    # Point the client at the fake's random port.
    with (
        patch.object(client_mod, "DEFAULT_PORT", controller.port),
        patch.object(client_mod.JellyFishClient.__init__, "__defaults__", (controller.port, 2.0)),
    ):
        yield controller
    await controller.stop()


@pytest.fixture
async def setup(hass: HomeAssistant, fake: FakeController):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="jellyfish-a1b2",
        title="Front House",
        data={CONF_HOST: "127.0.0.1"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def settle(hass: HomeAssistant) -> None:
    for _ in range(5):
        await asyncio.sleep(0.02)
        await hass.async_block_till_done()


async def call(hass: HomeAssistant, service: str, entity: str, **data) -> None:
    await hass.services.async_call(
        LIGHT_DOMAIN, service, {ATTR_ENTITY_ID: entity, **data}, blocking=True
    )
    await settle(hass)


# ---------------------------------------------------------------------- #
# Config flow
# ---------------------------------------------------------------------- #
async def test_user_flow_creates_entry(hass: HomeAssistant, fake: FakeController) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: " 127.0.0.1 "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Front House"
    assert result["data"] == {CONF_HOST: "127.0.0.1"}
    assert result["result"].unique_id == "jellyfish-a1b2"
    await hass.async_block_till_done()
    assert not fake.connected or fake.connections >= 2  # probe socket was released


async def test_user_flow_cannot_connect(hass: HomeAssistant, fake: FakeController) -> None:
    await fake.stop()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(hass: HomeAssistant, setup) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure(hass: HomeAssistant, setup, fake: FakeController) -> None:
    result = await setup.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    # The running integration held the controller's only socket; the flow
    # must free it (and keep it free) long enough to probe.
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await settle(hass)
    assert setup.state is ConfigEntryState.LOADED
    assert hass.states.get(FRONT).state == STATE_OFF


async def test_reconfigure_failure_restores_connection(
    hass: HomeAssistant, setup, fake: FakeController
) -> None:
    result = await setup.start_reconfigure_flow(hass)
    fake.ignore_requests = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    fake.ignore_requests = False
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=90))
    await settle(hass)
    assert hass.states.get(FRONT).state == STATE_OFF


async def test_reconfigure_wrong_device(hass: HomeAssistant, setup, fake: FakeController) -> None:
    fake.hostname = "JellyFish-OTHER"
    result = await setup.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"


# ---------------------------------------------------------------------- #
# Setup / entities
# ---------------------------------------------------------------------- #
async def test_setup_creates_entities(hass: HomeAssistant, setup, fake: FakeController) -> None:
    for entity_id in (FRONT, BACK, ALL, SELECT_FRONT, CONNECTED, REFRESH):
        assert hass.states.get(entity_id) is not None, entity_id
    front = hass.states.get(FRONT)
    assert front.state == STATE_OFF
    assert "Christmas/Candy Cane" in front.attributes[ATTR_EFFECT_LIST]
    assert hass.states.get(CONNECTED).state == STATE_ON
    assert fake.connections == 1

    registry = er.async_get(hass)
    assert registry.async_get(FRONT).unique_id == "jellyfish-a1b2_zone_Front"


async def test_setup_retries_when_unreachable(hass: HomeAssistant, fake: FakeController) -> None:
    await fake.stop()
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="jellyfish-a1b2", data={CONF_HOST: "127.0.0.1"}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_color_and_brightness(hass: HomeAssistant, setup, fake: FakeController) -> None:
    await call(hass, "turn_on", FRONT, **{ATTR_RGB_COLOR: (0, 0, 255), ATTR_BRIGHTNESS: 128})
    state = hass.states.get(FRONT)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_RGB_COLOR] == (0, 0, 255)
    assert state.attributes[ATTR_BRIGHTNESS] == 128  # 50% -> 128 round trip
    sent = json.loads(fake.sent_commands()[-1]["runPattern"]["data"])
    assert sent["runData"]["brightness"] == 50
    assert hass.states.get(BACK).state == STATE_OFF


async def test_brightness_one_is_not_zero(hass: HomeAssistant, setup, fake: FakeController) -> None:
    await call(hass, "turn_on", FRONT, **{ATTR_RGB_COLOR: (255, 0, 0), ATTR_BRIGHTNESS: 1})
    sent = json.loads(fake.sent_commands()[-1]["runPattern"]["data"])
    assert sent["runData"]["brightness"] == 1


async def test_brightness_keeps_running_pattern(
    hass: HomeAssistant, setup, fake: FakeController
) -> None:
    await call(hass, "turn_on", FRONT, **{ATTR_EFFECT: "Christmas/Candy Cane"})
    assert hass.states.get(FRONT).attributes[ATTR_EFFECT] == "Christmas/Candy Cane"
    await call(hass, "turn_on", FRONT, **{ATTR_BRIGHTNESS: 64})
    state = hass.states.get(FRONT)
    assert state.attributes[ATTR_EFFECT] == "Christmas/Candy Cane"
    assert state.attributes[ATTR_BRIGHTNESS] == 64
    cmd = fake.sent_commands()[-1]["runPattern"]
    assert cmd["file"] == "Christmas/Candy Cane"


async def test_turn_off_and_resume(hass: HomeAssistant, setup, fake: FakeController) -> None:
    await call(hass, "turn_on", FRONT, **{ATTR_EFFECT: "Halloween/Spooky"})
    await call(hass, "turn_off", FRONT)
    assert hass.states.get(FRONT).state == STATE_OFF
    await call(hass, "turn_on", FRONT)
    state = hass.states.get(FRONT)
    assert state.state == STATE_ON and state.attributes[ATTR_EFFECT] == "Halloween/Spooky"


async def test_all_zones(hass: HomeAssistant, setup, fake: FakeController) -> None:
    await call(hass, "turn_on", ALL, **{ATTR_EFFECT: "Everyday/Warm White"})
    assert fake.sent_commands()[-1]["runPattern"]["zoneName"] == ["Front", "Back"]
    assert hass.states.get(FRONT).state == STATE_ON
    assert hass.states.get(BACK).state == STATE_ON
    assert hass.states.get(ALL).attributes[ATTR_EFFECT] == "Everyday/Warm White"
    await call(hass, "turn_off", ALL)
    assert hass.states.get(ALL).state == STATE_OFF


async def test_unknown_effect_is_validation_error(hass: HomeAssistant, setup) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            "turn_on",
            {ATTR_ENTITY_ID: FRONT, ATTR_EFFECT: "Nope/Nope"},
            blocking=True,
        )


async def test_select_pattern(hass: HomeAssistant, setup, fake: FakeController) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: SELECT_FRONT, "option": "Halloween/Spooky"},
        blocking=True,
    )
    await settle(hass)
    assert hass.states.get(SELECT_FRONT).state == "Halloween/Spooky"
    assert hass.states.get(FRONT).attributes[ATTR_EFFECT] == "Halloween/Spooky"


async def test_light_string_service(hass: HomeAssistant, setup, fake: FakeController) -> None:
    await hass.services.async_call(
        DOMAIN,
        "apply_light_string",
        {ATTR_ENTITY_ID: BACK, "colors": [[255, 0, 0], [255, 255, 255]], "brightness_pct": 60},
        blocking=True,
    )
    await settle(hass)
    cmd = fake.sent_commands()[-1]["runPattern"]
    assert cmd["state"] == 3 and cmd["zoneName"] == ["Back"]
    assert json.loads(cmd["data"])["runData"]["brightness"] == 60
    assert hass.states.get(BACK).state == STATE_ON


async def test_push_update_from_app(hass: HomeAssistant, setup, fake: FakeController) -> None:
    await fake.push_state("Back", state=1, file="Christmas/Candy Cane")
    await settle(hass)
    state = hass.states.get(BACK)
    assert state.state == STATE_ON and state.attributes[ATTR_EFFECT] == "Christmas/Candy Cane"


async def test_disconnect_goes_unavailable_then_recovers(
    hass: HomeAssistant, setup, fake: FakeController
) -> None:
    await fake.drop()
    await settle(hass)
    # The immediate reconnect attempt may already have succeeded; if not,
    # entities must be unavailable until the next poll brings them back.
    if fake.connections == 1:
        assert hass.states.get(FRONT).state == STATE_UNAVAILABLE
        assert hass.states.get(CONNECTED).state == STATE_OFF
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=90))
    await settle(hass)
    assert hass.states.get(FRONT).state == STATE_OFF
    assert hass.states.get(CONNECTED).state == STATE_ON
    assert fake.connections == 2


async def test_unresponsive_socket_is_dropped(
    hass: HomeAssistant, setup, fake: FakeController
) -> None:
    fake.ignore_requests = True
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=90))
    await asyncio.sleep(2.5)  # client timeout is 2 s in tests
    await settle(hass)
    assert hass.states.get(FRONT).state == STATE_UNAVAILABLE
    fake.ignore_requests = False
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=200))
    await settle(hass)
    assert hass.states.get(FRONT).state == STATE_OFF


async def test_unload_closes_socket(hass: HomeAssistant, setup, fake: FakeController) -> None:
    assert fake.connected
    await hass.config_entries.async_unload(setup.entry_id)
    await settle(hass)
    assert setup.state is ConfigEntryState.NOT_LOADED
    assert not fake.connected


async def test_reload_reconnects(hass: HomeAssistant, setup, fake: FakeController) -> None:
    await hass.config_entries.async_reload(setup.entry_id)
    await settle(hass)
    assert setup.state is ConfigEntryState.LOADED
    assert hass.states.get(FRONT).state == STATE_OFF
    assert fake.connections == 2


async def test_new_and_removed_zones(hass: HomeAssistant, setup, fake: FakeController) -> None:
    fake.zones["Garage"] = {"numPixels": 30, "portMap": []}
    fake.states["Garage"] = {"state": 0, "zoneName": ["Garage"], "file": "", "id": "", "data": ""}
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: REFRESH}, blocking=True)
    await settle(hass)
    assert hass.states.get("light.front_house_garage") is not None

    del fake.zones["Back"]
    del fake.states["Back"]
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: REFRESH}, blocking=True)
    await settle(hass)
    registry = er.async_get(hass)
    assert registry.async_get(BACK) is None
    assert registry.async_get("select.front_house_back_pattern") is None


async def test_firmware_on_device(hass: HomeAssistant, setup, fake: FakeController) -> None:
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "jellyfish-a1b2")})
    assert device.sw_version == "2.5.3" and device.name == "Front House"
    fake.firmware = {"ver": "2.6.0", "details": "", "isUpdate": False}
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: REFRESH}, blocking=True)
    await settle(hass)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "jellyfish-a1b2")})
    assert device.sw_version == "2.6.0"


async def test_diagnostics(hass: HomeAssistant, setup) -> None:
    from custom_components.jellyfish_local.diagnostics import async_get_config_entry_diagnostics

    diag = await async_get_config_entry_diagnostics(hass, setup)
    assert diag["entry"][CONF_HOST] == "**REDACTED**"
    assert diag["connected"] is True
    assert set(diag["controller"]["zones"]) == {"Front", "Back"}


CUSTOM_DATA = json.dumps(
    {
        "type": "Color",
        "colors": [255, 201, 80, 255, 49, 0],
        "spaceBetweenPixels": 1,
        "effectBetweenPixels": "No Color Transform",
        "colorPos": [],
        "skip": 1,
        "numOfLeds": 1,
        "runData": {
            "speed": 10,
            "brightness": 23,
            "effect": "None",
            "effectValue": 0,
            "rgbAdj": [100, 100, 100],
        },
        "direction": "",
    }
)


async def _push_custom(hass, fake):
    # Exactly what firmware 4.6.1 reported for a color made in the JellyFish app.
    await fake.push_state(
        "Front",
        state=1,
        file="_JF-CUSTOM/Custom Color",
        data=CUSTOM_DATA,
        activatedBy="network_command",
    )
    await settle(hass)


async def test_app_custom_pattern_is_not_unknown(
    hass: HomeAssistant, setup, fake: FakeController
) -> None:
    await _push_custom(hass, fake)
    select = hass.states.get(SELECT_FRONT)
    assert select.state == "Custom: Custom Color"
    assert select.attributes["options"][0] == "Custom: Custom Color"
    light = hass.states.get(FRONT)
    assert light.attributes[ATTR_EFFECT] == "Custom: Custom Color"
    assert "Custom: Custom Color" in light.attributes[ATTR_EFFECT_LIST]
    assert light.attributes[ATTR_BRIGHTNESS] == 59  # 23 %


async def test_dimming_app_custom_pattern_keeps_it(
    hass: HomeAssistant, setup, fake: FakeController
) -> None:
    await _push_custom(hass, fake)
    await call(hass, "turn_on", FRONT, **{ATTR_BRIGHTNESS: 128})
    cmd = fake.sent_commands()[-1]["runPattern"]
    data = json.loads(cmd["data"])
    assert data["colors"] == [255, 201, 80, 255, 49, 0]
    assert data["runData"]["brightness"] == 50
    assert hass.states.get(FRONT).attributes[ATTR_BRIGHTNESS] == 128


async def test_reselecting_custom_is_noop(hass: HomeAssistant, setup, fake: FakeController) -> None:
    await _push_custom(hass, fake)
    before = len(fake.sent_commands())
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: SELECT_FRONT, "option": "Custom: Custom Color"},
        blocking=True,
    )
    await call(hass, "turn_on", FRONT, **{ATTR_EFFECT: "Custom: Custom Color"})
    assert len(fake.sent_commands()) == before
    # Picking a saved pattern afterwards still works and drops the custom entry.
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: SELECT_FRONT, "option": "Halloween/Spooky"},
        blocking=True,
    )
    await settle(hass)
    assert hass.states.get(SELECT_FRONT).state == "Halloween/Spooky"
    assert "Custom: Custom Color" not in hass.states.get(SELECT_FRONT).attributes["options"]


async def test_outputs_attribute(hass: HomeAssistant, setup) -> None:
    attrs = hass.states.get(FRONT).attributes
    assert attrs["pixels"] == 120
    assert attrs["outputs"] == [{"port": 1, "pixels": 120}]
