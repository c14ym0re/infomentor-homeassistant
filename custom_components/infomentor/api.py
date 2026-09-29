"""InfoMentor-klient (aiohttp).

Porterad från den dokumenterade Node-implementationen i
https://github.com/c14ym0re/infomentor-api (oauth_token → mentor/ → __VIEWSTATE
→ credentials → isauthenticated). aiohttp följer med Home Assistant, så inga
externa beroenden krävs.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from time import time
from typing import Any

import aiohttp
from yarl import URL

from .const import HUB_BASE, MATEO_API, MENTOR_LOGIN
from .util import (
    extract_oauth_token,
    extract_pupils,
    find_login_fields,
    hidden_input,
    hidden_inputs,
)

_LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/101.0.4951.67 Safari/537.36"
)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
MAX_HOPS = 20
_REDIRECTS = {301, 302, 303, 307, 308}

MappingLike = dict[str, Any]


class InfomentorError(Exception):
    """Bas för integrationsfel."""


class CannotConnect(InfomentorError):
    """Kunde inte nå InfoMentor."""


class InvalidAuth(InfomentorError):
    """Fel inloggningsuppgifter eller död session."""


class ApiError(InfomentorError):
    """En endpoint svarade oväntat — *inte* ett autentiseringsproblem.

    Hålls isär från InvalidAuth så att ett trasigt endpointsvar inte startar en
    oändlig reauth-loop. Koordinatorn loggar och hoppar över den delen i stället.
    """


class InfomentorApi:
    """Tunn klient mot hub.infomentor.se."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self.pupils: list[dict[str, Any]] = []

    # ------------------------------------------------------------- transport
    async def _once(
        self, url: str, *, method: str = "GET", data: Any = None, headers: MappingLike | None = None
    ) -> aiohttp.ClientResponse:
        request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
        try:
            return await self._session.request(
                method,
                url,
                data=data,
                headers=request_headers,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            raise CannotConnect(str(err)) from err

    async def _follow(self, url: str, *, method: str = "GET", data: Any = None) -> tuple[str, str]:
        """Följer omdirigeringar manuellt och returnerar (slutlig_url, body)."""
        headers: dict[str, str] = {}
        for _ in range(MAX_HOPS):
            response = await self._once(url, method=method, data=data, headers=headers)
            try:
                if response.status in _REDIRECTS:
                    location = response.headers.get("Location")
                    if not location:
                        return str(response.url), await response.text()
                    url = str(response.url.join(URL(location)))
                    if response.status in (302, 303):
                        method, data, headers = "GET", None, {}
                    continue
                text = await response.text()
                return str(response.url), text
            finally:
                response.release()
        raise CannotConnect("för många omdirigeringar")

    async def _post_hub(
        self,
        path: str,
        body: Any | None = None,
        *,
        _retried: bool = False,
        empty_ok: bool = False,
    ) -> Any:
        """POST mot en hub-endpoint. Tom body = död session.

        `empty_ok` för endpoints där tomt svar är ett normalt svar och inte en
        död session (se `async_plan_tasks`) — annars blir koordinatorn lurad att
        logga in och hämta allt en gång till, varje gång.
        """
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        payload = json.dumps(body) if body is not None else None
        url = f"{HUB_BASE}{path}"
        response = await self._once(url, method="POST", data=payload, headers=headers)
        try:
            status = response.status
            location = response.headers.get("Location") or ""
            final_url = str(response.url)
            text = await response.text()
        finally:
            response.release()

        if status in (401, 403):
            raise InvalidAuth(f"HTTP {status} på {path}")

        if status in {301, 302, 303, 307, 308}:
            target = str(URL(final_url).join(URL(location))) if location else "(okänd)"
            # Omdirigeringen kan vara ett led i auth-handskakningen (forceOAuth).
            # Följ den en gång och gör om anropet innan vi ger upp — och logga
            # alltid vart den pekar så att felrapporter blir åtgärdbara.
            _LOGGER.warning(
                "InfoMentor: %s svarade %s → %s", path, status, target.replace(HUB_BASE, "")
            )
            if location and not _retried:
                await self._follow(target)
                return await self._post_hub(path, body, _retried=True, empty_ok=empty_ok)
            raise InvalidAuth(f"{path} omdirigerade till {target}")
        if not text.strip():
            if empty_ok:
                return {}
            # Verifierat beteende: död session svarar 200 med tom body.
            raise InvalidAuth("tomt svar – sessionen har gått ut")

        # Endpoint-fel: logga och låt koordinatorn hoppa över delen.
        if status >= 400:
            raise ApiError(f"{path} svarade {status}: {text[:120]!r}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            raise ApiError(f"{path} gav ogiltigt svar: {text[:120]!r}") from err

    # ------------------------------------------------------------- inloggning
    async def async_login(self) -> list[dict[str, Any]]:
        """Loggar in och returnerar barnen. Kastar InvalidAuth/CannotConnect.

        Börjar alltid med en ren cookie-jar. Utan det kan en halv/gammal session
        göra att vi aldrig hittar inloggningsformuläret vid ett nytt försök
        ("nådde aldrig inloggningsformuläret").
        """
        self._session.cookie_jar.clear()
        _, html = await self._follow(f"{HUB_BASE}/")

        oauth = extract_oauth_token(html)
        if oauth:
            _, html = await self._follow(MENTOR_LOGIN, method="POST", data={"oauth_token": oauth})

        view_state = hidden_input(html, "__VIEWSTATE")
        if not view_state:
            raise InvalidAuth("nådde aldrig inloggningsformuläret")

        fields = find_login_fields(html)
        # Skicka med ALLA dolda fält precis som en webbläsare gör. Login-sidan
        # bäddar in hela kommun-/IdP-listan i dem; utan dem kan sessionen hamna
        # hos fel kommun och API-anropen svarar 302 (se #1/#2).
        form = hidden_inputs(html)
        idp_count = sum(1 for key in form if "IdpListRepeater" in key and key.endswith("$url"))
        if idp_count:
            _LOGGER.debug("Inloggningsformuläret innehåller %d kommun-/IdP-val", idp_count)
        form[fields["username"]] = self._username
        form[fields["password"]] = self._password
        if fields.get("submit"):
            form[fields["submit"]] = "Logga in"
        form["__EVENTTARGET"] = ""
        form["__EVENTARGUMENT"] = ""
        _, html = await self._follow(MENTOR_LOGIN, method="POST", data=form)

        oauth = extract_oauth_token(html)
        if oauth:
            _, html = await self._follow(MENTOR_LOGIN, method="POST", data={"oauth_token": oauth})

        await self._follow(
            f"{HUB_BASE}/authentication/authentication/isauthenticated/?_={int(time() * 1000)}",
            method="POST",
        )

        _, root = await self._follow(f"{HUB_BASE}/")
        if "selectedPupilName" not in root:
            raise InvalidAuth("inloggningen avvisades")

        self.pupils = extract_pupils(root)
        _LOGGER.debug("Inloggad, %d barn hittade", len(self.pupils))
        return self.pupils

    # ------------------------------------------------------------- endpoints
    async def async_switch_pupil(self, pupil: MappingLike) -> None:
        url = pupil.get("switchPupilUrl")
        if not url:
            raise InfomentorError("barnet saknar switchPupilUrl")
        await self._follow(str(url))

    async def async_lessons(self, pupil: MappingLike, *, days: int = 7) -> list[dict[str, Any]]:
        today = date.today()
        data = await self._post_hub(
            "/timetable/timetable/gettimetablelist",
            {
                "UTCOffset": "-120",
                "start": today.isoformat(),
                "end": (today + timedelta(days=days)).isoformat(),
            },
        )
        return data if isinstance(data, list) else data.get("items", [])

    async def async_calendar(self, pupil: MappingLike, *, days: int = 30) -> list[dict[str, Any]]:
        today = date.today()
        data = await self._post_hub(
            "/calendarv2/calendarv2/getentries",
            {"startDate": today.isoformat(), "endDate": (today + timedelta(days=days)).isoformat()},
        )
        return data if isinstance(data, list) else []

    async def async_tasks(self, pupil: MappingLike) -> list[dict[str, Any]]:
        data = await self._post_hub("/task/task/GetTasks", {})
        return data.get("items", []) if isinstance(data, dict) else []

    async def async_attendance(self, pupil: MappingLike) -> dict[str, Any]:
        data = await self._post_hub("/attendance/attendance/appData", {})
        return data if isinstance(data, dict) else {}

    async def async_learnlog(self, pupil: MappingLike) -> dict[str, Any]:
        """Lärloggen (veckobrev m.m.) för valt barn."""
        data = await self._post_hub("/learnlog/learnlog/appData", {})
        return data if isinstance(data, dict) else {}

    async def async_plan_tasks(self, uol_id: str) -> dict[str, Any]:
        """Uppgifterna som hör till en planering (prov, inlämningar …).

        Body-nyckeln måste vara `id`, som för `GetUol`.

        `GetAllTasks` svarar **200 med tom body** när planeringen inte har några
        uppgifter (verifierat mot skolplattformen 2026-09-30), så ett tomt svar här betyder
        "inga uppgifter" — inte död session. Utan det läste koordinatorn det som
        en utgången session och loggade in och hämtade om allt, varje cykel.
        """
        data = await self._post_hub("/UolV2/UolV2/GetAllTasks", {"id": uol_id}, empty_ok=True)
        return data if isinstance(data, dict) else {}

    async def async_plan_detail(self, uol_id: str) -> dict[str, Any]:
        """En planerings innehåll (översikt, pedagogisk planering, kriterier).

        Body-nyckeln måste vara just `id` — `uolId`/`Id` svarar HTTP 500.
        """
        data = await self._post_hub("/UolV2/UolV2/GetUol", {"id": uol_id})
        return data if isinstance(data, dict) else {}

    async def async_plans(self, pupil: MappingLike) -> dict[str, Any]:
        """Planeringar (Unit of Learning) för valt barn.

        Detaljvyn (`GetUol` med body `{id}`) hämtas medvetet inte här — den
        kostar ett anrop per planering och behövs inte för listan.
        """
        data = await self._post_hub("/UolV2/UolV2/GetUols", {})
        return data if isinstance(data, dict) else {}

    async def async_notifications(self) -> list[dict[str, Any]]:
        data = await self._post_hub("/NotificationApp/NotificationApp/GetNotifications", {})
        return data.get("notifications", []) if isinstance(data, dict) else []

    async def async_news(self) -> list[dict[str, Any]]:
        data = await self._post_hub("/Communication/News/GetNewsList", {})
        return data.get("items", []) if isinstance(data, dict) else []

    async def async_lunch(self, unit_id: str, *, days: int = 14) -> list[dict[str, Any]]:
        today = date.today()
        url = (
            f"{MATEO_API}/{unit_id}"
            f"?from={today.isoformat()}&to={(today + timedelta(days=days)).isoformat()}"
        )
        response = await self._once(
            url, headers={"Accept": "application/json", "Referer": "https://meny.mateo.se/"}
        )
        try:
            if response.status != 200:
                raise InfomentorError(f"Mateo svarade {response.status}")
            return await response.json(content_type=None)
        finally:
            response.release()
