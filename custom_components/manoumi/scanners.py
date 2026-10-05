"""Zugriff auf die Bluetooth-Scanner von HA und ihre Zuordnung zu Geräten/Areas.

Der Import von ``homeassistant.components.bluetooth`` passiert erst beim Aufruf: so bleibt
das Paket (und damit ``model.py``) ohne den kompletten Bluetooth-Stack importierbar – die
Unit-Tests brauchen ihn nicht.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from habluetooth import BaseHaScanner


def current_scanners(hass: HomeAssistant) -> list[BaseHaScanner]:
    from homeassistant.components import bluetooth

    return bluetooth.async_current_scanners(hass)


def mac_offset(mac: str, offset: int) -> str:
    value = (int(mac.replace(":", ""), 16) + offset) % (1 << 48)
    raw = f"{value:012X}"
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


@dataclass
class ScannerInfo:
    source: str
    name: str
    area_id: str | None


@callback
def scanner_device(hass: HomeAssistant, source: str) -> dr.DeviceEntry | None:
    """Gerät eines Scanners in der Registry.

    Scanner melden ihre BLE-MAC; das Gerät hängt oft an der WLAN-MAC (ESP/Shelly: BLE = WLAN
    + 2). Daher wie Bermuda ein paar Offsets probieren, Geräte mit Area bevorzugt.
    """
    if ":" not in source:
        return None
    registry = dr.async_get(hass)
    candidates: list[dr.DeviceEntry] = []
    for offset in (0, -2, 2, -1, 1, -3, 3):
        mac = mac_offset(source, offset)
        for device in registry.async_get_devices(
            connections={(dr.CONNECTION_BLUETOOTH, mac), (dr.CONNECTION_NETWORK_MAC, mac.lower())}
        ):
            if device not in candidates:
                candidates.append(device)
    if not candidates:
        return None

    def rank(device: dr.DeviceEntry) -> tuple[bool, bool]:
        # Das eigentliche Gerät (Shelly/ESPHome, WLAN-MAC) vor dem Bluetooth-Scanner-Gerät,
        # jeweils mit Area bevorzugt.
        has_network = any(kind == dr.CONNECTION_NETWORK_MAC for kind, _ in device.connections)
        return (not has_network, device.area_id is None)

    return min(candidates, key=rank)


@callback
def scanner_infos(hass: HomeAssistant) -> dict[str, ScannerInfo]:
    """Alle aktiven Scanner mit lesbarem Namen und Area."""
    result: dict[str, ScannerInfo] = {}
    for scanner in current_scanners(hass):
        device = scanner_device(hass, scanner.source)
        name = (device.name_by_user or device.name) if device else None
        result[scanner.source] = ScannerInfo(
            source=scanner.source,
            name=name or scanner.name,
            area_id=device.area_id if device else None,
        )
    return result


@callback
def scanner_areas(hass: HomeAssistant) -> list[str]:
    """Areas, in denen Scanner stehen (Vorauswahl im Config Flow)."""
    areas: list[str] = []
    for info in scanner_infos(hass).values():
        if info.area_id and info.area_id not in areas:
            areas.append(info.area_id)
    return areas
