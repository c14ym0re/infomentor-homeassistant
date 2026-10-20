"""InfoMentor-integration för Home Assistant."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_REFRESH_TOKEN
from .coordinator import InfomentorCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Sätt upp en config-post."""
    # Egen session med egen cookie-jar (inloggningen sätter cookies per domän).
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    coordinator = InfomentorCoordinator(hass, entry, session)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = InfomentorRuntimeData(
        coordinator=coordinator, session=session, snapshot=_without_token(entry.data)
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stäng ner en config-post."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.session.close()
    return unloaded


def _without_token(data: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k != CONF_REFRESH_TOKEN}


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ladda om när inställningarna ändras.

    En roterande refresh-token uppdateras vid varje inloggning och ska *inte* starta om
    integrationen, för det stänger den session vi just byggt. Jämför därför med det som
    gällde vid uppsättningen (utan token); alla andra ändringar laddar om, en gång.
    """
    runtime = getattr(entry, "runtime_data", None)
    now = _without_token(entry.data)
    if runtime is not None:
        if runtime.snapshot == now:
            return
        runtime.snapshot = now
    await hass.config_entries.async_reload(entry.entry_id)


@dataclass(slots=True)
class InfomentorRuntimeData:
    """Det som hänger på config-posten i minnet."""

    coordinator: InfomentorCoordinator
    session: aiohttp.ClientSession
    # Posten utan token vid uppsättningen: skiljer en token-rotation från en riktig ändring.
    snapshot: dict[str, Any]
