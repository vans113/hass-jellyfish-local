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

SERVICE_RUN_PATTERN = "run_pattern"
SERVICE_ADJUST_PATTERN = "adjust_pattern"
SERVICE_SAVE_PATTERN = "save_pattern"
SERVICE_DELETE_PATTERN = "delete_pattern"
ATTR_PATTERN = "pattern"
ATTR_PATTERN_TYPE = "pattern_type"
ATTR_SPEED = "speed"
ATTR_DIRECTION = "direction"
ATTR_SPACING = "spacing"
ATTR_EFFECT_BETWEEN = "effect_between"
ATTR_NAME = "name"
ATTR_FOLDER = "folder"
DEFAULT_SAVE_FOLDER = "Home Assistant"
