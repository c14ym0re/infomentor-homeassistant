"""BankID-drivern mot en skriptad Stockholm Stad-kedja.

Formerna följer den verkliga kedjan: metodvalet är en länk, BankID-sidans URL bär
SiteMinders query och används byte för byte, poll-JSON skickas som text/html, en oskannad
order dör med ERROR, och efter OK kommer SAML-intyget från en GET av sidans URL.
"""

from __future__ import annotations

import json

import pytest

from custom_components.infomentor.bankid import (
    BankIDDenied,
    BankIDError,
    BankIDPageChanged,
    BankIDTimeout,
    post_saml_response,
)
from custom_components.infomentor.bankid_stockholm import StockholmDriver
from tests.fakes import Call, FakeResponse, Router

START = "https://sso.infomentor.se/login.ashx?idp=stockholm_par"
SAML2SSO = "https://login001.stockholm.se/affwebservices/public/saml2sso?SAMLRequest=abc"
# SiteMinders query, med tecken som skadas av omkodning.
AUTH_URL = (
    "https://login003.stockholm.se/NECSadcmbid/authenticate/NECSadcmbid"
    "?TYPE=33554433&REALMOID=06-0a1b%2Dc&TARGET=-SM-HTTPS%3a%2f%2fsso%2einfomentor%2ese%2f"
)
ADC = "https://login003.stockholm.se/adc"
ASSERTION_PAGE = "https://login001.stockholm.se/saml2sso-ok"
CHOOSER = (
    '<a href="https://login003.stockholm.se/NECSadc/mbid/b64startpage.jsp'
    '?startpage=https%3A%2F%2Flogin001&amp;x=1">Mobilt BankID</a>'
)
ASSERTION = (
    '<form action="https://sso.infomentor.se/login.ashx?idp=stockholm_par" method="POST">'
    '<input type="hidden" name="SAMLResponse" value="ENCODED-ASSERT"></form>'
)
TEXT_HTML = {"content-type": "text/html;charset=UTF-8"}  # SERVERBUGG: JSON-svaret skickas med content-type text/html


class Chain(Router):
    """Hela kedjan; `states` är verifyorder-svaren i tur och ordning."""

    def __init__(
        self, states, *, chooser=CHOOSER, assertion=ASSERTION, poll_status=200, init_body=None
    ):
        super().__init__()
        self.states, self.initializes, self.approved = list(states), 0, False
        self.chooser, self.assertion = chooser, assertion
        self.poll_status, self.init_body = poll_status, init_body

    async def __call__(self, method, url, body=None, headers=None):
        url = str(url)
        self.calls.append(Call(method, url, body, headers or {}))
        if url == START:
            return FakeResponse(
                302, location=SAML2SSO, set_cookies=["ASP.NET_SessionId=s1; Path=/"]
            )
        if url == SAML2SSO:
            return FakeResponse(200, self.chooser)
        if "b64startpage.jsp" in url:
            return FakeResponse(302, location=AUTH_URL)
        if url == AUTH_URL and not self.approved:
            return FakeResponse(200, "<html>BankID</html>", set_cookies=["PhxAuthN=p1; Path=/"])
        if url == AUTH_URL:
            return FakeResponse(302, location=ADC)
        if url == ADC:
            return FakeResponse(
                302,
                location=ASSERTION_PAGE,
                set_cookies=["SMSESSION=sm1; Domain=.stockholm.se; Path=/"],
            )
        if url == ASSERTION_PAGE:
            return FakeResponse(200, self.assertion)
        if "initialize=bankid" in url:
            self.initializes += 1
            if self.init_body is not None:
                return FakeResponse(200, self.init_body, headers=TEXT_HTML)
            n = self.initializes
            payload = {"token": f"tok{n}", "order": f"ord{n}", "qrData": f"qr{n}.0"}
            return FakeResponse(
                200, json.dumps(payload), headers={"content-type": "application/json"}
            )
        if "verifyorder=" in url:
            state = self.states.pop(0)
            self.approved = self.approved or state == "OK"
            payload = {"state": state}
            if state in ("PENDING", "USER_SIGN"):
                payload["qrData"] = f"qr-{len(self.calls)}"
            return FakeResponse(self.poll_status, json.dumps(payload), headers=TEXT_HTML)
        raise AssertionError(f"unexpected call: {method} {url}")

    def polls(self):
        return [c for c in self.calls if "verifyorder=" in c.url]


def driver() -> StockholmDriver:
    return StockholmDriver(poll_interval=0)


async def test_login_walks_the_chain_and_returns_the_assertion() -> None:
    chain, steps = Chain(["PENDING", "USER_SIGN", "OK"]), []

    async def progress(step):
        steps.append((step.state, step.qr_data, step.autostart_token))

    result = await driver().run(chain, progress=progress)

    assert result.saml_response == "ENCODED-ASSERT"
    assert steps[0] == ("PENDING", "qr1.0", "tok1")
    assert "SCANNED" in [state for state, *_ in steps]
    assert steps[-1][0] == "APPROVED"


async def test_every_new_qr_is_reported_in_order() -> None:
    qrs = []

    async def progress(step):
        if step.qr_data:
            qrs.append(step.qr_data)

    await driver().run(Chain(["PENDING", "PENDING", "PENDING", "OK"]), progress=progress)
    assert len(qrs) == len(set(qrs)) == 4  # första orderns kod, sedan en per poll
    assert qrs[0] == "qr1.0"


