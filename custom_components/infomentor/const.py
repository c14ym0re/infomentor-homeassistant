"""Konstanter för InfoMentor-integrationen."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "infomentor"

MANUFACTURER: Final = "InfoMentor"
MODEL: Final = "Skolplattform"

# Inställningar
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ENABLE_LUNCH: Final = "enable_lunch"
CONF_MATEO_UNIT: Final = "mateo_unit_id"
CONF_NAMES: Final = "names"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_IDP: Final = "idp"
CONF_REFRESH_TOKEN: Final = "refresh_token"

DEFAULT_SCAN_INTERVAL_MIN: Final = 20
MIN_SCAN_INTERVAL_MIN: Final = 5
MAX_SCAN_INTERVAL_MIN: Final = 180
DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MIN)

# Ändpunkter
HUB_BASE: Final = "https://hub.infomentor.se"
MENTOR_LOGIN: Final = "https://infomentor.se/swedish/production/mentor/"
MATEO_API: Final = "https://meny-api.mateo.se/api/v1/days"
MENTOR_BASE: Final = "https://infomentor.se"
USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/101.0.4951.67 Safari/537.36"
)

# BankID: varje kommun har en egen `idp` (se bankid.py).
SSO_LOGIN_PATH: Final = "/login.ashx"
IDP_STOCKHOLM_PAR: Final = "stockholm_par"

ATTR_PUPILS: Final = "pupils"
ATTR_ATTRIBUTION: Final = "Data från InfoMentor"


def clamp_interval(minutes: int) -> int:
    """Håller uppdateringsintervallet inom tillåtna gränser."""
    return max(MIN_SCAN_INTERVAL_MIN, min(MAX_SCAN_INTERVAL_MIN, int(minutes)))
