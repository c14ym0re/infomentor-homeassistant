"""Diagnostik: maskering och gränsfall (post utan laddning)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.infomentor.const import DOMAIN  # noqa: E402
from custom_components.infomentor.diagnostics import (  # noqa: E402
    async_get_config_entry_diagnostics,
)


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"refresh_token": "hemlig-token"},
    )
    entry.add_to_hass(hass)
    return entry


async def test_diagnostics_on_an_unloaded_entry_does_not_crash(hass: HomeAssistant) -> None:
    """Posten saknar runtime_data (t.ex. misslyckat setup) — kraschar inte, maskerar token."""
    entry = _entry(hass)
    assert not hasattr(entry, "runtime_data")

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["loaded"] is False
    assert result["entry"]["data"]["refresh_token"] == "**REDACTED**"


def _pupil() -> SimpleNamespace:
    return SimpleNamespace(
        pupil_id="42",
        name="Olofsson, Anna",
        lessons=[],
        calendar=[],
        tasks=[],
        learnlog=[],
        plans=[],
        attendance={},
    )


async def test_diagnostics_redacts_child_names(hass: HomeAssistant) -> None:
    """Barnens namn är personuppgifter och får inte hamna i diagnostikfilen."""
    entry = _entry(hass)
    data = SimpleNamespace(pupils=[_pupil()], lunch=[], notifications=[], news=[])
    coordinator = SimpleNamespace(
        data=data, last_update_success=True, last_exception=None
    )
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["loaded"] is True
    (pupil,) = result["pupils"]
    assert pupil["name"] == "REDACTED"
    assert pupil["id"] == "42"
    # Tokenen maskas även när posten är laddad.
    assert result["entry"]["data"]["refresh_token"] == "**REDACTED**"
