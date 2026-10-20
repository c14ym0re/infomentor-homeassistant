"""web.py: cookiejaren, `fetch`, `follow` och beviset att cookies skickas oförändrade."""

from __future__ import annotations

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.infomentor.api import new_raw_cookie_session
from custom_components.infomentor.token_client import make_aiohttp_request
from custom_components.infomentor.web import CookieJar, fetch, follow, form_action
from tests.fakes import FakeResponse, Router

PAGE = "https://login003.stockholm.se/a/b"


def test_a_cookie_is_sent_to_its_domain_and_path_only() -> None:
    jar = CookieJar()
    jar.set_from_header("A=1; Domain=.stockholm.se; Path=/a", PAGE)
    jar.set_from_header("B=2; Path=/", PAGE)
    assert jar.cookie_header("https://login001.stockholm.se/a/x") == "A=1"
    assert jar.cookie_header("https://login003.stockholm.se/a/x") == "A=1; B=2"
    assert jar.cookie_header("https://login003.stockholm.se/other") == "B=2"
    assert jar.cookie_header("https://example.com/a") == ""


def test_secure_cookies_stay_off_plain_http_and_max_age_zero_expires() -> None:
    jar = CookieJar()
    jar.set_from_header("S=1; Secure; Path=/", PAGE)
    jar.set_from_header("Gone=1; Max-Age=0; Path=/", PAGE)
    assert jar.cookie_header("http://login003.stockholm.se/") == ""
    assert jar.cookie_header(PAGE) == "S=1"


async def test_fetch_sends_the_jar_cookies_and_keeps_the_ones_that_come_back() -> None:
    jar = CookieJar()
    jar.set_from_header("Sess=ab/cd+ef==; Path=/", PAGE)
    router = Router().add("GET", "/a/b", FakeResponse(200, "ok", set_cookies=["New=1; Path=/"]))
    page = await fetch(router, jar, "GET", PAGE, headers={"Accept": "application/json"})
    sent = router.calls[0].headers
    assert sent["Cookie"] == "Sess=ab/cd+ef=="  # oförändrad
    assert sent["Accept"] == "application/json"  # anroparens header vinner
    assert jar.has("New")
    assert (page.status, page.text, page.url) == (200, "ok", PAGE)


async def test_fetch_sends_the_url_exactly_as_the_server_wrote_it() -> None:
    """yarl skriver om %2D till "-" och %3a%2f%2f till ://, vilket SiteMinders query avvisar."""
    url = "https://login003.stockholm.se/x?REALMOID=06-0a%2Dc&TARGET=-SM-HTTPS%3a%2f%2fsso%2ese%2f"
    router = Router().add("GET", "/x", FakeResponse())
    await fetch(router, CookieJar(), "GET", url)
    assert router.calls[0].url == url


async def test_follow_carries_cookies_across_redirects() -> None:
    router = (
        Router()
        .add("GET", "/start", FakeResponse(302, location="/next", set_cookies=["C=1; Path=/"]))
        .add("GET", "/next", lambda call: FakeResponse(200, call.headers.get("Cookie", "")))
    )
    page = await follow(router, CookieJar(), "https://h.example/start", error=ValueError)
    assert page.text == "C=1"
    assert page.url == "https://h.example/next"


@pytest.mark.parametrize(
    "router",
    [
        Router().add("GET", "/", FakeResponse(302)),  # ingen Location
        Router().add("GET", "/", FakeResponse(302, location="/")),  # loop
    ],
    ids=["no-location", "loop"],
)
async def test_follow_raises_the_callers_error_on_a_broken_redirect(router) -> None:
    with pytest.raises(ValueError):
        await follow(router, CookieJar(), "https://h.example/", error=ValueError)


def test_form_action_is_resolved_against_the_page() -> None:
    assert (
        form_action('<form action="/post" method="x">', "https://h.example/a/b")
        == "https://h.example/post"
    )
    assert form_action("<p>no form</p>", "https://h.example/") is None


# ---- genom riktiga aiohttp -------------------------------------------------------------
COOKIE = "Sess=ab/cd+ef=="


async def _protected_status(session: aiohttp.ClientSession) -> int:
    """En lokal server som godtar sessionscookien bara om den kommer fram oförändrad."""

    async def set_cookie(request):
        response = web.Response(text="ok")
        response.headers.add("Set-Cookie", f"{COOKIE}; Path=/")
        return response

    async def protected(request):
        if request.headers.get("Cookie") == COOKIE:
            raise web.HTTPFound("/assertion")
        return web.Response(text="the BankID page again")  # 200, som i verkligheten

    app = web.Application()
    app.router.add_get("/set", set_cookie)
    app.router.add_get("/protected", protected)
    server = TestServer(app)
    await server.start_server()
    try:
        do_request, jar = make_aiohttp_request(session), CookieJar()
        await fetch(do_request, jar, "GET", str(server.make_url("/set")))
        page = await fetch(do_request, jar, "GET", str(server.make_url("/protected")))
        return page.status
    finally:
        await session.close()
        await server.close()


@pytest.mark.usefixtures("socket_enabled")
async def test_the_raw_session_delivers_the_session_cookie_unchanged() -> None:
    assert await _protected_status(new_raw_cookie_session()) == 302


@pytest.mark.usefixtures("socket_enabled")
async def test_an_ordinary_cookie_jar_session_re_quotes_it_and_breaks_the_login() -> None:
    """Sett live: aiohttp skickade Sess="ab/cd+ef==", servern svarade 200 i stället för 302."""
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    assert await _protected_status(session) == 200
