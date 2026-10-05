"""Diagnose-Download: Modell und letzte Messfenster."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import ManoumiConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ManoumiConfigEntry
) -> dict[str, Any]:
    return entry.runtime_data.diagnostics()
