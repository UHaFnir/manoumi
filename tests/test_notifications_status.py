"""Tests für Kalibrier-Benachrichtigungen und den Kalibrierbedarf je Raum (ohne laufendes HA)."""

from __future__ import annotations

import random
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.manoumi.notifications import CalibrationNotifier
from custom_components.manoumi.tracker import ManoumiTracker

from .test_model import PROFILES, calibrated_model, make_window


def _notifier(language: str = "de") -> tuple[CalibrationNotifier, list[tuple[str, str]]]:
    hass = MagicMock()
    hass.config.language = language
    notifier = CalibrationNotifier(hass, "entry", None)
    sent: list[tuple[str, str]] = []
    notifier._send = lambda title, message: sent.append((title, message))  # type: ignore[method-assign]
    return notifier, sent


def test_finished_reports_duration_separability_and_weakened_rooms() -> None:
    notifier, sent = _notifier()
    notifier.finished(
        "Arbeitszimmer", 12, 138, separability=1.0, smoothed=1.0, seconds=54, early=True,
        weakened=[("Flur", 0.8)],
    )
    title, message = sent[0]
    assert "Arbeitszimmer kalibriert" in title
    assert "54 s" in message and "vorzeitig" in message
    assert "im Verlauf" in message and "100%" in message
    assert "Flur (80%)" in message


def test_finished_recommends_second_position_only_for_single_position() -> None:
    notifier, sent = _notifier()
    notifier.finished("Galerie", 10, 90, separability=0.7, smoothed=0.8, positions=1,
                      rival=("Büro", 0.3), seconds=60)
    assert "zweite Position" in sent[-1][1]
    notifier.finished("Galerie", 10, 90, separability=0.7, smoothed=0.8, positions=2,
                      rival=("Büro", 0.3), seconds=60)
    assert "zweite Position" not in sent[-1][1]
    assert "zusätzlicher Scanner" in sent[-1][1]


def test_finished_without_signal_and_english() -> None:
    notifier, sent = _notifier("en-GB")
    notifier.finished("Kitchen", 0, 0)
    assert "not calibrated" in sent[0][0]


def test_room_status() -> None:
    rng = random.Random(20)
    model = calibrated_model(rng)
    twin = model.begin_calibration("buero_zwilling", replace=True)
    for _ in range(30):
        twin.add_calibration(make_window(rng, PROFILES["buero"]))
    thin = model.begin_calibration("klein", replace=True)
    thin.add_calibration(make_window(rng, PROFILES["schlafzimmer"]))
    fake = SimpleNamespace(
        model=model,
        confusion=model.confusion(),
        sequence=model.sequence_accuracy(),
        area_name=lambda a: a,
    )
    status = lambda area: ManoumiTracker.room_status(fake, area)[0]
    assert status("schlafzimmer") == "ok"
    assert status("buero_zwilling") == "nachkalibrieren"
    assert status("klein") == "nachkalibrieren"
    assert status("bad") == "nicht_kalibriert"
