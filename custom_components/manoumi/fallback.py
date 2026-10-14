"""Wo ist das Gerät, wenn Bluetooth schweigt? – reines Python, keine HA-Importe.

Der Private-BLE-Tracker hört dieselben Funkpakete wie MaNoUmi, nur mit mehr Geduld – er ist
kein zweiter Zeuge, sondern ein längerer Timeout und hält deshalb nur begrenzt. Ein
Anwesenheitssensor (z.B. `person.*` über die GPS-Ortung der App) ist unabhängig von
Bluetooth und entscheidet, sobald er etwas weiß.
"""

from __future__ import annotations

_NO_INFO = {None, "", "unknown", "unavailable"}


def silent_home(
    presence: str | None, ble_tracker: str | None, silent: float, hold: float
) -> bool:
    """True = „Zuhause“ (Raum unbekannt), False = „Nicht da“.

    presence: Zustand des Anwesenheitssensors (None = keiner hinterlegt), ble_tracker: Zustand des
    Private-BLE-Trackers, silent: Sekunden seit dem letzten Empfang.
    """
    if presence not in _NO_INFO:
        return presence == "home"
    return ble_tracker == "home" and silent < hold
