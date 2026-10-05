"""Laufzeit eines Geräte-Eintrags: RSSI pro Scanner erfassen, Fenster bilden, Raum bestimmen."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    floor_registry as fr,
)
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .collector import RssiCollector
from .const import (
    AWAY_SECONDS,
    CONF_AREAS,
    CONF_NOTIFY_SERVICE,
    CONF_TRACKER,
    DATA_CAPTURE,
    DATA_HUB,
    DOMAIN,
    MODE_REPLACE,
    POLL_INTERVAL,
    SAVE_DELAY,
    SIGNAL_ROOMS_CHANGED,
    SIGNAL_UPDATE,
    STORAGE_VERSION,
    WINDOW_SECONDS,
)
from .model import (
    CAL_MIN_WINDOWS,
    CAL_TARGET_SEPARABILITY,
    Component,
    FingerprintModel,
    RoomDecider,
    Window,
    apply_drift,
)
from .notifications import CalibrationNotifier
from .scanners import scanner_infos

_LOGGER = logging.getLogger(__name__)


@dataclass
class Calibration:
    room: str
    mode: str
    end: float  # monotonic, spätestes Ende
    start: float = 0.0
    windows: int = 0
    seen_total: int = 0  # Summe gehörter Scanner über alle Fenster
    early: bool = False  # vorzeitig beendet, weil es gereicht hat


@dataclass
class Explanation:
    """Warum der aktuelle Raum vorn liegt (für das Attribut „begruendung“)."""

    room: str
    runner_up: str | None
    p_room: float
    p_runner_up: float
    scanners: list[tuple[str, float | None]] = field(default_factory=list)  # (Quelle, RSSI)


class ManoumiTracker:
    """Ein verfolgtes Gerät (z.B. ein iPhone über Private BLE Device)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.tracker_entity: str = entry.data[CONF_TRACKER]
        self.signal = SIGNAL_UPDATE.format(entry.entry_id)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.model = FingerprintModel()
        self.decider = RoomDecider()
        self.mode: str = MODE_REPLACE
        self.calibration: Calibration | None = None
        self._component: Component | None = None
        self.notifier = CalibrationNotifier(
            hass, entry.entry_id, entry.options.get(CONF_NOTIFY_SERVICE)
        )
        self.confusion: dict[str, dict[str, float]] = {}
        self.sequence: dict[str, float] = {}  # geglättete Trennschärfe je Raum

        self.room: str | None = None  # area_id, None = nicht da / unbekannt
        # Nach dem Start erst „unbekannt“; „nicht da“ erst nach AWAY_SECONDS ohne Signal.
        self.away = False
        self.posterior: dict[str, float] = {}
        self.last_window: Window | None = None
        self.last_room: str | None = None  # bleibt über „nicht da“ und Neustarts erhalten
        self.last_seen: datetime | None = None
        self.explanation: Explanation | None = None
        self.recent: deque[dict[str, Any]] = deque(maxlen=50)

        self._collector = RssiCollector(hass)
        self._window_end = 0.0
        self._last_heard = 0.0
        self._unsub: CALLBACK_TYPE | None = None
        self._labels: dict[str, str] = {}

    # --- Lebenszyklus -------------------------------------------------------------------

    @property
    def areas(self) -> list[str]:
        return list(self.entry.options.get(CONF_AREAS, self.entry.data.get(CONF_AREAS, [])))

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.model = FingerprintModel.from_dict(data.get("model"))
        self.mode = data.get("mode", MODE_REPLACE)
        self.last_room = data.get("last_room")
        if last_seen := data.get("last_seen"):
            self.last_seen = dt_util.parse_datetime(last_seen)
        await self._async_update_confusion()

    async def _async_update_confusion(self) -> None:
        """Trennschärfe im Executor berechnen – auf einer Kopie, das Original lernt weiter."""
        snapshot = FingerprintModel.from_dict(self.model.to_dict())
        areas = set(self.areas)
        self.confusion = await self.hass.async_add_executor_job(snapshot.confusion, areas)
        self.sequence = await self.hass.async_add_executor_job(snapshot.sequence_accuracy, areas)

    @callback
    def async_start(self) -> None:
        self._last_heard = monotonic_time_coarse()
        self._window_end = self._last_heard + WINDOW_SECONDS
        self._unsub = async_track_time_interval(
            self.hass, self._async_poll, timedelta(seconds=POLL_INTERVAL)
        )

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        await self._store.async_save(self._data_to_save())

    async def async_remove_storage(self) -> None:
        await self._store.async_remove()

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "mode": self.mode,
            "last_room": self.last_room,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    # --- Gerät & Anzeige-Hilfen -----------------------------------------------------------

    @callback
    def device_info(self) -> DeviceInfo:
        """Entities an das vorhandene Private-BLE-Gerät hängen (wie Bermuda), sonst eigenes Gerät."""
        entity = er.async_get(self.hass).async_get(self.tracker_entity)
        if entity and entity.device_id:
            device = dr.async_get(self.hass).async_get(entity.device_id)
            if device is not None:
                return DeviceInfo(identifiers=device.identifiers, connections=device.connections)
        return self.own_device_info()

    @callback
    def own_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self.entry.title,
            manufacturer="MaNoUmi",
            entry_type=DeviceEntryType.SERVICE,
        )

    def area_name(self, area_id: str) -> str:
        area = ar.async_get(self.hass).async_get_area(area_id)
        return area.name if area else area_id

    def area_by_name(self, name: str) -> str | None:
        for area_id in self.areas:
            if self.area_name(area_id) == name:
                return area_id
        return None

    def floor_of(self, area_id: str | None) -> fr.FloorEntry | None:
        if area_id is None:
            return None
        area = ar.async_get(self.hass).async_get_area(area_id)
        if area is None or area.floor_id is None:
            return None
        return fr.async_get(self.hass).async_get_floor(area.floor_id)

    def floors(self) -> list[fr.FloorEntry]:
        """Etagen der konfigurierten Räume, nach Level sortiert."""
        found: dict[str, fr.FloorEntry] = {}
        for area_id in self.areas:
            if (floor := self.floor_of(area_id)) is not None:
                found[floor.floor_id] = floor
        return sorted(found.values(), key=lambda f: (f.level is None, f.level or 0))

    # --- Erfassung -----------------------------------------------------------------------

    def _current_address(self) -> str | None:
        state = self.hass.states.get(self.tracker_entity)
        if state is None:
            return None
        address = state.attributes.get("current_address")
        return address.upper() if isinstance(address, str) else None

    @callback
    def _async_poll(self, _now: Any = None) -> None:
        address = self._current_address()
        addresses = [address] if address else []
        self._collector.poll(addresses)
        now = monotonic_time_coarse()
        if now < self._window_end:
            return
        self._window_end = now + WINDOW_SECONDS
        windows = self._collector.close(addresses)
        window = windows.get(address) if address else None
        if window is None:
            window = Window(rssi={}, active=set())
        corrected = self._drift_corrected(window)
        self._process_window(corrected, now)
        capture = self.hass.data.get(DOMAIN, {}).get(DATA_CAPTURE)
        if capture and window.seen_count:
            cal = self.calibration
            capture.add(
                "device",
                self.entry.title,
                cal.room if cal else None,
                window,
                corrected=corrected.to_dict()["rssi"],
                room=self.room,
            )

    def _drift_corrected(self, window: Window) -> Window:
        hub = self.hass.data.get(DOMAIN, {}).get(DATA_HUB)
        return apply_drift(window, hub.drift) if hub is not None else window

    # --- Auswertung ----------------------------------------------------------------------

    @callback
    def _process_window(self, window: Window, now: float) -> None:
        self.last_window = window
        changed = False
        rooms_changed = False

        if window.seen_count:
            self._last_heard = now
            self.last_seen = dt_util.utcnow()
        elif now - self._last_heard >= AWAY_SECONDS and not self.away:
            self.away = True
            self.room = None
            self.posterior = {}
            self.explanation = None
            self.decider.reset()
            changed = rooms_changed = True

        cal = self.calibration
        if cal is not None:
            if window.seen_count:
                if cal.windows == 0 or self._component is None:
                    self._component = self.model.begin_calibration(
                        cal.room, cal.mode == MODE_REPLACE
                    )
                self._component.add_calibration(window)
                cal.windows += 1
                cal.seen_total += window.seen_count
                if cal.windows % 2 == 0 and self.model.calibration_complete(
                    cal.room, self._component, set(self.areas)
                ):
                    cal.early = True
            if cal.early or now >= cal.end:
                self.calibration = None
                self.decider.reset()
                self._schedule_save()
                self.hass.async_create_task(self._async_finish_calibration(cal))
            changed = True

        if window.seen_count:
            if self.away:
                rooms_changed = True
            self.away = False
            lls = self.model.log_likelihoods(window, set(self.areas))
            decision = self.decider.step(lls)
            if decision.room != self.room:
                _LOGGER.debug("Raum %s → %s", self.room, decision.room)
                rooms_changed = True
            self.room = decision.room
            self.posterior = decision.posterior
            if self.room is not None and self.room != self.last_room:
                self.last_room = self.room
                self._schedule_save()
            self.explanation = self._explain(window)
            if decision.learn_room and self.calibration is None:
                self.model.learn(decision.learn_room, window)
                self._schedule_save()
            self.recent.append(
                {
                    "rssi": {s: round(v, 1) for s, v in window.rssi.items()},
                    "active": sorted(window.active),
                    "loglik": {r: round(v, 2) for r, v in lls.items()},
                    "room": self.room,
                    "learned": decision.learn_room,
                }
            )
            changed = True

        if changed:
            async_dispatcher_send(self.hass, self.signal)
        if rooms_changed:
            async_dispatcher_send(self.hass, SIGNAL_ROOMS_CHANGED)

    def _explain(self, window: Window) -> Explanation | None:
        if self.room is None or not self.posterior:
            return None
        others = sorted(
            (r for r in self.posterior if r != self.room),
            key=self.posterior.__getitem__,
            reverse=True,
        )
        runner_up = others[0] if others else None
        scanners: list[tuple[str, float | None]] = []
        if runner_up is not None:
            for source, diff in self.model.explain(window, self.room, runner_up)[:2]:
                if diff > 0.5:
                    scanners.append((source, window.rssi.get(source)))
        return Explanation(
            room=self.room,
            runner_up=runner_up,
            p_room=self.posterior.get(self.room, 0.0),
            p_runner_up=self.posterior.get(runner_up, 0.0) if runner_up else 0.0,
            scanners=scanners,
        )

    async def _async_finish_calibration(self, cal: Calibration) -> None:
        _LOGGER.info("Kalibrierung %s beendet: %d Fenster", self.area_name(cal.room), cal.windows)
        await self._async_update_confusion()
        own = self.confusion.get(cal.room, {})
        rival = next(((r, p) for r, p in own.items() if r != cal.room), None)
        self.notifier.finished(
            self.area_name(cal.room),
            cal.windows,
            cal.seen_total,
            seconds=monotonic_time_coarse() - cal.start,
            early=cal.early,
            separability=own.get(cal.room, 0.0) if len(self.confusion) > 1 else None,
            smoothed=self.sequence.get(cal.room) if len(self.confusion) > 1 else None,
            positions=len(self.model.rooms.get(cal.room, [])),
            # Ein neuer Raum kann früher kalibrierte Nachbarn verwechselbar machen.
            weakened=[
                (self.area_name(r), p)
                for r, p in sorted(self.sequence.items(), key=lambda i: i[1])
                if r != cal.room and p < CAL_TARGET_SEPARABILITY
            ],
            rival=(self.area_name(rival[0]), rival[1]) if rival else None,
        )
        async_dispatcher_send(self.hass, self.signal)

    # --- Bedienung -----------------------------------------------------------------------

    @callback
    def async_start_calibration(self, room: str, mode: str | None, duration: float) -> None:
        now = monotonic_time_coarse()
        self.calibration = Calibration(
            room=room, mode=mode or self.mode, end=now + duration, start=now
        )
        self._component = None
        _LOGGER.info(
            "Kalibrierung %s (%s) für %d s gestartet",
            self.area_name(room),
            self.calibration.mode,
            duration,
        )
        self.notifier.started(self.area_name(room), self.calibration.mode, duration)
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def async_stop_calibration(self) -> None:
        if self.calibration is not None:
            self.notifier.aborted(self.area_name(self.calibration.room), self.calibration.windows)
            self.calibration = None
            self._schedule_save()
            async_dispatcher_send(self.hass, self.signal)

    @callback
    def async_set_mode(self, mode: str) -> None:
        self.mode = mode
        self._schedule_save()
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def async_reset_learned(self) -> None:
        self.model.reset_learned()
        self.decider.reset()
        self._schedule_save()
        async_dispatcher_send(self.hass, self.signal)

    def room_status(self, area_id: str) -> tuple[str, str]:
        """Kalibrierbedarf eines Raums: (Code, Text) – ok / nachkalibrieren / nicht_kalibriert."""
        comps = self.model.rooms.get(area_id, [])
        windows = sum(c.cal_windows for c in comps)
        if windows == 0:
            return "nicht_kalibriert", "nicht kalibriert"
        if windows < CAL_MIN_WINDOWS:
            return "nachkalibrieren", "zu wenig Messfenster – „Ergänzen“"
        smooth = self.sequence.get(area_id)
        if len(self.confusion) > 1 and smooth is not None and smooth < CAL_TARGET_SEPARABILITY:
            own = self.confusion.get(area_id, {})
            rival = next((r for r in own if r != area_id), None)
            with_rival = f" – verwechselt mit {self.area_name(rival)}" if rival else ""
            return "nachkalibrieren", f"„Ergänzen“ an anderer Stelle{with_rival}"
        return "ok", "fertig"

    def scanner_label(self, source: str) -> str:
        if source not in self._labels:
            self._labels.update({s: i.name for s, i in scanner_infos(self.hass).items()})
        return self._labels.get(source, source)

    def diagnostics(self) -> dict[str, Any]:
        hub = self.hass.data.get(DOMAIN, {}).get(DATA_HUB)
        return {
            "tracker": self.tracker_entity,
            "areas": self.areas,
            "room": self.room,
            "last_room": self.last_room,
            "posterior": self.posterior,
            "mode": self.mode,
            "calibration": vars(self.calibration) if self.calibration else None,
            "scanners": {s: vars(i) for s, i in scanner_infos(self.hass).items()},
            "drift": hub.drift if hub else None,
            "confusion": self.confusion,
            "sequence_accuracy": self.sequence,
            "model": self.model.to_dict(),
            "recent_windows": list(self.recent),
        }
