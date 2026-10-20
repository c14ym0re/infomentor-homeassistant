"""HTTP-anrop för inloggningskedjorna (BankID, token-bryggan, parkopplingen).

Cookies skickas här som en färdig `Cookie`-header, exakt som servern gav dem. aiohttps egen
cookiejar skriver nämligen om värden som innehåller `/`, `+` eller `=` (den sätter
citattecken runt dem), och då avvisar SiteMinder och PhenixID sessionen (se
`new_raw_cookie_session` i api.py). `fetch` sparar därför alla cookies själv i en
`CookieJar` och skickar dem oförändrade."""

from __future__ import annotations

import contextlib
import email.utils
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from yarl import URL

from .const import USER_AGENT

MAX_HOPS = 20

# `do_request(method, url, body, headers)` ger ett aiohttp-svar utan att följa omdirigeringar.
DoRequest = Callable[..., Awaitable[Any]]


class CookieJar:
    """Minimal RFC 6265-cookiejar, tillräckligt för att bära IMHome över domäner."""

    def __init__(self) -> None:
        self._cookies: list[dict[str, Any]] = []

    def all(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self._cookies]

    def has(self, name: str) -> bool:
        return any(c["name"] == name for c in self._cookies)

    def cookie_header(self, url: str) -> str:
        try:
            u = URL(url)
        except Exception:  # noqa: BLE001
            return ""
        host, path, secure = u.host, u.path or "/", u.scheme == "https"
        now = time.time()
        matching = []
        for c in self._cookies:
            if c.get("expires") is not None and now > c["expires"]:
                continue
            if not self._domain_matches(c.get("domain", ""), host):
                continue
            if not self._path_matches(c.get("path", "/"), path):
                continue
            if c.get("secure") and not secure:
                continue
            matching.append(c)
        matching.sort(key=lambda c: len(c.get("path", "/")), reverse=True)
        return "; ".join(f"{c['name']}={c['value']}" for c in matching)

    @staticmethod
    def _domain_matches(domain: str, host: str) -> bool:
        if not domain:
            return False
        if domain == host:
            return True
        if domain.startswith("."):
            root = domain[1:]
            return host == root or host.endswith(domain)
        return False

    @staticmethod
    def _path_matches(cookie_path: str, request_path: str) -> bool:
        if cookie_path == request_path:
            return True
        if request_path.startswith(cookie_path):
            return cookie_path.endswith("/") or request_path[len(cookie_path)] == "/"
        return False

    def set_from_header(self, set_cookie: str, url: str) -> None:
        parts = set_cookie.split(";")
        first = parts[0]
        eq = first.find("=")
        if eq == -1:
            return
        name = first[:eq].strip()
        value = first[eq + 1 :].strip()
        try:
            host = URL(url).host or ""
        except Exception:  # noqa: BLE001
            return
        domain = "." + host if host else ""
        path = "/"
        secure = False
        expires: float | None = None
        for part in parts[1:]:
            kv = part.strip()
            key_eq = kv.find("=")
            key = (kv if key_eq == -1 else kv[:key_eq]).lower()
            val = "" if key_eq == -1 else kv[key_eq + 1 :].strip()
            if key == "domain":
                domain = val if val.startswith(".") else "." + val
            elif key == "path":
                path = val if val.startswith("/") else "/"
            elif key == "secure":
                secure = True
            elif key == "max-age":
                try:
                    expires = time.time() + int(val)
                except ValueError:
                    expires = 0
            elif key == "expires":
                with contextlib.suppress(TypeError, ValueError):
                    expires = email.utils.parsedate_to_datetime(val).timestamp()
        if not domain:
            domain = host
        self._cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": secure,
                "expires": expires,
            }
        )


@dataclass(frozen=True)
class Page:
    status: int
    headers: dict[str, str]  # nycklarna med små bokstäver
    text: str
    url: str


async def fetch(
    do_request: DoRequest,
    jar: CookieJar,
    method: str,
    url: str,
    *,
    body: str | None = None,
    headers: dict[str, str] | None = None,
) -> Page:
    """Ett anrop: cookies ur `jar` skickas, `Set-Cookie` sparas i den, ingen omdirigering följs.

    URL:en skickas exakt som den är skriven (`encoded=True`); yarl skulle annars skriva om
    t.ex. `%2D` till `-` i SiteMinders query.
    """
    sent = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    }
    if cookie := jar.cookie_header(url):
        sent["Cookie"] = cookie
    sent.update(headers or {})
    response = await do_request(method, URL(url, encoded=True), body, sent)
    try:
        status = response.status
        received = {k.lower(): v for k, v in response.headers.items()}
        text = await response.text()
    finally:
        response.release()
    for chunk in response.headers.getall("Set-Cookie", []):
        jar.set_from_header(chunk, url)
    return Page(status, received, text, url)


async def follow(
    do_request: DoRequest,
    jar: CookieJar,
    url: str,
    *,
    error: type[Exception],
    headers: dict[str, str] | None = None,
) -> Page:
    """GET `url` och följ 3xx som GET. Kastar `error` vid omdirigering utan Location eller loop."""
    for _ in range(MAX_HOPS):
        page = await fetch(do_request, jar, "GET", url, headers=headers)
        if not 300 <= page.status < 400:
            return page
        location = page.headers.get("location")
        if not location:
            raise error(f"{page.status} utan Location vid {host_and_path(url)}")
        url = urljoin(url, location)
    raise error(f"fler än {MAX_HOPS} omdirigeringar")


def form_action(html: str, page_url: str) -> str | None:
    """`action` från första <form>, relativt till sidan den hämtades från."""
    form = re.search(r"<form\b[^>]*>", html, re.IGNORECASE)
    action = (
        re.search(r'action=["\']([^"\']*)["\']', form.group(0), re.IGNORECASE) if form else None
    )
    return urljoin(page_url, action.group(1)) if action else None


def host_and_path(url: str) -> str:
    """Bara värd och sökväg: en query kan innehålla sessions-id."""
    parsed = URL(url)
    return f"{parsed.host}{parsed.path}"
