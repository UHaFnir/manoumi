"""Tests für „Zuhause“ / „Nicht da“, wenn Bluetooth schweigt."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.manoumi.const import AWAY_SECONDS, HOME_HOLD_SECONDS
from custom_components.manoumi.fallback import silent_home
from custom_components.manoumi.model import Window
from custom_components.manoumi.tracker import ManoumiTracker

HOLD = HOME_HOLD_SECONDS


@pytest.mark.parametrize(
    ("presence", "ble", "silent", "home"),
    [
        # Anwesenheitssensor weiß etwas → er entscheidet, ohne Zeitgrenze.
        ("home", "not_home", 3 * HOLD, True),
        ("not_home", "home", AWAY_SECONDS, False),
        ("Arbeit", "home", AWAY_SECONDS, False),  # andere Zone = nicht zuhause
        # Kein Sensor oder keine Auskunft → Private-BLE-Tracker, höchstens HOLD.
        (None, "home", HOLD - 1, True),
        (None, "home", HOLD, False),
        ("unavailable", "home", AWAY_SECONDS, True),
        ("unknown", "not_home", AWAY_SECONDS, False),
        (None, None, AWAY_SECONDS, False),
    ],
)
def test_silent_home(presence: str | None, ble: str | None, silent: float, home: bool) -> None:
    assert silent_home(presence, ble, silent, HOLD) is home


def _tracker(states: dict[str, str], presence: str | None) -> ManoumiTracker:
    hass = MagicMock()
    hass.states.get = lambda eid: SimpleNamespace(state=states[eid]) if eid in states else None
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.data = {"tracker": "device_tracker.ble"}
    entry.options = {"areas": ["a"], **({"presence": presence} if presence else {})}
    tracker = ManoumiTracker(hass, entry)
    tracker.room = tracker.last_room = "a"
    return tracker


SILENT = Window(rssi={}, active={"s1"})


@patch("custom_components.manoumi.tracker.async_dispatcher_send")
def test_tracker_holds_home_then_follows_presence(_send: MagicMock) -> None:
    states = {"device_tracker.ble": "not_home", "person.p": "home"}
    t = _tracker(states, "person.p")
    t._process_window(SILENT, AWAY_SECONDS - 1)
    assert (t.room, t.at_home, t.away) == ("a", False, False)  # noch nicht lange genug still

    t._process_window(SILENT, AWAY_SECONDS)
    assert (t.room, t.at_home, t.away) == (None, True, False)
    assert t.last_room == "a"

    states["person.p"] = "not_home"
    t._process_window(SILENT, AWAY_SECONDS + 6)
    assert (t.at_home, t.away) == (False, True)


@patch("custom_components.manoumi.tracker.async_dispatcher_send")
def test_tracker_without_presence_holds_at_most_hold(_send: MagicMock) -> None:
    t = _tracker({"device_tracker.ble": "home"}, None)
    t._process_window(SILENT, AWAY_SECONDS)
    assert (t.at_home, t.away) == (True, False)
    t._process_window(SILENT, HOLD)
    assert (t.at_home, t.away) == (False, True)
