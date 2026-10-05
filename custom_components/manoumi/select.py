"""Kalibrier-Selects: Raum starten/stoppen, Modus Neu/Ergänzen."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ManoumiConfigEntry
from .const import CALIBRATION_DEFAULT_SECONDS, MODES, OPTION_OFF
from .entity import TrackerEntity
from .tracker import ManoumiTracker


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ManoumiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    tracker = entry.runtime_data
    if not isinstance(tracker, ManoumiTracker):
        return
    async_add_entities([CalibrateSelect(tracker), ModeSelect(tracker)])


class CalibrateSelect(TrackerEntity, SelectEntity):
    """Raum auswählen = Kalibrierung starten, „Aus“ = abbrechen."""

    def __init__(self, tracker) -> None:
        super().__init__(tracker, "kalibrieren")

    @property
    def options(self) -> list[str]:
        return [OPTION_OFF] + [self.tracker.area_name(a) for a in self.tracker.areas]

    @property
    def current_option(self) -> str:
        cal = self.tracker.calibration
        return self.tracker.area_name(cal.room) if cal else OPTION_OFF

    async def async_select_option(self, option: str) -> None:
        if option == OPTION_OFF:
            self.tracker.async_stop_calibration()
            return
        area_id = self.tracker.area_by_name(option)
        if area_id is not None:
            self.tracker.async_start_calibration(area_id, None, CALIBRATION_DEFAULT_SECONDS)


class ModeSelect(TrackerEntity, SelectEntity):
    """Neu = Raum komplett neu anlernen, Ergänzen = weitere Position im Raum."""

    _attr_options = MODES

    def __init__(self, tracker) -> None:
        super().__init__(tracker, "kalibriermodus")

    @property
    def current_option(self) -> str:
        return self.tracker.mode

    async def async_select_option(self, option: str) -> None:
        self.tracker.async_set_mode(option)
