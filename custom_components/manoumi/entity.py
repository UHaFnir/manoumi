"""Gemeinsame Basis der MaNoUmi-Entities."""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .hub import ManoumiHub
from .tracker import ManoumiTracker


class ManoumiEntity(Entity):
    """Aktualisiert sich bei jedem Fenster des Besitzers (Gerät oder Haus)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, owner: Any, key: str, unique_suffix: str | None = None) -> None:
        self.owner = owner
        self._attr_unique_id = f"{owner.entry.entry_id}_{unique_suffix or key}"
        self._attr_translation_key = key
        if isinstance(owner, ManoumiTracker):
            self._attr_device_info = owner.device_info()
        else:
            self._attr_device_info = hub_device_info(owner)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.owner.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class TrackerEntity(ManoumiEntity):
    owner: ManoumiTracker

    @property
    def tracker(self) -> ManoumiTracker:
        return self.owner


def hub_device_info(hub: ManoumiHub) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, hub.entry.entry_id)},
        name=hub.entry.title,
        manufacturer="MaNoUmi",
        entry_type=DeviceEntryType.SERVICE,
    )
