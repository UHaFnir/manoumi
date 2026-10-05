"""Statusseiten (Markdown) für die Options-Dialoge – Scanner-Zustand, Kalibrierung, Drift."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar

from .scanners import ScannerInfo, current_scanners, scanner_infos

if TYPE_CHECKING:
    from .hub import ManoumiHub
    from .tracker import ManoumiTracker

_STATUS_ICON = {"ok": "✅", "nachkalibrieren": "⚠️", "nicht_kalibriert": "⬜"}

SCANNER_OK = 60  # s seit letztem empfangenen Advert
SCANNER_WARN = 300


def _age_icon(age: float) -> str:
    if age < SCANNER_OK:
        return "✅"
    if age < SCANNER_WARN:
        return "⚠️"
    return "❌"


def _age_text(age: float) -> str:
    if age < 90:
        return f"{age:.0f} s"
    if age < 5400:
        return f"{age / 60:.0f} min"
    return f"{age / 3600:.1f} h"


def _area_name(hass: HomeAssistant, area_id: str | None) -> str:
    if area_id is None:
        return "–"
    area = ar.async_get(hass).async_get_area(area_id)
    return area.name if area else area_id


def scanner_table(
    hass: HomeAssistant,
    infos: dict[str, ScannerInfo],
    heard: dict[str, float] | None = None,
    drift: dict[str, float] | None = None,
) -> str:
    rows = []
    for scanner in sorted(current_scanners(hass), key=lambda s: infos.get(s.source, s).name):
        info = infos.get(scanner.source)
        age = scanner.time_since_last_detection()
        cells = [
            _age_icon(age),
            info.name if info else scanner.name,
            _area_name(hass, info.area_id if info else None),
            _age_text(age),
        ]
        if heard is not None:
            rssi = heard.get(scanner.source)
            cells.append("–" if rssi is None else f"{rssi:.0f} dBm")
        if drift is not None:
            d = drift.get(scanner.source)
            cells.append("–" if d is None else f"{d:+.1f} dB")
        rows.append("| " + " | ".join(cells) + " |")
    header = ["", "Scanner", "Raum", "zuletzt aktiv"]
    if heard is not None:
        header.append("hört Gerät")
    if drift is not None:
        header.append("Drift")
    lines = [
        f"**Scanner ({len(rows)})**",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *rows,
    ]
    return "\n".join(lines)


def device_status(hass: HomeAssistant, tracker: ManoumiTracker) -> str:
    infos = scanner_infos(hass)
    window = tracker.last_window
    heard = window.rssi if window else {}
    summary = tracker.model.summary()
    rows = []
    order = {"nachkalibrieren": 0, "ok": 1, "nicht_kalibriert": 2}
    for area in sorted(tracker.areas, key=lambda a: order[tracker.room_status(a)[0]]):
        info = summary.get(area)
        conf = tracker.confusion.get(area, {}) if len(tracker.confusion) > 1 else {}
        rival = next(((r, p) for r, p in conf.items() if r != area), None)
        rows.append(
            "| "
            + " | ".join(
                [
                    _STATUS_ICON[tracker.room_status(area)[0]] + " " + tracker.room_status(area)[1],
                    tracker.area_name(area),
                    str(info["positionen"]) if info else "–",
                    str(info["kalibrierfenster"]) if info else "nicht kalibriert",
                    f"{info['gelernte_fenster']:.0f}" if info else "–",
                    f"{conf[area]:.0%}" if area in conf else "–",
                    f"{tracker.sequence[area]:.0%}" if conf and area in tracker.sequence else "–",
                    f"{tracker.area_name(rival[0])} ({rival[1]:.0%})" if rival else "–",
                ]
            )
            + " |"
        )
    state = "nicht da" if tracker.away else (tracker.area_name(tracker.room) if tracker.room else "unbekannt")
    return "\n".join(
        [
            f"**Aktuell:** {state}",
            "",
            "**Räume**",
            "",
            "| Status | Raum | Positionen | Kalibrierfenster | gelernt | Trennschärfe einzeln | im Verlauf | verwechselt mit |",
            "|---|---|---|---|---|---|---|---|",
            *rows,
            "",
            scanner_table(hass, infos, heard=heard),
        ]
    )


def hub_status(hass: HomeAssistant, hub: ManoumiHub, infos: dict[str, ScannerInfo]) -> str:
    anchors = hub.anchor_addresses()
    anchor_lines = [
        f"- {name}: von {hub.anchor_seen.get(addr, 0)} Scannern gehört" for addr, name in anchors.items()
    ] or ["- keine – Referenzgeräte unten auswählen (fest stehende BLE-Geräte, z.B. Thermometer)"]
    return "\n".join(
        [
            "**Referenzgeräte**",
            "",
            *anchor_lines,
            "",
            scanner_table(hass, infos, drift=hub.drift),
        ]
    )
