"""Benachrichtigungen rund um die Kalibrierung.

Immer als persistente Benachrichtigung in HA (eine pro Eintrag, Start wird durch das Ergebnis
ersetzt), optional zusätzlich über einen notify-Dienst (z.B. Push aufs Handy) – die
Kalibrierung macht man ja mit dem Handy in der Hand, nicht vor dem Dashboard.
"""

from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant

from .const import MODE_REPLACE

_LOGGER = logging.getLogger(__name__)

# Unter so vielen Fenstern ist eine Kalibrierung zu dünn (bei 6-s-Fenstern ≈ 1 min Signal).
MIN_GOOD_WINDOWS = 10
# Unter diesem Anteil einzeln richtig erkannter Fenster gibt es einen Hinweis.
LOW_SEPARABILITY = 0.8

TEXTS = {
    "de": {
        "start_title": "MaNoUmi: Kalibrierung {room}",
        "start": (
            "Kalibrierung von **{room}** läuft – höchstens {minutes} min, "
            "früher fertig, sobald der Raum sicher erkannt wird ({mode}).\n\n"
            "- Bleib die ganze Zeit in diesem Raum.\n"
            "- Geh die Stellen ab, an denen du dich meist aufhältst (Tisch, Bett, Sofa …).\n"
            "- Handy mal in der Hand, mal in der Tasche, mal abgelegt – so, wie du es sonst trägst.\n"
            "- Tür so lassen, wie sie normalerweise steht.\n\n"
            "Du bekommst eine Nachricht, wenn die Kalibrierung fertig ist."
        ),
        "mode_replace": "Neu – ersetzt die bisherige Kalibrierung dieses Raums",
        "mode_add": "Ergänzen – weitere Position im Raum",
        "early": " (vorzeitig – der Raum war schnell sicher erkennbar)",
        "weakened": (
            "\n\n**Durch den neuen Raum schwächer erkennbar:** {rooms}. "
            "Nachkalibrieren mit Modus „Ergänzen“ empfohlen."
        ),
        "full_low": (
            "\n\nDie volle Zeit hat nicht gereicht, um den Raum sicher von {rival} zu trennen. "
            "Mehr Zeit an derselben Stelle hilft dabei kaum – besser eine **zweite Position** im Raum "
            "mit Modus „Ergänzen“ anlernen."
        ),
        "done_title": "MaNoUmi: {room} kalibriert",
        "done": (
            "Kalibrierung von **{room}** nach {duration} abgeschlossen{early}: {windows} Messfenster, "
            "im Schnitt {scanners} Scanner gehört.\n\n"
            "Nächster Raum: dort hingehen und unter „Kalibrieren“ auswählen."
        ),
        "separability": (
            "\n\n**Trennschärfe:** im Verlauf (so wie der Sensor entscheidet) {smooth}, "
            "einzelne Messfenster {share}{rival}."
        ),
        "rival": ", am ehesten verwechselt mit {rival} ({rival_share})",
        "separability_low": (
            " Das ist knapp – noch eine Position mit „Ergänzen“ anlernen oder {rival} neu kalibrieren. "
            "Bleibt es knapp, hilft ein zusätzlicher Scanner zwischen den beiden Räumen."
        ),
        "separability_single": "\n\nSobald ein zweiter Raum kalibriert ist, steht hier die Trennschärfe.",
        "thin": (
            "\n\n⚠️ Wenig Daten ({windows} Messfenster) – das Gerät wurde nur selten gehört. "
            "Kalibrierung dieses Raums besser wiederholen (Modus „Neu“)."
        ),
        "none_title": "MaNoUmi: {room} nicht kalibriert",
        "none": (
            "Während der Kalibrierung von **{room}** hat kein Scanner das Gerät gehört – "
            "es wurde nichts gespeichert. Ist Bluetooth am Gerät an und der Private-BLE-Tracker "
            "„zu Hause“?"
        ),
        "aborted_title": "MaNoUmi: Kalibrierung {room} abgebrochen",
        "aborted": (
            "Kalibrierung von **{room}** abgebrochen. Die {windows} bis dahin gesammelten "
            "Messfenster bleiben gespeichert; für ein sauberes Ergebnis den Raum neu kalibrieren."
        ),
    },
    "en": {
        "start_title": "MaNoUmi: calibrating {room}",
        "start": (
            "Calibration of **{room}** is running – at most {minutes} min, "
            "done earlier once the room is recognised reliably ({mode}).\n\n"
            "- Stay in this room the whole time.\n"
            "- Visit the spots where you usually are (desk, bed, sofa …).\n"
            "- Phone sometimes in hand, sometimes in a pocket, sometimes put down – as you usually carry it.\n"
            "- Leave the door as it normally is.\n\n"
            "You will get a message when the calibration is done."
        ),
        "mode_replace": "New – replaces this room's previous calibration",
        "mode_add": "Add – another position in the room",
        "early": " (early – the room was quickly recognisable)",
        "weakened": (
            "\n\n**Harder to recognise because of the new room:** {rooms}. "
            "Recalibrating with mode “Add” is recommended."
        ),
        "full_low": (
            "\n\nThe full time was not enough to separate the room reliably from {rival}. "
            "More time at the same spot hardly helps – better add a **second position** in the room "
            "with mode “Add”."
        ),
        "done_title": "MaNoUmi: {room} calibrated",
        "done": (
            "Calibration of **{room}** finished after {duration}{early}: {windows} windows, "
            "{scanners} scanners heard on average.\n\n"
            "Next room: go there and select it under “Calibrate”."
        ),
        "separability": (
            "\n\n**Separability:** over time (as the sensor decides) {smooth}, "
            "single windows {share}{rival}."
        ),
        "rival": ", most often confused with {rival} ({rival_share})",
        "separability_low": (
            " That is tight – add another position (“Add”) or recalibrate {rival}. "
            "If it stays tight, an extra scanner between the two rooms helps."
        ),
        "separability_single": "\n\nOnce a second room is calibrated, separability will be shown here.",
        "thin": (
            "\n\n⚠️ Little data ({windows} windows) – the device was rarely heard. "
            "Better repeat this room (mode “New”)."
        ),
        "none_title": "MaNoUmi: {room} not calibrated",
        "none": (
            "No scanner heard the device while calibrating **{room}** – nothing was stored. "
            "Is Bluetooth on and the Private BLE tracker “home”?"
        ),
        "aborted_title": "MaNoUmi: calibration of {room} aborted",
        "aborted": (
            "Calibration of **{room}** aborted. The {windows} windows collected so far are kept; "
            "recalibrate the room for a clean result."
        ),
    },
}


