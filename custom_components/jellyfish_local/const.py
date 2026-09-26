"""Constants for JellyFish Local."""

from datetime import timedelta
import logging

DOMAIN = "jellyfish_local"
LOGGER = logging.getLogger(__package__)

MANUFACTURER = "JellyFish Lighting"
MODEL = "JellyFish controller"

# Push updates keep state live; the poll only proves the socket is still alive
# and catches anything a push missed.
POLL_INTERVAL = timedelta(seconds=60)
# How often zones, patterns and firmware are re-read in the background.
METADATA_INTERVAL = timedelta(minutes=30)

SERVICE_APPLY_LIGHT_STRING = "apply_light_string"
ATTR_COLORS = "colors"
ATTR_BRIGHTNESS_PCT = "brightness_pct"
