"""Haus-Eintrag: Referenzgeräte (Drift je Scanner) und Personenzählung je Raum."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .capture import CaptureWriter
from .collector import RssiCollector
from .const import (
    CONF_ANCHORS,
    CONF_CAPTURE,
    DATA_CAPTURE,
    DATA_TRACKERS,
    DOMAIN,
    POLL_INTERVAL,
    SAVE_DELAY,
    SIGNAL_ROOMS_CHANGED,
    SIGNAL_UPDATE,
    STORAGE_VERSION,
    WINDOW_SECONDS,
)
from .model import DriftEstimator
from .scanners import scanner_infos

_LOGGER = logging.getLogger(__name__)


@callback
def anchor_address(hass: HomeAssistant, device_id: str) -> str | None:
    """BLE-Adresse eines Geräts aus seinen Registry-Connections."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for kind, value in device.connections:
        if kind == dr.CONNECTION_BLUETOOTH:
            return value.upper()
    return None


class ManoumiHub:
    """Ein Eintrag pro Installation."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.signal = SIGNAL_UPDATE.format(entry.entry_id)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.drift_estimator = DriftEstimator()
        self.drift: dict[str, float] = {}
        self.anchor_seen: dict[str, int] = {}  # Adresse → Zahl gehörter Scanner im letzten Fenster
        self._collector = RssiCollector(hass)
        self._window_end = 0.0
        self._unsubs: list[CALLBACK_TYPE] = []

    @property
    def anchor_devices(self) -> list[str]:
        return list(self.entry.options.get(CONF_ANCHORS, []))

    def anchor_areas(self) -> dict[str, str | None]:
        """Adresse → Area-ID der Referenzgeräte (Label für Aufzeichnungen)."""
        registry = dr.async_get(self.hass)
        result: dict[str, str | None] = {}
        for device_id in self.anchor_devices:
            address = anchor_address(self.hass, device_id)
            device = registry.async_get(device_id)
            if address and device:
                result[address] = device.area_id
        return result

    def anchor_addresses(self) -> dict[str, str]:
        """Adresse → Gerätename aller Referenzgeräte mit BLE-Adresse."""
        registry = dr.async_get(self.hass)
        result: dict[str, str] = {}
        for device_id in self.anchor_devices:
            address = anchor_address(self.hass, device_id)
            device = registry.async_get(device_id)
            if address and device:
                result[address] = device.name_by_user or device.name or address
        return result

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.drift_estimator = DriftEstimator.from_dict(data.get("drift"))
        self.drift_estimator.retain(set(self.anchor_addresses()))
        self.drift = self.drift_estimator.drift()

    @callback
    def async_start(self) -> None:
        if self.entry.options.get(CONF_CAPTURE):
            writer = CaptureWriter(self.hass)
            writer.add_scanners({s: vars(i) for s, i in scanner_infos(self.hass).items()})
            self.hass.data[DOMAIN][DATA_CAPTURE] = writer
        self._window_end = monotonic_time_coarse() + WINDOW_SECONDS
        self._unsubs.append(
            async_track_time_interval(self.hass, self._async_poll, timedelta(seconds=POLL_INTERVAL))
        )
        self._unsubs.append(
            async_dispatcher_connect(self.hass, SIGNAL_ROOMS_CHANGED, self._async_rooms_changed)
        )

    async def async_stop(self) -> None:
        self.hass.data.get(DOMAIN, {}).pop(DATA_CAPTURE, None)
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self._data_to_save())

    async def async_remove_storage(self) -> None:
        await self._store.async_remove()

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {"drift": self.drift_estimator.to_dict()}

    @callback
    def _async_poll(self, _now: Any = None) -> None:
        addresses = list(self.anchor_addresses())
        if not addresses:
            return
        self._collector.poll(addresses)
        now = monotonic_time_coarse()
        if now < self._window_end:
            return
        self._window_end = now + WINDOW_SECONDS
        capture = self.hass.data.get(DOMAIN, {}).get(DATA_CAPTURE)
        areas = self.anchor_areas() if capture else {}
        for address, window in self._collector.close(addresses).items():
            self.anchor_seen[address] = window.seen_count
            if capture and window.rssi:
                capture.add("anchor", address, areas.get(address), window, drift=self.drift)
            if window.rssi:
                self.drift_estimator.add(address, window.rssi)
        self.drift = self.drift_estimator.drift()
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def async_reset_drift(self) -> None:
        """Ausgangslage neu aufnehmen (z.B. nach dem Umstellen eines Referenzgeräts)."""
        self.drift_estimator.reset()
        self.drift = {}
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def _async_rooms_changed(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    # --- Personenzählung --------------------------------------------------------------------

    def occupancy(self) -> dict[str, list[str]]:
        """Area → Namen der Geräte/Personen, die gerade dort erkannt sind."""
        result: dict[str, list[str]] = {}
        for tracker in self.hass.data.get(DOMAIN, {}).get(DATA_TRACKERS, {}).values():
            if tracker.away or tracker.room is None:
                continue
            result.setdefault(tracker.room, []).append(tracker.entry.title)
        return result

    def diagnostics(self) -> dict[str, Any]:
        return {
            "anchors": self.anchor_addresses(),
            "anchor_seen": self.anchor_seen,
            "drift": self.drift,
            "estimator": self.drift_estimator.to_dict(),
            "occupancy": self.occupancy(),
        }