class CalibrationNotifier:
    """Schickt Start-/Ergebnis-Nachrichten einer Kalibrierung."""

    def __init__(self, hass: HomeAssistant, entry_id: str, notify_service: str | None) -> None:
        self.hass = hass
        self.notification_id = f"manoumi_{entry_id}_calibration"
        self.notify_service = notify_service or None

    def _texts(self) -> dict[str, str]:
        return TEXTS.get(self.hass.config.language.split("-")[0], TEXTS["en"])

    def started(self, room: str, mode: str, duration: float) -> None:
        t = self._texts()
        mode_text = t["mode_replace"] if mode == MODE_REPLACE else t["mode_add"]
        minutes = f"{duration / 60:g}"
        self._send(
            t["start_title"].format(room=room),
            t["start"].format(room=room, minutes=minutes, mode=mode_text),
        )

    def finished(
        self,
        room: str,
        windows: int,
        seen_total: int,
        separability: float | None = None,
        rival: tuple[str, float] | None = None,
        seconds: float = 0.0,
        early: bool = False,
        smoothed: float | None = None,
        positions: int = 1,
        weakened: list[tuple[str, float]] | None = None,
    ) -> None:
        t = self._texts()
        if windows == 0:
            self._send(t["none_title"].format(room=room), t["none"].format(room=room))
            return
        duration = f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.1f} min"
        message = t["done"].format(
            room=room,
            windows=windows,
            scanners=f"{seen_total / windows:.1f}",
            duration=duration,
            early=t["early"] if early else "",
        )
        if separability is None:
            message += t["separability_single"]
        else:
            rival_text = (
                t["rival"].format(rival=rival[0], rival_share=f"{rival[1]:.0%}") if rival else ""
            )
            smooth = separability if smoothed is None else smoothed
            message += t["separability"].format(
                share=f"{separability:.0%}", smooth=f"{smooth:.0%}", rival=rival_text
            )
            # Maßstab für Hinweise ist die geglättete Erkennung; einzelne Ausreißer sind normal.
            if rival and smooth < 0.95:
                if positions <= 1 and not early:
                    message += t["full_low"].format(rival=rival[0])
                else:
                    message += t["separability_low"].format(rival=rival[0])
        if weakened:
            rooms = ", ".join(f"{name} ({share:.0%})" for name, share in weakened)
            message += t["weakened"].format(rooms=rooms)
        if windows < MIN_GOOD_WINDOWS:
            message += t["thin"].format(windows=windows)
        self._send(t["done_title"].format(room=room), message)

    def aborted(self, room: str, windows: int) -> None:
        t = self._texts()
        self._send(
            t["aborted_title"].format(room=room), t["aborted"].format(room=room, windows=windows)
        )

    def _send(self, title: str, message: str) -> None:
        persistent_notification.async_create(
            self.hass, message, title=title, notification_id=self.notification_id
        )
        if not self.notify_service:
            return
        domain, _, service = self.notify_service.partition(".")
        if not service:
            domain, service = "notify", domain
        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning("Benachrichtigungsdienst %s nicht gefunden", self.notify_service)
            return
        # Push-Nachrichten haben kein Markdown – Sternchen entfernen.
        self.hass.async_create_task(
            self.hass.services.async_call(
                domain,
                service,
                {"title": title, "message": message.replace("**", "")},
                blocking=False,
            )
        )
