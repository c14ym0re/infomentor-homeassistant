"""BankID-inloggningen som en bakgrundsuppgift som config-flödet kan visa live.

HA ritar om ett progress-steg först när steget körs om med nya
`description_placeholders`. `wait_for_change()` ger därför flödet en kortlivad
uppgift som blir klar vid nästa ändring (ny QR-bild, nytt läge) eller vid slut,
så att steget kan köras om med de nya placeholders.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .api import CannotConnect, InvalidAuth
from .bankid import (
    BankIDDenied,
    BankIDError,
    BankIDPageChanged,
    BankIDStep,
    BankIDTimeout,
)
from .qr import qr_markdown_image

_LOGGER = logging.getLogger(__name__)

# Säkerhetsnät: en väntan får aldrig hänga längre än så, även om ingen ändring kommer.
MAX_WAIT_SECONDS = 5.0

# QR-data och token går in i HTML-attribut och en URL i en markdown-beskrivning, så
# bara tecken som BankID själv använder släpps igenom.
_SAFE = re.compile(r"^[A-Za-z0-9._\-]{1,512}$")

AUTOSTART_LINK = "https://app.bankid.com/?autostarttoken={token}&redirect=null"


def _error_key(err: Exception) -> str:
    if isinstance(err, BankIDDenied):
        return "bankid_denied"
    if isinstance(err, BankIDTimeout):
        return "bankid_timeout"
    if isinstance(err, BankIDPageChanged):
        return "bankid_page_changed"
    if isinstance(err, BankIDError):
        return "bankid_failed"
    if isinstance(err, InvalidAuth):
        return "invalid_auth"
    if isinstance(err, CannotConnect):
        return "cannot_connect"
    return "unknown"


class BankIdLogin:
    """Kör `run(progress)` i bakgrunden och samlar det UI:t ska visa."""

    def __init__(
        self,
        hass: Any,
        run: Callable[[Callable[[BankIDStep], Awaitable[None]]], Awaitable[Any]],
    ) -> None:
        self.message = "Startar BankID…"
        self.qr_data = ""
        self.autostart_token = ""
        self.error: str | None = None
        self.detail = ""
        self._seen: list[str] = []
        self.outcome: Any = None
        self._changed = asyncio.Event()
        self._waiter: asyncio.Task[None] | None = None
        self._task = hass.async_create_task(self._main(run))

    async def _progress(self, step: BankIDStep) -> None:
        if not self._seen or self._seen[-1] != step.state:
            self._seen.append(step.state)
        self.message = step.message
        if step.qr_data and _SAFE.match(step.qr_data):
            self.qr_data = step.qr_data
        if step.autostart_token and _SAFE.match(step.autostart_token):
            self.autostart_token = step.autostart_token
        self._changed.set()

    async def _main(self, run: Callable[..., Awaitable[Any]]) -> None:
        try:
            self.outcome = await run(self._progress)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            self.error = _error_key(err)
            self.detail = self._describe(err)
            if self.error == "unknown":
                _LOGGER.exception("Oväntat fel vid BankID-inloggning")
            else:
                _LOGGER.warning("BankID-inloggningen slutade: %s", self.detail)
        finally:
            self._changed.set()

    def _describe(self, err: Exception) -> str:
        """Vad ordern hann göra och hur den slutade, utan länkar, till retry-formuläret."""
        seen = " → ".join(self._seen) or "inget"
        ended = re.sub(r"https?://\S+", "<url>", f"{type(err).__name__}: {err}")[:200]
        return f"Sett: {seen}. Slut: {ended}"

    @property
    def done(self) -> bool:
        return self._task.done()

    def cancel(self) -> None:
        if not self._task.done():
            self._task.cancel()
        if self._waiter is not None and not self._waiter.done():
            self._waiter.cancel()

    async def _wait(self) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._changed.wait(), MAX_WAIT_SECONDS)
        self._changed.clear()

    def wait_for_change(self) -> asyncio.Task[None]:
        """Samma uppgift tills den är klar, sedan en ny som väntar på nästa ändring."""
        if self._waiter is None or self._waiter.done():
            self._waiter = asyncio.ensure_future(self._wait())
        return self._waiter

    @property
    def progress_action(self) -> str:
        return "bankid" if self.qr_data else "bankid_starting"

    def placeholders(self) -> dict[str, str]:
        link = (
            AUTOSTART_LINK.format(token=self.autostart_token) if self.autostart_token else ""
        )
        # Bilden kommer in som ett placeholder-värde: HA:s översättningar tolkar `<tagg>`
        # i en sträng som ICU-taggar (INVALID_TAG). Ritad här, inte med HA:s ha-qr-code,
        # så att den alltid är svart på vitt (se qr.py).
        qr = qr_markdown_image(self.qr_data) if self.qr_data else ""
        return {"message": self.message, "qr_code": qr, "autostart_link": link}
