"""Buttons: Gelerntes zurücksetzen (Gerät), Ausgangslage neu aufnehmen (Haus)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ManoumiConfigEntry
from .entity import ManoumiEntity, TrackerEntity
from .hub import ManoumiHub
from .tracker import ManoumiTracker


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ManoumiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    owner = entry.runtime_data
    if isinstance(owner, ManoumiHub):
        async_add_entities([ResetDriftButton(owner)])
    else:
        async_add_entities([ResetLearnedButton(owner)])


class ResetLearnedButton(TrackerEntity, ButtonEntity):
    """Selbstgelerntes verwerfen, Kalibrierung bleibt."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, tracker: ManoumiTracker) -> None:
        super().__init__(tracker, "gelerntes_zuruecksetzen")

    async def async_press(self) -> None:
        self.tracker.async_reset_learned()


class ResetDriftButton(ManoumiEntity, ButtonEntity):
    """Ausgangslage der Referenzgeräte neu aufnehmen (z.B. nach dem Umstellen)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: ManoumiHub) -> None:
        super().__init__(hub, "ausgangslage_neu")

    async def async_press(self) -> None:
        self.owner.async_reset_drift()
