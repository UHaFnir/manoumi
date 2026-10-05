"""Sensoren: Raum und Etage je Gerät; Personen je Raum und Drift im Haus-Eintrag."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ManoumiConfigEntry
from .const import (
    ATTR_DURATION,
    ATTR_MODE,
    ATTR_ROOM,
    CALIBRATION_DEFAULT_SECONDS,
    CALIBRATION_MAX_SECONDS,
    CALIBRATION_MIN_SECONDS,
    MODES,
    SERVICE_CALIBRATE,
    STATE_AWAY,
)
from .entity import ManoumiEntity, TrackerEntity
from .hub import ManoumiHub
from .scanners import scanner_infos
from .tracker import ManoumiTracker


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ManoumiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    owner = entry.runtime_data
    if isinstance(owner, ManoumiHub):
        areas = ar.async_get(hass).async_list_areas()
        async_add_entities(
            [OccupancySensor(owner, area.id, area.name) for area in areas] + [DriftSensor(owner)]
        )
        return

    entities: list[SensorEntity] = [RoomSensor(owner)]
    if owner.floors():
        entities.append(FloorSensor(owner))
    async_add_entities(entities)
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_CALIBRATE,
        {
            vol.Required(ATTR_ROOM): cv.string,
            vol.Optional(ATTR_MODE): vol.In(MODES),
            vol.Optional(ATTR_DURATION, default=CALIBRATION_DEFAULT_SECONDS): vol.All(
                vol.Coerce(int), vol.Range(CALIBRATION_MIN_SECONDS, CALIBRATION_MAX_SECONDS)
            ),
        },
        "async_calibrate",
    )


def _fmt_rssi(value: float | None) -> str:
    return "nicht gehört" if value is None else f"{value:.0f} dBm"


class RoomSensor(TrackerEntity, SensorEntity):
    """Aktueller Raum des Geräts."""

    _attr_device_class = SensorDeviceClass.ENUM
    _unrecorded_attributes = frozenset(
        {
            "wahrscheinlichkeiten",
            "konfidenz",
            "begruendung",
            "sichtbare_scanner",
            "zuletzt_gesehen",
            "raeume",
        }
    )

    def __init__(self, tracker: ManoumiTracker) -> None:
        super().__init__(tracker, "raum")

    @property
    def options(self) -> list[str]:
        return [self.tracker.area_name(a) for a in self.tracker.areas] + [STATE_AWAY]

    @property
    def native_value(self) -> str | None:
        if self.tracker.away:
            return STATE_AWAY
        if self.tracker.room is None:
            return None
        return self.tracker.area_name(self.tracker.room)

    def _reason(self) -> str | None:
        t = self.tracker
        ex = t.explanation
        if ex is None:
            return None
        text = f"{t.area_name(ex.room)} {ex.p_room:.0%}"
        if ex.runner_up:
            text += f" vor {t.area_name(ex.runner_up)} {ex.p_runner_up:.0%}"
        if ex.scanners:
            parts = [f"{t.scanner_label(s)} ({_fmt_rssi(r)})" for s, r in ex.scanners]
            text += " – entscheidend: " + ", ".join(parts)
        return text

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self.tracker
        window = t.last_window
        summary = t.model.summary()
        floor = t.floor_of(t.room if not t.away else None)
        rooms: dict[str, Any] = {}
        for area in t.areas:
            info = dict(
                summary.get(area, {"positionen": 0, "kalibrierfenster": 0, "gelernte_fenster": 0})
            )
            if len(t.confusion) > 1 and area in t.confusion:
                info["trennschaerfe"] = round(t.confusion[area].get(area, 0.0), 2)
                if area in t.sequence:
                    info["trennschaerfe_verlauf"] = round(t.sequence[area], 2)
                rival = next(((r, p) for r, p in t.confusion[area].items() if r != area), None)
                if rival:
                    info["verwechselt_mit"] = f"{t.area_name(rival[0])} ({rival[1]:.0%})"
            info["status"] = t.room_status(area)[0]
            rooms[t.area_name(area)] = info
        return {
            "konfidenz": round(t.posterior.get(t.room, 0.0), 2) if t.room else None,
            "begruendung": self._reason(),
            "wahrscheinlichkeiten": {
                t.area_name(r): round(p, 3)
                for r, p in sorted(t.posterior.items(), key=lambda i: -i[1])
            },
            "etage": floor.name if floor else None,
            "letzter_raum": t.area_name(t.last_room) if t.last_room else None,
            "zuletzt_gesehen": t.last_seen.isoformat() if t.last_seen else None,
            "sichtbare_scanner": window.seen_count if window else 0,
            "raeume": rooms,
            "nachkalibrieren": [
                t.area_name(a) for a in t.areas if t.room_status(a)[0] == "nachkalibrieren"
            ],
            "kalibrierung_laeuft": t.area_name(t.calibration.room) if t.calibration else None,
        }

    async def async_calibrate(self, call: ServiceCall) -> None:
        t = self.tracker
        room = call.data[ATTR_ROOM]
        area_id = room if room in t.areas else t.area_by_name(room)
        if area_id is None:
            raise ServiceValidationError(f"Unbekannter Raum: {room}")
        t.async_start_calibration(area_id, call.data.get(ATTR_MODE), call.data[ATTR_DURATION])


class FloorSensor(TrackerEntity, SensorEntity):
    """Etage des aktuellen Raums (aus der Area abgeleitet, wie bei Bermuda)."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, tracker: ManoumiTracker) -> None:
        super().__init__(tracker, "etage")

    @property
    def options(self) -> list[str]:
        return [f.name for f in self.tracker.floors()] + [STATE_AWAY]

    @property
    def native_value(self) -> str | None:
        t = self.tracker
        if t.away:
            return STATE_AWAY
        floor = t.floor_of(t.room)
        return floor.name if floor else None


class OccupancySensor(ManoumiEntity, SensorEntity):
    """Wie viele verfolgte Geräte/Personen gerade in einem Raum sind."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: ManoumiHub, area_id: str, area_name: str) -> None:
        super().__init__(hub, "personen", unique_suffix=f"personen_{area_id}")
        self.area_id_ = area_id
        self._attr_translation_placeholders = {"area": area_name}

    @property
    def native_value(self) -> int:
        return len(self.owner.occupancy().get(self.area_id_, []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"personen": self.owner.occupancy().get(self.area_id_, [])}


class DriftSensor(ManoumiEntity, SensorEntity):
    """Größte Drift eines Scanners laut Referenzgeräten (0 = alles wie bei der Ausgangslage)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "dB"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _unrecorded_attributes = frozenset({"drift_je_scanner", "referenzgeraete"})

    def __init__(self, hub: ManoumiHub) -> None:
        super().__init__(hub, "drift")

    @property
    def native_value(self) -> float | None:
        drift = self.owner.drift
        if not drift:
            return None if not self.owner.anchor_devices else 0.0
        return max(drift.values(), key=abs)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        infos = scanner_infos(self.hass)
        anchors = self.owner.anchor_addresses()
        return {
            "drift_je_scanner": {
                (infos[s].name if s in infos else s): round(d, 1)
                for s, d in sorted(self.owner.drift.items(), key=lambda i: -abs(i[1]))
            },
            "referenzgeraete": {
                name: f"von {self.owner.anchor_seen.get(addr, 0)} Scannern gehört"
                for addr, name in anchors.items()
            },
        }
