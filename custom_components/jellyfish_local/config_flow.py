"""Config flow for JellyFish Local."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .client import ControllerData, JellyFishClient, JellyFishError
from .const import DOMAIN, LOGGER


class JellyFishConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a controller by IP address or hostname."""

    VERSION = 1

    async def _probe(self, host: str) -> ControllerData:
        client = JellyFishClient(host, async_get_clientsession(self.hass))
        try:
            await client.connect()
            await client.refresh_info()
            return client.data
        finally:
            await client.disconnect()

    async def _validate(self, host: str, errors: dict[str, str]) -> ControllerData | None:
        try:
            return await self._probe(host)
        except JellyFishError:
            errors["base"] = "cannot_connect"
        except Exception:
            LOGGER.exception("Unexpected error talking to %s", host)
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            # Check this before connecting: a controller that's already set up
            # is holding its only websocket slot and would refuse the probe.
            self._async_abort_entries_match({CONF_HOST: host})
            if info := await self._validate(host, errors):
                await self.async_set_unique_id(_unique_id(info, host))
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=info.name or info.hostname or host, data={CONF_HOST: host}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HOST): str}), user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the controller's address (e.g. after a new DHCP lease)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator is not None:
                # Release the single websocket slot so the probe can get in.
                await coordinator.async_pause()
            info = await self._validate(host, errors)
            if info and _unique_id(info, host) == entry.unique_id:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host})
            if coordinator is not None:
                await coordinator.async_resume()
            if info:
                return self.async_abort(reason="wrong_device")
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HOST): str}),
                user_input or {CONF_HOST: entry.data[CONF_HOST]},
            ),
            errors=errors,
        )


def _unique_id(info: ControllerData, host: str) -> str:
    return (info.hostname or host).lower()
