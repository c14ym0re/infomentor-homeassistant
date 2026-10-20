"""Väntan på BankID-godkännande, oberoende av vilken kommuns IdP som ligger under."""

from __future__ import annotations

import pytest

from custom_components.infomentor.bankid import (
    APPROVED,
    CANCELLED,
    EXPIRED,
    PENDING,
    SCANNED,
    BankIDDenied,
    BankIDError,
    BankIDTimeout,
    wait_for_approval,
)


class Provider:
    """En IdP som svarar med de lägen testet anger, i ordning."""

    def __init__(self, *statuses: str) -> None:
        self.statuses = list(statuses)
        self.starts = 0
        self.steps: list[tuple[str, str | None]] = []

    async def start(self) -> tuple[str, str, str]:
        self.starts += 1
        return f"order{self.starts}", "tok", f"qr{self.starts}.0"

    async def poll(self, order: str) -> tuple[str, str | None]:
        return self.statuses.pop(0), None

    async def progress(self, step) -> None:
        self.steps.append((step.state, step.qr_data))

    async def wait(self, *, timeout: float = 60, max_restarts: int = 3) -> None:
        await wait_for_approval(
            self.start,
            self.poll,
            progress=self.progress,
            poll_interval=0,
            timeout=timeout,
            max_restarts=max_restarts,
        )


async def test_approval_ends_the_wait_and_every_state_change_is_reported() -> None:
    provider = Provider(PENDING, SCANNED, APPROVED)
    await provider.wait()
    assert provider.steps == [
        (PENDING, "qr1.0"),  # första bilden
        (SCANNED, "qr1.0"),
        (APPROVED, None),
    ]


async def test_an_order_nobody_scanned_is_restarted_with_a_fresh_qr() -> None:
    provider = Provider(EXPIRED, APPROVED)
    await provider.wait()
    assert provider.starts == 2
    assert (PENDING, "qr2.0") in provider.steps


async def test_restarts_are_limited_and_end_as_a_timeout() -> None:
    provider = Provider(*[EXPIRED] * 10)
    with pytest.raises(BankIDTimeout):  # ingen skannade: en tidsgräns, inte ett sidfel
        await provider.wait(max_restarts=2)
    assert provider.starts == 3


async def test_an_order_that_dies_after_a_scan_is_not_restarted() -> None:
    provider = Provider(SCANNED, EXPIRED)
    with pytest.raises(BankIDError) as caught:
        await provider.wait()
    assert not isinstance(caught.value, BankIDTimeout)
    assert provider.starts == 1


async def test_cancelling_in_the_app_is_a_denial() -> None:
    with pytest.raises(BankIDDenied):
        await Provider(SCANNED, CANCELLED).wait()


async def test_no_approval_in_time_is_a_timeout() -> None:
    with pytest.raises(BankIDTimeout):
        await Provider(*[PENDING] * 5).wait(timeout=-1)
