"""MaNoUmi – BLE-Raumerkennung per Fingerprint."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_TYPE,
    DATA_HUB,
    DATA_TRACKERS,
    DOMAIN,
    PLATFORMS,
    SIGNAL_ROOMS_CHANGED,
    TYPE_DEVICE,
    TYPE_HUB,
)
from .hub import ManoumiHub
from .tracker import ManoumiTracker

type ManoumiConfigEntry = ConfigEntry[ManoumiTracker | ManoumiHub]

HUB_PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


def entry_type(entry: ConfigEntry) -> str:
    return entry.data.get(CONF_TYPE, TYPE_DEVICE)


async def async_setup_entry(hass: HomeAssistant, entry: ManoumiConfigEntry) -> bool:
    """Eintrag einrichten (Gerät oder Haus)."""
    data = hass.data.setdefault(DOMAIN, {DATA_TRACKERS: {}})
    if entry_type(entry) == TYPE_HUB:
        hub = ManoumiHub(hass, entry)
        await hub.async_load()
        entry.runtime_data = hub
        data[DATA_HUB] = hub
        await hass.config_entries.async_forward_entry_setups(entry, HUB_PLATFORMS)
        hub.async_start()
    else:
        tracker = ManoumiTracker(hass, entry)
        await tracker.async_load()
        entry.runtime_data = tracker
        data[DATA_TRACKERS][entry.entry_id] = tracker
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _async_drop_own_device(hass, entry, tracker)
        tracker.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


def _async_drop_own_device(
    hass: HomeAssistant, entry: ConfigEntry, tracker: ManoumiTracker
) -> None:
    """Hängen die Entities am Private-BLE-Gerät, das eigene (alte) Gerät entfernen."""
    if (DOMAIN, entry.entry_id) in (tracker.device_info().get("identifiers") or set()):
        return
    registry = dr.async_get(hass)
    if device := registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id):
        registry.async_remove_device(device.id)


async def _async_options_updated(hass: HomeAssistant, entry: ManoumiConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ManoumiConfigEntry) -> bool:
    """Eintrag entladen, Daten sofort sichern."""
    is_hub = entry_type(entry) == TYPE_HUB
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, HUB_PLATFORMS if is_hub else PLATFORMS
    )
    if unloaded:
        await entry.runtime_data.async_stop()
        data = hass.data.get(DOMAIN, {})
        if is_hub:
            data.pop(DATA_HUB, None)
        else:
            data.get(DATA_TRACKERS, {}).pop(entry.entry_id, None)
            async_dispatcher_send(hass, SIGNAL_ROOMS_CHANGED)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ManoumiConfigEntry) -> None:
    """Gespeicherte Daten beim Löschen mit entfernen."""
    owner = ManoumiHub(hass, entry) if entry_type(entry) == TYPE_HUB else ManoumiTracker(hass, entry)
    await owner.async_remove_storage()
