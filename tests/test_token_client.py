"""token_client.py: utfall vid refresh, parkoppling, oauth_token-steget och bryggan till IMHome."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.infomentor.token_client import (
    Blocked,
    NetworkError,
    TokenError,
    TokenRevoked,
    bridge_web_session,
    make_aiohttp_request,
    pair_app_token,
    parse_refresh_response,
    refresh_app_token,
    submit_oauth_token,
)
from custom_components.infomentor.web import CookieJar
from tests.fakes import FakeResponse, Router

JSON = {"Content-Type": "application/json;charset=UTF-8"}
IMHOME = "IMHome=1; Domain=.infomentor.se; Path=/"
TOKEN = {"access_token": "a", "refresh_token": "rt2"}


# ---- utfallet bedöms efter svarets form, inte statuskoden ------------------------------------
@pytest.mark.parametrize(
    ("status", "content_type", "body", "outcome"),
    [
        (200, "application/json", json.dumps(TOKEN), "rt2"),
        (200, "application/json; charset=utf-8", json.dumps(TOKEN), "rt2"),
        (200, "application/json", '{"error":"invalid_grant"}', TokenRevoked),
        (400, "application/json", '{"error":"invalid_grant"}', TokenRevoked),
        (400, "text/html", "<html>Bad Request</html>", Blocked),  # F5 framför endpointen
        (200, "text/html", "", Blocked),
        (200, "application/json", "{broken", Blocked),
        (200, "application/json", '{"x":1}', Blocked),
    ],
)
def test_the_outcome_follows_the_shape_of_the_answer(status, content_type, body, outcome) -> None:
    if isinstance(outcome, str):
        assert parse_refresh_response(status, content_type, body)["refresh_token"] == outcome
    else:
        with pytest.raises(outcome):
            parse_refresh_response(status, content_type, body)


# ---- refresh ----------------------------------------------------------------------------
async def test_refresh_sends_the_app_constants_and_returns_the_rotated_token() -> None:
    router = Router().add(
        "POST", "/OAuth2/Token", FakeResponse(200, json.dumps(TOKEN), headers=JSON)
    )
    token = await refresh_app_token(router, "old")
    body = router.calls[0].body
    assert "refresh_token=old" in body
    assert "client_id=notificationapp" in body
    assert "client_secret=NONE" in body
    assert token["refresh_token"] == "rt2"


@pytest.mark.parametrize(
    ("response", "raised"),
    [
        (FakeResponse(400, '{"error":"invalid_grant"}', headers=JSON), TokenRevoked),
        (FakeResponse(400, "<html>Bad Request</html>"), Blocked),  # aldrig skäl för om-auth
    ],
)
async def test_refresh_failures_split_dead_token_from_blocked_front_end(response, raised) -> None:
    with pytest.raises(raised):
        await refresh_app_token(Router().add("POST", "/OAuth2/Token", response), "old")


async def test_a_network_error_is_not_a_dead_token() -> None:
    async def down(method, url, body=None, headers=None):
        raise aiohttp.ClientConnectionError("down")

    with pytest.raises(NetworkError):
        await refresh_app_token(down, "old")


async def test_requests_are_made_without_following_redirects() -> None:
    session = MagicMock()
    session.request = AsyncMock(return_value="response")
    await make_aiohttp_request(session)("GET", "https://x.example/", None, {"A": "b"})
    assert session.request.await_args.kwargs["allow_redirects"] is False


# ---- parkoppling: webbsession -> appens refresh-token ------------------------------------
GET_AUTH_DATA = json.dumps({"authenticationUrl": "https://im.infomentor.se/x?authGuid=G-1&y=2"})


def _pairing(*, auth_data=(200, f"<pre>{GET_AUTH_DATA}</pre>"), code="a+b/c==", token_status=200):
    return (
        Router()
        .add("POST", "GetAuthenticationData", FakeResponse(*auth_data))
        .add(
            "GET",
            "LoginOAuth2",
            FakeResponse(302, location=f"InfomentorNotification://oauth2Callback/?code={code}"),
        )
        .add("POST", "/OAuth2/Token", FakeResponse(token_status, json.dumps(TOKEN)))
    )


async def test_pairing_sends_the_code_unencoded_and_the_session_cookie() -> None:
    jar = CookieJar()
    jar.set_from_header("IMHome=1; Domain=.infomentor.se; Path=/", "https://hub.infomentor.se")
    router = _pairing()
    token = await pair_app_token(router, jar, device_identifier="0123456789abcdef")
    auth_data, authorize, exchange = router.calls
    assert "IMHome=1" in auth_data.headers["Cookie"]
    assert "authGuid=G-1" in authorize.url and "DeviceIdentifier=0123456789abcdef" in authorize.url
    # Retrofit `encoded = true`: om koden URL-kodas ger servern HTTP 400.
    assert exchange.body.startswith("code=a+b/c==")
    assert token["refresh_token"] == "rt2"


@pytest.mark.parametrize(
    "router",
    [
        _pairing(auth_data=(302, "")),  # webbsessionen avvisad
        _pairing(auth_data=(200, "<pre>not json</pre>")),
        Router()
        .add("POST", "GetAuthenticationData", FakeResponse(200, GET_AUTH_DATA))
        .add("GET", "LoginOAuth2", FakeResponse(200)),  # ingen auktoriseringskod
        _pairing(token_status=400),
    ],
    ids=["session-rejected", "unparseable", "no-code", "token-refused"],
)
async def test_pairing_fails_loudly(router) -> None:
    with pytest.raises(TokenError):
        await pair_app_token(router, CookieJar(), device_identifier="0" * 16)


# ---- request-token-sidan -> POST -> LoginCallback ----------------------------------------
TOKEN_PAGE = (
    '<form action="https://infomentor.se/swedish/production/mentor/" method="post">'
    '<input type="hidden" name="oauth_token" value="a+b/c="></form>'
)


def _hub(
    *,
    page=TOKEN_PAGE,
    callback=None,
    submit_location="/cb",
):
    callback = callback or FakeResponse(200, set_cookies=[IMHOME])
    return (
        Router()
        .add("GET", "authentication/login", FakeResponse(200, page))
        .add("POST", "/mentor/", FakeResponse(302, location=submit_location))
        .add("GET", "/cb", callback)
    )


async def test_the_oauth_token_is_form_encoded_and_imhome_ends_up_in_the_jar() -> None:
    router = _hub()
    jar = await submit_oauth_token(
        router, CookieJar(), "https://hub.infomentor.se/authentication/login"
    )
    assert jar.has("IMHome")
    assert router.calls[1].body == "oauth_token=a%2Bb%2Fc%3D"


@pytest.mark.parametrize(
    "router",
    [
        _hub(page="<p>no token</p>"),
        _hub(submit_location=""),
        _hub(callback=FakeResponse(200)),  # kedjan slutar utan IMHome
    ],
    ids=["no-oauth-token", "no-callback-redirect", "no-imhome"],
)
async def test_the_hub_chain_fails_loudly(router) -> None:
    with pytest.raises(TokenError):
        await submit_oauth_token(
            router, CookieJar(), "https://hub.infomentor.se/authentication/login"
        )


# ---- bryggan: access-token -> IMHome ---------------------------------------------------
HANDOFF = '"https://infomentor.se/Swedish/Production/mentor/?login=remote&id=0Ab12"'


def _bridge(*, sso=None, handoff_status=302):
    sso = sso or FakeResponse(200, HANDOFF, headers=JSON)
    return (
        Router()
        .add("GET", "/NA1/Authentication/sso", sso)
        .add(
            "GET",
            "/Swedish/Production/mentor/",
            FakeResponse(
                handoff_status,
                location="https://hub.infomentor.se/authentication/login?forceOAuth=true",
            ),
        )
        .add("GET", "authentication/login", FakeResponse(200, TOKEN_PAGE))
        .add("POST", "/mentor/", FakeResponse(302, location="https://hub.infomentor.se/cb"))
        .add("GET", "/cb", FakeResponse(200, set_cookies=[IMHOME]))
    )


async def test_the_bridge_carries_the_bearer_on_every_hop_and_ends_with_imhome() -> None:
    router = _bridge()
    jar = await bridge_web_session(router, "ACCESS")
    assert jar.has("IMHome")
    assert all(call.headers.get("Authorization") == "Bearer ACCESS" for call in router.calls)


@pytest.mark.parametrize(
    "router",
    [
        _bridge(sso=FakeResponse(401)),
        _bridge(sso=FakeResponse(200, '"https://infomentor.se/other/"', headers=JSON)),
        _bridge(handoff_status=200),  # det avslutande snedstrecket försvann: svaret blir 200, inte 302
    ],
    ids=["token-rejected", "not-the-mentor-handoff", "lost-trailing-slash"],
)
async def test_the_bridge_names_the_hop_that_failed(router) -> None:
    with pytest.raises(TokenError):
        await bridge_web_session(router, "ACCESS")


async def test_an_empty_access_token_never_touches_the_network() -> None:
    router = Router()
    with pytest.raises(TokenError):
        await bridge_web_session(router, "")
    assert router.calls == []
