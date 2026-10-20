"""Gemensamt för BankID-inloggning mot InfoMentors IdP:er.

Varje kommun har sin egen IdP (`sso.infomentor.se/login.ashx?idp=<värde>`) med egna sidor,
egen start av BankID-ordern, egen QR och egen polling. Det som är lika för alla ligger här:
felen, `BankIDStep`/`BankIDResult`, väntan på godkännande (`wait_for_approval`),
kontrollen av SAML-formen och `post_saml_response`.

En drivrutin (en klass per IdP, se `bankid_stockholm.py`) har
`async run(do_request, *, progress) -> BankIDResult`: den öppnar IdP:ns sidor, lämnar
väntan till `wait_for_approval` och slutar med `saml_result(sidan, jar)`. Den registreras
i `DRIVERS` i `api.py`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

from .const import SSO_LOGIN_PATH
from .token_client import TokenError, submit_oauth_token
from .util import hidden_inputs
from .web import CookieJar, DoRequest, Page, fetch, follow, form_action, host_and_path

_LOGGER = logging.getLogger(__name__)

SSO_BASE = "https://sso.infomentor.se"
MAX_POLL_SECONDS = 180.0
POLL_INTERVAL_SECONDS = 1.0  # QR-koden byter varje sekund; en äldre bild hinner inte skannas
MAX_RESTARTS = 3  # en oskannad QR-order dör efter ca 30 s

# Läget hos en BankID-order, så som `wait_for_approval` förstår det. Varje drivrutin
# översätter IdP:ns egna lägen hit.
PENDING = "PENDING"  # väntar på att QR-koden skannas
SCANNED = "SCANNED"  # skannad, användaren signerar
APPROVED = "APPROVED"
CANCELLED = "CANCELLED"  # användaren avbröt
EXPIRED = "EXPIRED"  # ordern dog (en oskannad QR-order gör det efter ca 30 s)


class BankIDError(Exception):
    """Basfel för BankID-inloggningen."""


class BankIDTimeout(BankIDError):
    """Tiden gick ut innan BankID-appen godkände. Be användaren försöka igen."""


class BankIDDenied(BankIDError):
    """Användaren avbröt BankID (avböjde) i stället för att godkänna."""


class BankIDPageChanged(BankIDError):
    """En IdP-sida såg annorlunda ut än förväntat."""


@dataclass
class BankIDStep:
    """Ett steg i inloggningen att visa i config-flödet."""

    message: str
    qr_data: str | None = None
    autostart_token: str | None = None
    state: str = PENDING


@dataclass
class BankIDResult:
    """Resultatet av BankID-inloggningen."""

    saml_response: str
    jar: CookieJar  # cookies från IdP-hoppen


async def wait_for_approval(
    start: Callable[[], Awaitable[tuple[str, str, str]]],
    poll: Callable[[str], Awaitable[tuple[str, str | None]]],
    *,
    progress: Any | None,
    poll_interval: float,
    timeout: float,
    max_restarts: int,
) -> None:
    """Väntar tills användaren godkänt en BankID-order; kastar annars.

    `start()` skapar en order och ger (order, autostart-token, qrData). `poll(order)` ger
    (läge, qrData eller None) där läget är PENDING/SCANNED/APPROVED/CANCELLED/EXPIRED; allt
    annat en IdP kan svara ska drivrutinen själv avvisa. Varje ny QR-bild och varje nytt
    läge rapporteras till `progress` som ett `BankIDStep`.
    """
    order, token, qr_data = await start()
    deadline = time.monotonic() + timeout
    restarts = 0
    scanned = False
    status = PENDING
    shown: tuple[str, str] | None = None
    logged = ""
    while True:
        if status in (PENDING, SCANNED) and (status, qr_data) != shown:
            shown = (status, qr_data)
            if progress:
                await progress(
                    BankIDStep(
                        "Skanna QR-koden i BankID-appen",
                        qr_data=qr_data,
                        autostart_token=token,
                        state=status,
                    )
                )
        if time.monotonic() > deadline:
            raise BankIDTimeout(f"Tiden gick ut efter {int(timeout)} s utan godkännande")
        await asyncio.sleep(poll_interval)
        status, new_qr = await poll(order)
        qr_data = new_qr or qr_data
        if status not in (PENDING, EXPIRED):
            scanned = True
        if status != logged:
            _LOGGER.debug("BankID-order %s: %s", order[:8], status)
            logged = status
        if status == EXPIRED and not scanned:
            # Är alla ordrar oskannade är det en tidsgräns, inte ett sidfel.
            if restarts >= max_restarts:
                raise BankIDTimeout(
                    f"ingen skannade QR-koden i {restarts + 1} order (var och en ca 30 s)"
                )
            restarts += 1
            _LOGGER.debug("Oskannad order gick ut, startar en ny (%d/%d)", restarts, max_restarts)
            order, token, qr_data = await start()
            status = PENDING
        elif status == APPROVED:
            break
        elif status == CANCELLED:
            raise BankIDDenied("BankID-appen avbröt inloggningen")
        elif status == EXPIRED:
            raise BankIDError("BankID-ordern avbröts efter att koden skannats")
    if progress:
        await progress(BankIDStep("Godkänt — slutför inloggning…", state=APPROVED))


def saml_result(page: Page, jar: CookieJar) -> BankIDResult:
    """SAML-intyget ur IdP:ns sista sida (en auto-post-form till sso.infomentor.se)."""
    saml = hidden_inputs(page.text).get("SAMLResponse")
    action = form_action(page.text, page.url)
    if page.status != 200 or not saml or not action:
        raise BankIDPageChanged(
            f"ingen SAMLResponse-form efter godkännande ({page.status} {host_and_path(page.url)})"
        )
    if urlsplit(action).hostname != "sso.infomentor.se":
        # Bara parten som litar på intyget får ta emot det.
        raise BankIDPageChanged(f"SAMLResponse-formen pekar på {urlsplit(action).hostname}")
    return BankIDResult(saml_response=saml, jar=jar)


async def post_saml_response(
    do_request: DoRequest,
    *,
    saml_response: str,
    jar: CookieJar,
    idp: str,
) -> CookieJar:
    """Posta intyget till login.ashx och följ till hubbens request-token-sida och IMHome.

    `jar` är den som `run` fyllde: login.ashx behöver cookies från det första hoppet.
    """
    post_url = f"{SSO_BASE}{SSO_LOGIN_PATH}?idp={idp}"
    page = await fetch(
        do_request,
        jar,
        "POST",
        post_url,
        body=urlencode({"SAMLResponse": saml_response}),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": SSO_BASE},
    )
    location = page.headers.get("location")
    if not location:
        raise TokenError(f"POST SAMLResponse till login.ashx svarade {page.status}")
    page = await follow(do_request, jar, urljoin(post_url, location), error=TokenError)
    if page.status != 200 or "oauth_token" not in page.text:
        raise TokenError(f"nådde inte request-token-sidan (status {page.status})")
    return await submit_oauth_token(do_request, jar, page.url)
