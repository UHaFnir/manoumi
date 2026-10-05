"""Optionale Aufzeichnung von Messfenstern als JSONL – für Offline-Auswertung und Tuning.

Jede Zeile ist ein Fenster: wer (Referenzgerät/verfolgtes Gerät), welches Label (Area des
Referenzgeräts bzw. gerade kalibrierter Raum, sonst erkannter Raum) und RSSI je Scanner.
Geschrieben wird gesammelt im Executor; ab MAX_BYTES wird in eine .1-Datei rotiert.
"""

from __future__ import annotations

import json
import os
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .model import Window

MAX_BYTES = 20 * 1024 * 1024


class CaptureWriter:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.path = hass.config.path("manoumi", "capture.jsonl")
        self._buffer: list[str] = []
        self._scheduled = False

    @callback
    def add(self, kind: str, ident: str, label: str | None, window: Window, **extra: Any) -> None:
        record = {
            "t": dt_util.utcnow().isoformat(timespec="seconds"),
            "kind": kind,
            "id": ident,
            "label": label,
            **window.to_dict(),
            **extra,
        }
        self._buffer.append(json.dumps(record, ensure_ascii=False))
        if not self._scheduled:
            self._scheduled = True
            self.hass.loop.call_later(5, self._flush)

    @callback
    def add_scanners(self, scanners: dict[str, Any]) -> None:
        """Kopfzeile mit Name und Area je Scanner – macht die Aufzeichnung lesbar."""
        record = {"t": dt_util.utcnow().isoformat(timespec="seconds"), "kind": "scanners", "scanners": scanners}
        self._buffer.append(json.dumps(record, ensure_ascii=False))

    @callback
    def _flush(self) -> None:
        lines, self._buffer, self._scheduled = self._buffer, [], False
        if lines:
            self.hass.async_add_executor_job(self._write, lines)

    def _write(self, lines: list[str]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
            os.replace(self.path, self.path + ".1")
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
