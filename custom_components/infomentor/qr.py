"""QR-koden som en svartvit bild, oberoende av HA:s tema.

HA:s `ha-qr-code` ritar de mörka rutorna i temats textfärg; i mörkt tema blir det
en inverterad kod som BankID-appen kan vägra läsa, och elementet har inget
färgalternativ. Koden ritas därför här som en SVG, svart på vitt med tyst zon,
och skickas som en data-URI.
"""

from __future__ import annotations

import base64
import io

import segno

# BankID-QR-data är en kort sträng; låg felkorrigering ger färre, större rutor och en
# kod som är lätt att skanna från en skärm.
_ERROR_LEVEL = "l"
# Fast version, så bilden har samma storlek hela tiden. BankID-datat växer (räknaren stiger,
# `bankid.<uuid>.<n>.<hash>` är ca 110 byte) och en automatisk version hoppar då mellan
# storlekar mitt under skanningen. Version 7 rymmer 154 byte på nivå L.
_VERSION = 7
_SCALE = 8
_QUIET_ZONE_MODULES = 4


def qr_markdown_image(data: str) -> str:
    """`![](data:image/svg+xml;base64,...)` för `data`, svart på vitt."""
    try:
        qr = segno.make(data, error=_ERROR_LEVEL, version=_VERSION, micro=False)
    except segno.DataOverflowError:
        # Oväntat långt data: hellre en större bild än ingen kod.
        qr = segno.make(data, error=_ERROR_LEVEL, micro=False)
    out = io.BytesIO()
    qr.save(
        out,
        kind="svg",
        scale=_SCALE,
        border=_QUIET_ZONE_MODULES,
        dark="#000000",
        light="#ffffff",
        xmldecl=False,
        nl=False,
    )
    encoded = base64.b64encode(out.getvalue()).decode("ascii")
    return f"![BankID](data:image/svg+xml;base64,{encoded})"
