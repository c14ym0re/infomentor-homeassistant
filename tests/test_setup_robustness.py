"""Det som gick fel vid första uppsättningen på en riktig HA."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")

from homeassistant import data_entry_flow  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402,F401
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.infomentor.api import ApiError, InvalidAuth  # noqa: E402
from custom_components.infomentor.const import DOMAIN  # noqa: E402


def _coordinator(hass: HomeAssistant):
    from custom_components.infomentor.coordinator import InfomentorCoordinator

    coord = InfomentorCoordinator.__new__(InfomentorCoordinator)
    coord._ok_calls = 0
    coord._auth_skips = 0
    coord._not_permitted = set()
    coord.hass = hass
    return coord


async def _rejected():
    raise InvalidAuth("/learnlog/learnlog/appData omdirigerade till HandleUnauthorizedRequest")


async def _ok():
    return ["data"]


async def test_a_rejected_section_is_skipped_when_other_calls_got_through(hass) -> None:
    """Alla barn och skolor har inte alla funktioner. Gick andra anrop igenom lever
    sessionen, och en ny inloggning hjälper inte (den avvisades dessutom direkt efter)."""
    coord = _coordinator(hass)
    assert await coord._safe("schema", _ok(), [], strict=True) == ["data"]
    assert await coord._safe("lärlogg", _rejected(), {"default": 1}, strict=True) == {"default": 1}
    assert coord._not_permitted == {"lärlogg"}


async def test_a_rejected_first_call_still_means_a_dead_session(hass) -> None:
    """Har inget gått igenom kan sessionen vara död: då loggar vi in igen."""
    coord = _coordinator(hass)
    with pytest.raises(InvalidAuth):
        await coord._safe("schema", _rejected(), [], strict=True)


async def test_the_not_permitted_warning_is_logged_once(hass, caplog) -> None:
    import logging

    coord = _coordinator(hass)
    await coord._safe("schema", _ok(), [], strict=True)
    with caplog.at_level(logging.DEBUG, logger="custom_components.infomentor.coordinator"):
        await coord._safe("lärlogg", _rejected(), {}, strict=True)
        await coord._safe("lärlogg", _rejected(), {}, strict=True)
    levels = [r.levelno for r in caplog.records if "lärlogg" in r.getMessage()]
    assert levels == [logging.WARNING, logging.DEBUG]


async def test_other_errors_are_still_skipped_not_fatal(hass) -> None:
    async def broken():
        raise ApiError("kaputt")

    coord = _coordinator(hass)
    assert await coord._safe("x", broken(), "dflt", strict=True) == "dflt"


# ---------------------------------------------------------------- update-lyssnaren
def _listener_entry(data, snapshot):
    return SimpleNamespace(
        data=data, entry_id="e1", runtime_data=SimpleNamespace(snapshot=snapshot)
    )


async def test_a_token_rotation_does_not_reload_the_entry(hass) -> None:
    from custom_components.infomentor import _async_update_listener, _without_token

    base = {"refresh_token": "t1"}
    entry = _listener_entry({**base, "refresh_token": "t2"}, _without_token(base))
    hass.config_entries.async_reload = AsyncMock()
    await _async_update_listener(hass, entry)
    hass.config_entries.async_reload.assert_not_called()


async def test_a_real_change_reloads_once_and_only_once(hass) -> None:
    from custom_components.infomentor import _async_update_listener, _without_token

    base = {"refresh_token": "t1"}
    entry = _listener_entry({**base, "idp": "other"}, _without_token(base))
    hass.config_entries.async_reload = AsyncMock()
    await _async_update_listener(hass, entry)
    await _async_update_listener(hass, entry)  # samma ändring igen: inget nytt att ladda om
    hass.config_entries.async_reload.assert_awaited_once_with("e1")


# ---------------------------------------------------------------- om-auth
@pytest.fixture
def token_entry(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"refresh_token": "dead", "idp": "stockholm_par"}
    )
    entry.add_to_hass(hass)
    return entry


async def test_reauth_of_a_token_entry_goes_straight_to_bankid(
    hass: HomeAssistant, token_entry
) -> None:
    async def never_scanned(*_):
        await asyncio.Event().wait()

    with patch("custom_components.infomentor.config_flow._validate_bankid", never_scanned):
        result = await token_entry.start_reauth_flow(hass)
    assert result["type"] == data_entry_flow.FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "bankid"
    hass.config_entries.flow.async_abort(result["flow_id"])
