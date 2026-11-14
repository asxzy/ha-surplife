"""Constants for the Surplife integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "surplife"

MANUFACTURER: Final = "Zengge"
MODEL: Final = "Surplife Outdoor Permanent Lighting (0xB6)"

CONF_MAC: Final = "mac"
CONF_HW_VERSION: Final = "hw_version"
CONF_MODEL_INFO: Final = "model_info"
CONF_FIRMWARE_INFO: Final = "firmware_info"
CONF_LED_COUNT: Final = "led_count"

DEFAULT_NAME: Final = "Surplife Light"
DEFAULT_PORT: Final = 5577

#: Pseudo-effect offered alongside the 24 real effects so the light card can
#: leave pattern mode. Selecting it sets a plain static colour. It is not a
#: firmware effect and never reaches the wire as an effect id.
STATIC_EFFECT: Final = "Static"

#: How often to poll the state frame. The device reports only power, mode,
#: effect and speed, so this is mostly for picking up changes made by the app
#: or the hardware timers.
DEFAULT_SCAN_INTERVAL: Final = 30

#: Seconds to wait for a TCP command before giving up.
DEFAULT_TIMEOUT: Final = 6.0

SERVICE_SET_SCRIBBLE: Final = "set_scribble"
SERVICE_SET_SEGMENTS: Final = "set_segments"
SERVICE_SET_TIMER: Final = "set_timer"
SERVICE_CLEAR_TIMER: Final = "clear_timer"
SERVICE_SYNC_CLOCK: Final = "sync_clock"
SERVICE_APPLY_PRESET: Final = "apply_preset"
SERVICE_APPLY_LOOK: Final = "apply_look"

SERVICE_SAVE_SCENE: Final = "save_scene"
SERVICE_DELETE_SCENE: Final = "delete_scene"
SERVICE_APPLY_SCENE: Final = "apply_scene"
SERVICE_APPLY_SCENE_FOR_DATE: Final = "apply_scene_for_date"
SERVICE_IMPORT_SCENES: Final = "import_scenes"
SERVICE_APPLY_THEME: Final = "apply_theme"

ATTR_PATTERN: Final = "pattern"
ATTR_COLORS: Final = "colors"
ATTR_EFFECT: Final = "effect"
ATTR_DIRECTION: Final = "direction"
ATTR_DENSITY: Final = "density"
ATTR_SPEED: Final = "speed"
ATTR_BLINK: Final = "blink"
ATTR_BLINK_SPEED: Final = "blink_speed"
ATTR_ENTER: Final = "enter"
ATTR_SLOT: Final = "slot"
ATTR_TIME: Final = "time"
ATTR_ACTION: Final = "action"
ATTR_REPEAT: Final = "repeat"
ATTR_COLOR: Final = "color"
ATTR_ACTIVE: Final = "active"
ATTR_PRESET: Final = "preset"

# -- scenes and looks -------------------------------------------------------
ATTR_NAME: Final = "name"
ATTR_LOOK: Final = "look"
ATTR_PALETTE: Final = "palette"
ATTR_SCENE: Final = "scene"
ATTR_MODE: Final = "mode"
ATTR_TITLE: Final = "title"
ATTR_BRIGHTNESS: Final = "brightness"
ATTR_WHITE: Final = "white"
ATTR_SEGMENTS: Final = "segments"
ATTR_SCRIBBLE_EFFECT: Final = "scribble_effect"
ATTR_START: Final = "start"
ATTR_END: Final = "end"
ATTR_TAGS: Final = "tags"
ATTR_PRIORITY: Final = "priority"
ATTR_TOPIC: Final = "topic"
ATTR_RANDOMIZE: Final = "randomize"
ATTR_DATE: Final = "date"
ATTR_SCENES: Final = "scenes"
ATTR_REPLACE_ALL: Final = "replace_all"
