"""Stockholm Stads BankID-inloggning (idp `stockholm_par`).

Kedjan (verifierad live): sso.infomentor.se/login.ashx, SiteMinder, metodvalets länk, BankID-sidan
(vars URL med SiteMinders query bär sessionen), `initialize=bankid` ger {token, order, qrData},
`verifyorder` pollas tills `OK`, GET sidans URL ger SAML-intyget.

Det här är Stockholm Stads sidor, inte BankIDs eget API, och egenheterna här gäller bara
dem. Andra kommuner får en egen drivrutin i en egen modul; den här ska inte röras för
deras skull.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin

from .bankid import (
    APPROVED,
    CANCELLED,
    EXPIRED,
    MAX_POLL_SECONDS,
    MAX_RESTARTS,
    PENDING,
    POLL_INTERVAL_SECONDS,
    SCANNED,
    SSO_BASE,
    BankIDError,
    BankIDPageChanged,
    BankIDResult,
    saml_result,
    wait_for_approval,
)
from .const import IDP_STOCKHOLM_PAR, SSO_LOGIN_PATH
from .web import CookieJar, DoRequest, fetch, follow, host_and_path

_LOGGER = logging.getLogger(__name__)

# PhenixIDs states, mapped to the ones `wait_for_approval` understands. ERROR is what an
# order that nobody scanned turns into after ca 30 s.
_STATUS = {
    "PENDING": PENDING,
    "STARTED": SCANNED,
    "USER_SIGN": SCANNED,
    "OK": APPROVED,
    "CANCELLED": CANCELLED,
    "ERROR": EXPIRED,
}
BANKID_LINK_RE = re.compile(
    r"href=[\"']([^\"']*/NECSadc/mbid/b64startpage\.jsp\?startpage=[^\"']*)[\"']", re.IGNORECASE
)
NECSADCMBID_RE = re.compile(r"NECSadcmbid/authenticate/NECSadcmbid", re.IGNORECASE)
_JSON_XHR = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}



class StockholmDriver:
    """Kör Stockholm Stads BankID-inloggning (QR) och postar intyget till InfoMentor.

    `do_request(method, url, body, headers)` ger ett aiohttp-svar och följer inga omdirigeringar;
    `fetch` och `follow` (web.py) gör det och samlar cookies i en jar över hela kedjan.
    """

    NAME = "Stockholms Stad"  # visas i kommunlistan

    def __init__(self, *, poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
        self._poll_interval = poll_interval

    async def _open(self, do_request: DoRequest, jar: CookieJar) -> str:
        """Till BankID-sidan; returnerar dess URL med SiteMinders query, byte för byte."""
        start = f"{SSO_BASE}{SSO_LOGIN_PATH}?idp={IDP_STOCKHOLM_PAR}"
        chooser = await follow(do_request, jar, start, error=BankIDPageChanged)
        link = BANKID_LINK_RE.search(chooser.text)
        if chooser.status != 200 or not link:
            raise BankIDPageChanged(
                f"metodvalet svarade {chooser.status} utan BankID-länk: sidan ser annorlunda ut"
            )
        href = link.group(1).replace("&amp;", "&")
        page = await follow(do_request, jar, urljoin(chooser.url, href), error=BankIDPageChanged)
        if page.status != 200 or not NECSADCMBID_RE.search(page.url):
            raise BankIDPageChanged(
                f"väntade NECSadcmbid-sidan, hamnade på {page.status} {host_and_path(page.url)}"
            )
        return page.url

    async def _get_json(
        self, do_request: DoRequest, jar: CookieJar, auth_url: str, param: str, what: str
    ) -> dict[str, Any]:
        sep = "&" if "?" in auth_url else "?"
        url = f"{auth_url}{sep}{param}&_={int(time.time() * 1000)}"
        page = await fetch(do_request, jar, "GET", url, headers={**_JSON_XHR, "Referer": auth_url})
        # SERVERBUGG: PhenixID svarar på verifyorder med JSON märkt som text/html (initialize
        # säger application/json); sidans egen kod kör JSON.parse på texten. Content-type
        # säger därför ingenting, bara om kroppen blir ett objekt. Statusen måste vara 200:
        # efter OK svarar servern 302 och efter CANCELLED 500, aldrig "godkänt".
        try:
            data = json.loads(page.text.strip())
        except ValueError:
            data = None
        if page.status != 200 or not isinstance(data, dict):
            raise BankIDPageChanged(f"{what}: status {page.status}, ingen JSON")
        return data

    async def _initialize(
        self, do_request: DoRequest, jar: CookieJar, auth_url: str
    ) -> tuple[str, str, str]:
        data = await self._get_json(do_request, jar, auth_url, "initialize=bankid", "initialize")
        order, token, qr = data.get("order"), data.get("token"), data.get("qrData")
        if not order or not token or not isinstance(qr, str):
            raise BankIDPageChanged("initialize gav ingen order/token/qrData")
        return str(order), str(token), qr

    async def run(
        self,
        do_request: DoRequest,
        *,
        progress: Any | None = None,
        max_restarts: int = MAX_RESTARTS,
        timeout: float = MAX_POLL_SECONDS,
    ) -> BankIDResult:
        """Kör kedjan till SAMLResponse. Kastar BankIDDenied/-Timeout/-PageChanged/-Error."""
        jar = CookieJar()
        auth_url = await self._open(do_request, jar)
        async def start() -> tuple[str, str, str]:
            return await self._initialize(do_request, jar, auth_url)

        async def poll(order: str) -> tuple[str, str | None]:
            answer = await self._get_json(
                do_request, jar, auth_url, f"verifyorder={order}", "verifyorder"
            )
            state = answer.get("state")
            if not isinstance(state, str):
                raise BankIDPageChanged("verifyorder gav inget state")
            if state not in _STATUS:
                raise BankIDError(f"BankID slutade i läget {state}")
            return _STATUS[state], answer.get("qrData")

        await wait_for_approval(
            start,
            poll,
            progress=progress,
            poll_interval=self._poll_interval,
            timeout=timeout,
            max_restarts=max_restarts,
        )
        return await self._collect_assertion(do_request, jar, auth_url)

    async def _collect_assertion(
        self, do_request: DoRequest, jar: CookieJar, auth_url: str
    ) -> BankIDResult:
        """Efter OK: GET sidans URL, 302 till SiteMinderAuthADC (SMSESSION), sedan en
        saml2sso-sida med en auto-post-form till sso.infomentor.se."""
        page = await follow(do_request, jar, auth_url, error=BankIDPageChanged)
        return saml_result(page, jar)
