"""Koordinatorns inloggning: vilka fel som betyder om-auth, och en enda innehavare av den roterande tokenen."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("homeassistant")

from homeassistant.exceptions import ConfigEntryAuthFailed  # noqa: E402
from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.infomentor.api import CannotConnect, InvalidAuth, RevokedAuth  # noqa: E402
from custom_components.infomentor.const import DOMAIN  # noqa: E402
from custom_components.infomentor.coordinator import InfomentorCoordinator  # noqa: E402


def make_coordinator(hass, data):
    """En koordinator med mockat api och en enkel post, utan Home Assistants uppsättning."""
    coord = InfomentorCoordinator.__new__(InfomentorCoordinator)
    coord.hass = hass
    coord.entry = SimpleNamespace(data=data)
    coord.api = MagicMock()
    coord._lock = asyncio.Lock()
    return coord


TOKEN_ENTRY = {"refresh_token": "old"}


@pytest.mark.parametrize(
    ("data", "failure", "expected"),
    [
        (TOKEN_ENTRY, RevokedAuth("dead"), ConfigEntryAuthFailed),
        (TOKEN_ENTRY, CannotConnect("down or blocked"), UpdateFailed),  # aldrig om-auth
        ({"username": "u", "password": "p"}, InvalidAuth("wrong password"), ConfigEntryAuthFailed),
        ({"username": "u", "password": "p"}, CannotConnect("down"), UpdateFailed),
    ],
    ids=["token-revoked", "token-blocked", "password-wrong", "password-down"],
)
async def test_a_failed_login_is_reauth_only_when_the_credential_is_dead(
    hass, data, failure, expected
) -> None:
    coord = make_coordinator(hass, data)
    coord.api.async_login_with_token = AsyncMock(side_effect=failure)
    coord.api.async_login = AsyncMock(side_effect=failure)
    with pytest.raises(expected):
        await coord._login()


async def test_password_mode_never_touches_the_token_flow(hass) -> None:
    coord = make_coordinator(hass, {"username": "u", "password": "p"})
    coord.api.async_login = AsyncMock(return_value=[{"id": "1"}])
    await coord._login()
    coord.api.async_login.assert_awaited_once()
    coord.api.async_login_with_token.assert_not_called()


async def test_a_password_entry_gives_the_api_its_credentials(hass) -> None:
    """Koordinatorn byggs på riktigt här: api:t får användarnamn och lösenord ur posten."""
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "u@example.com", "password": "p"})
    entry.add_to_hass(hass)
    coord = InfomentorCoordinator(hass, entry, MagicMock())
    assert (coord.api._username, coord.api._password) == ("u@example.com", "p")


def _rotating_api(coord):
    """Ett api med engångstoken: det avvisar en token som redan roterats."""
    used: list[str] = []
    alive = {"old"}

    async def login(refresh_token, on_rotated=None):
        await asyncio.sleep(0.01)  # långsamt nät: den andra inloggningen hinner läsa tokenen
        used.append(refresh_token)
        if refresh_token not in alive:
            raise RevokedAuth("consumed")
        alive.discard(refresh_token)
        new = f"rotated-{len(used)}"
        alive.add(new)
        on_rotated(new)
        return [{"id": "1"}]

    coord.api.async_login_with_token = login
    coord.hass.config_entries.async_update_entry = lambda entry, data: setattr(entry, "data", data)
    return used


async def test_the_rotated_token_is_stored_before_the_login_continues(hass) -> None:
    coord = make_coordinator(hass, dict(TOKEN_ENTRY))
    stored_during_login = []

    async def login(refresh_token, on_rotated=None):
        on_rotated("rotated")
        stored_during_login.append(coord.entry.data["refresh_token"])
        raise CannotConnect("fails after the rotation")

    coord.api.async_login_with_token = login
    coord.hass.config_entries.async_update_entry = lambda entry, data: setattr(entry, "data", data)
    with pytest.raises(UpdateFailed):
        await coord._login()
    assert stored_during_login == ["rotated"]
    assert coord.entry.data["refresh_token"] == "rotated"  # nästa försök använder den nya tokenen


async def test_two_overlapping_logins_never_use_the_same_token_twice(hass) -> None:
    coord = make_coordinator(hass, dict(TOKEN_ENTRY))
    used = _rotating_api(coord)
    await asyncio.gather(coord._login(), coord._login())
    assert used == ["old", "rotated-1"]
