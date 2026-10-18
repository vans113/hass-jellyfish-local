"""Serve the JellyFish card with the integration (no separate HACS download)."""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.start import async_at_start
from homeassistant.loader import async_get_integration

from .const import DOMAIN, LOGGER

CARD_FILE = Path(__file__).parent / "www" / "jellyfish-card.js"
CARD_URL = f"/{DOMAIN}/jellyfish-card.js"
_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Make `custom:jellyfish-card` available on every dashboard.

    The registration is deferred until Home Assistant has finished starting, so
    the `frontend` component is guaranteed loaded by the time we add the module
    URL. Doing it during setup can lose a start-up race against `frontend` and
    skip the card entirely, leaving dashboards with "Custom element doesn't
    exist: jellyfish-card".
    """

    async def _register(_: HomeAssistant) -> None:
        if hass.data.get(_REGISTERED):
            return
        if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
            return  # no web UI (e.g. test setups)
        try:
            from homeassistant.components.frontend import add_extra_js_url
            from homeassistant.components.http import StaticPathConfig
        except ImportError:  # pragma: no cover
            return
        version = (await async_get_integration(hass, DOMAIN)).version or "0"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=False)]
        )
        # The version query makes browsers fetch the new card after an update.
        add_extra_js_url(hass, f"{CARD_URL}?v={version}")
        hass.data[_REGISTERED] = True
        LOGGER.debug("Registered JellyFish card at %s", CARD_URL)

    async_at_start(hass, _register)
