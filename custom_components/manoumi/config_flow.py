"""Einrichtung: Gerät (Tracker + Räume) oder Haus (Referenzgeräte, Personenzählung)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    AreaSelector,
    AreaSelectorConfig,
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_ANCHORS,
    CONF_AREAS,
    CONF_CAPTURE,
    CONF_NOTIFY_SERVICE,
    CONF_TRACKER,
    CONF_TYPE,
    DOMAIN,
    HUB_UNIQUE_ID,
    TYPE_DEVICE,
    TYPE_HUB,
)
from .scanners import current_scanners, scanner_areas, scanner_infos
from .status import device_status, hub_status


def _tracker_selector() -> EntitySelector:
    return EntitySelector(
        EntitySelectorConfig(domain="device_tracker", integration="private_ble_device")
    )


def _areas_selector() -> AreaSelector:
    return AreaSelector(AreaSelectorConfig(multiple=True))


def _notify_selector(hass: HomeAssistant) -> SelectSelector:
    """notify-Dienste zur Auswahl (z.B. Push aufs Handy), eigener Wert erlaubt."""
    services = sorted(f"notify.{name}" for name in hass.services.async_services_for_domain("notify"))
    return SelectSelector(
        SelectSelectorConfig(options=services, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
    )


def _anchors_selector() -> DeviceSelector:
    return DeviceSelector(DeviceSelectorConfig(multiple=True))


def _device_options(user_input: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {CONF_AREAS: user_input[CONF_AREAS]}
    if user_input.get(CONF_NOTIFY_SERVICE):
        options[CONF_NOTIFY_SERVICE] = user_input[CONF_NOTIFY_SERVICE]
    return options


@callback
def _scanner_sources(hass: HomeAssistant) -> set[str]:
    return {s.source for s in current_scanners(hass)}


def _validate_anchors(hass: HomeAssistant, device_ids: list[str]) -> str | None:
    """Referenzgeräte brauchen eine BLE-Adresse und dürfen keine Scanner sein."""

    registry = dr.async_get(hass)
    scanners = _scanner_sources(hass)
    for device_id in device_ids:
        device = registry.async_get(device_id)
        macs = {v.upper() for k, v in device.connections if k == dr.CONNECTION_BLUETOOTH} if device else set()
        if not macs:
            return "anchor_no_ble"
        if macs & scanners:
            return "anchor_is_scanner"
    return None


class ManoumiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config Flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["device", "hub"])

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_TRACKER])
            self._abort_if_unique_id_configured()
            if not user_input[CONF_AREAS]:
                errors[CONF_AREAS] = "no_areas"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_TYPE: TYPE_DEVICE, CONF_TRACKER: user_input[CONF_TRACKER]},
                    options=_device_options(user_input),
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_TRACKER): _tracker_selector(),
                vol.Required(CONF_AREAS, default=scanner_areas(self.hass)): _areas_selector(),
                vol.Optional(CONF_NOTIFY_SERVICE): _notify_selector(self.hass),
            }
        )
        return self.async_show_form(step_id="device", data_schema=schema, errors=errors)

    async def async_step_hub(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(HUB_UNIQUE_ID)
        if self._async_current_ids(include_ignore=False) and HUB_UNIQUE_ID in self._async_current_ids():
            return self.async_abort(reason="already_hub")
        errors: dict[str, str] = {}
        if user_input is not None:
            anchors = user_input.get(CONF_ANCHORS, [])
            if (error := _validate_anchors(self.hass, anchors)) is not None:
                errors[CONF_ANCHORS] = error
            else:
                return self.async_create_entry(
                    title="MaNoUmi Haus",
                    data={CONF_TYPE: TYPE_HUB},
                    options={CONF_ANCHORS: anchors},
                )
        schema = vol.Schema({vol.Optional(CONF_ANCHORS, default=[]): _anchors_selector()})
        return self.async_show_form(step_id="hub", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        if config_entry.data.get(CONF_TYPE) == TYPE_HUB:
            return HubOptionsFlow()
        return DeviceOptionsFlow()


class DeviceOptionsFlow(OptionsFlow):
    """Status + Räume und Benachrichtigung eines Geräts."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_AREAS]:
                return self.async_create_entry(data=_device_options(user_input))
            errors[CONF_AREAS] = "no_areas"
        options = self.config_entry.options
        notify = options.get(CONF_NOTIFY_SERVICE)
        schema = vol.Schema(
            {
                vol.Required(CONF_AREAS, default=options.get(CONF_AREAS, [])): _areas_selector(),
                vol.Optional(
                    CONF_NOTIFY_SERVICE,
                    description={"suggested_value": notify} if notify else None,
                ): _notify_selector(self.hass),
            }
        )
        tracker = getattr(self.config_entry, "runtime_data", None)
        status = device_status(self.hass, tracker) if tracker is not None else ""
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"status": status},
        )


class HubOptionsFlow(OptionsFlow):
    """Status + Referenzgeräte des Hauses."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            anchors = user_input.get(CONF_ANCHORS, [])
            if (error := _validate_anchors(self.hass, anchors)) is None:
                return self.async_create_entry(
                    data={CONF_ANCHORS: anchors, CONF_CAPTURE: user_input.get(CONF_CAPTURE, False)}
                )
            errors[CONF_ANCHORS] = error
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ANCHORS, default=self.config_entry.options.get(CONF_ANCHORS, [])
                ): _anchors_selector(),
                vol.Optional(
                    CONF_CAPTURE, default=self.config_entry.options.get(CONF_CAPTURE, False)
                ): bool,
            }
        )
        hub = getattr(self.config_entry, "runtime_data", None)
        status = hub_status(self.hass, hub, scanner_infos(self.hass)) if hub is not None else ""
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"status": status},
        )
