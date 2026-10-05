"""Konstanten für MaNoUmi."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "manoumi"
PLATFORMS = [Platform.SENSOR, Platform.SELECT, Platform.BUTTON]

CONF_TRACKER = "tracker"
CONF_AREAS = "areas"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_TYPE = "type"
CONF_ANCHORS = "anchors"
CONF_CAPTURE = "capture"

TYPE_DEVICE = "device"
TYPE_HUB = "hub"
HUB_UNIQUE_ID = "hub"

STORAGE_VERSION = 1
SAVE_DELAY = 60  # s

POLL_INTERVAL = 2.0  # s
WINDOW_SECONDS = 6.0  # Länge eines Messfensters
MAX_ADVERT_AGE = 10.0  # s – ältere Adverts eines Scanners zählen nicht
AWAY_SECONDS = 60.0  # so lange kein Scanner etwas hört → „nicht da“
INVALID_RSSI = -127

CALIBRATION_DEFAULT_SECONDS = 60  # Erstkalibrierung je Raum; Nachbessern gezielt per „Ergänzen“
CALIBRATION_MIN_SECONDS = 30
CALIBRATION_MAX_SECONDS = 1800

MODE_REPLACE = "neu"
MODE_ADD = "ergaenzen"
MODES = [MODE_REPLACE, MODE_ADD]

OPTION_OFF = "Aus"
STATE_AWAY = "Nicht da"

SERVICE_CALIBRATE = "calibrate"
ATTR_ROOM = "room"
ATTR_MODE = "mode"
ATTR_DURATION = "duration"

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"
# Irgendein Gerät hat Raum/Anwesenheit geändert (für die Personenzählung im Haus-Eintrag).
SIGNAL_ROOMS_CHANGED = f"{DOMAIN}_rooms_changed"

DATA_TRACKERS = "trackers"
DATA_HUB = "hub"
DATA_CAPTURE = "capture"