async def test_api_calls_keep_siteminders_query_byte_for_byte() -> None:
    chain = Chain(["OK"])
    await driver().run(chain)
    init = next(c for c in chain.calls if "initialize=bankid" in c.url)
    poll = chain.polls()[0]
    assert init.url.startswith(AUTH_URL + "&initialize=bankid&_=")
    assert poll.url.startswith(AUTH_URL + "&verifyorder=ord1&_=")
    assert poll.headers["X-Requested-With"] == "XMLHttpRequest"


async def test_after_ok_the_assertion_comes_from_the_bare_page_url() -> None:
    chain = Chain(["OK"])
    await driver().run(chain)
    assert [c.url for c in chain.calls].count(AUTH_URL) == 2  # sidan, sedan 302 efter OK


async def test_cookies_accumulate_across_the_hops() -> None:
    chain = Chain(["OK"])
    result = await driver().run(chain)
    assert "SMSESSION=sm1" in chain.calls[-1].headers["Cookie"]
    assert result.jar.has("ASP.NET_SessionId") and result.jar.has("PhxAuthN")


# ---- en order som ingen skannar dör efter ~30 s -----------------------------------------
async def test_an_unscanned_order_is_restarted_with_a_new_one() -> None:
    chain = Chain(["PENDING", "ERROR", "PENDING", "OK"])
    result = await driver().run(chain)
    assert chain.initializes == 2
    assert "verifyorder=ord2" in chain.polls()[-1].url
    assert result.saml_response == "ENCODED-ASSERT"



async def test_an_error_after_a_scan_is_not_restarted() -> None:
    chain = Chain(["USER_SIGN", "ERROR"])
    with pytest.raises(BankIDError) as caught:
        await driver().run(chain)
    assert not isinstance(caught.value, BankIDTimeout)
    assert chain.initializes == 1


# ---- vad som inte är godkännande ------------------------------------------------------
async def test_cancelled_is_a_denial() -> None:
    with pytest.raises(BankIDDenied):
        await driver().run(Chain(["USER_SIGN", "CANCELLED"]))


@pytest.mark.parametrize("status", [500, 302], ids=["after-cancel", "after-ok"])
async def test_a_poll_answered_with_an_http_error_or_redirect_is_never_approval(status) -> None:
    chain = Chain(["PENDING"], poll_status=status)
    with pytest.raises(BankIDPageChanged):
        await driver().run(chain)
    assert not chain.approved


async def test_an_unknown_state_ends_the_login() -> None:
    with pytest.raises(BankIDError):
        await driver().run(Chain(["WAT"]))



async def test_the_saml_field_is_found_whatever_the_attribute_order() -> None:
    reordered = ASSERTION.replace(
        'name="SAMLResponse" value="ENCODED-ASSERT"', 'value="ENCODED-ASSERT" name="SAMLResponse"'
    )
    assert (
        await driver().run(Chain(["OK"], assertion=reordered))
    ).saml_response == "ENCODED-ASSERT"


# ---- sidorna slutar likna kedjan ------------------------------------------------------
@pytest.mark.parametrize(
    "chain",
    [
        Chain(["OK"], chooser="<form action='/mbid/start'></form>"),  # ingen BankID-länk
        Chain(["OK"], init_body="<html>BankID är nere</html>"),  # initialize svarar med html
        Chain(["OK"], assertion="<html>nothing</html>"),  # ingen SAML-form efter OK
        Chain(["OK"], assertion=ASSERTION.replace("sso.infomentor.se", "evil.example.com")),
    ],
    ids=["no-bankid-link", "initialize-html", "no-saml-form", "saml-form-for-another-host"],
)
async def test_a_changed_page_is_reported_as_such(chain) -> None:
    with pytest.raises(BankIDPageChanged):
        await driver().run(chain)


# ---- båda faserna ihop, med hubbens riktiga omdirigeringar ------------------------------------
async def test_the_whole_login_ends_with_imhome_and_the_session_cookie_reaches_the_saml_post() -> (
    None
):
    chain = Chain(["PENDING", "USER_SIGN", "OK"])
    hub = (
        Router()
        .add("POST", "login.ashx", FakeResponse(302, location="https://sso.infomentor.se/Login"))
        .add(
            "GET",
            "/Login",
            FakeResponse(302, location="https://hub.infomentor.se/authentication/login"),
        )
        .add(
            "GET",
            "authentication/login",
            FakeResponse(
                200,
                '<form action="https://infomentor.se/m/"><input type="hidden" name="oauth_token" value="t+1"></form>',
            ),
        )
        .add("POST", "/m/", FakeResponse(302, location="https://hub.infomentor.se/cb"))
        .add(
            "GET", "/cb", FakeResponse(200, set_cookies=["IMHome=1; Domain=.infomentor.se; Path=/"])
        )
    )

    result = await driver().run(chain)
    jar = await post_saml_response(
        hub, saml_response="a+b/c=", jar=result.jar, idp="stockholm_par"
    )

    saml_post = hub.calls[0]
    assert jar.has("IMHome")
    assert "ASP.NET_SessionId=s1" in saml_post.headers["Cookie"]  # från första fasen
    assert saml_post.body == "SAMLResponse=a%2Bb%2Fc%3D"
    assert hub.calls[-2].body == "oauth_token=t%2B1"
