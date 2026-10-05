"""RSSI je Scanner für eine Menge von BLE-Adressen sammeln und zu Fenstern verdichten.

HA reicht über Callbacks nur das jeweils bevorzugte Advert weiter, deshalb wird gepollt:
pro Scanner das letzte Advert der Adresse plus dessen Zeitstempel. Gleiche Zeitstempel =
dasselbe Advert → nicht doppelt zählen (wie Bermuda).
"""

from __future__ import annotations

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.core import HomeAssistant

from .const import INVALID_RSSI, MAX_ADVERT_AGE
from .model import Window, robust_mean
from .scanners import current_scanners


class RssiCollector:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._samples: dict[str, dict[str, list[float]]] = {}
        self._active: set[str] = set()
        self._last_stamp: dict[tuple[str, str], float] = {}

    def poll(self, addresses: list[str]) -> None:
        now = monotonic_time_coarse()
        for scanner in current_scanners(self.hass):
            source = scanner.source
            self._active.add(source)
            stamps = None
            for address in addresses:
                found = scanner.get_discovered_device_advertisement_data(address)
                if found is None:
                    continue
                rssi = found[1].rssi
                if rssi is None or rssi <= INVALID_RSSI:
                    continue
                if stamps is None:
                    stamps = scanner.discovered_device_timestamps
                stamp = stamps.get(address)
                if stamp is None or now - stamp > MAX_ADVERT_AGE:
                    continue
                key = (source, address)
                if self._last_stamp.get(key) == stamp:
                    continue
                self._last_stamp[key] = stamp
                self._samples.setdefault(address, {}).setdefault(source, []).append(float(rssi))

    def close(self, addresses: list[str]) -> dict[str, Window]:
        """Fenster je Adresse abschließen und Puffer leeren."""
        active = set(self._active)
        windows = {
            address: Window(
                rssi={s: robust_mean(v) for s, v in self._samples.get(address, {}).items()},
                active=active,
            )
            for address in addresses
        }
        self._samples = {}
        self._active = set()
        return windows
