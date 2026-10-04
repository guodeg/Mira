"""A-share code normalisation for the Yahoo-backed channels.

``market_price`` and ``technical`` take a Yahoo ticker, so a bare A-share code fails with an
HTTP 404 — and the 404 names only the URL, not the reason. Since the A-share workflow starts from
a six-digit code, that is a usability cliff in the middle of the localised stack: the analyst has
to know that 600183 needs ``.SS`` and 000001 needs ``.SZ``.

**The suffix is mechanically derivable**, which is exactly why guessing it should not be the
user's job:

- ``6xxxxx`` and ``9xxxxx`` (B-shares) → ``.SS`` (Shanghai)
- ``0xxxxx`` and ``2xxxxx`` (B-shares) → ``.SZ`` (Shenzhen)
- ``3xxxxx`` (创业板) → ``.SZ``
- ``4xxxxx`` / ``8xxxxx`` (北交所) → ``.BJ``

A ticker that already carries a suffix, or that is not a bare six-digit A-share code, is returned
**unchanged** — including US tickers and index symbols like ``^GSPC`` — because this must not
rewrite anything it does not fully understand.
"""

from __future__ import annotations

import re
from typing import Optional

# prefix -> Yahoo suffix, checked in order. Longest-prefix logic is unnecessary because every key
# is a single leading digit for a six-digit code.
_PREFIX_SUFFIX = {
    "6": ".SS",
    "9": ".SS",
    "0": ".SZ",
    "2": ".SZ",
    "3": ".SZ",
    "4": ".BJ",
    "8": ".BJ",
}

_BARE_A_SHARE = re.compile(r"^\d{6}$")
# A suffix we must not duplicate or second-guess.
_HAS_SUFFIX = re.compile(r"\.(SS|SZ|BJ|HK|T|TW|KS|KQ|DE|AS|PA|L|MI|ST|TO|AX|SI|NS|BO)$", re.I)


def to_yahoo_symbol(code: str) -> str:
    """Return the Yahoo ticker for an A-share code, or the input unchanged if it is not one.

    Idempotent: ``to_yahoo_symbol("600183.SS") == "600183.SS"``.
    """
    text = str(code or "").strip()
    if not text:
        return text
    if _HAS_SUFFIX.search(text):
        return text                       # already resolved (or a non-CN market)
    if text.startswith("^"):
        return text                       # an index symbol
    if not _BARE_A_SHARE.match(text):
        return text                       # a US ticker, a name, or something else entirely
    suffix = _PREFIX_SUFFIX.get(text[0])
    return f"{text}{suffix}" if suffix else text


def is_bare_a_share(code: str) -> bool:
    """True when the input is a bare six-digit A-share code that will be given a suffix."""
    text = str(code or "").strip()
    return bool(_BARE_A_SHARE.match(text)) and text[0] in _PREFIX_SUFFIX


def exchange_of(code: str) -> Optional[str]:
    """``"SH"``/``"SZ"``/``"BJ"`` for a bare code or a suffixed ticker, else ``None``."""
    text = str(code or "").strip()
    if _BARE_A_SHARE.match(text):
        suffix = _PREFIX_SUFFIX.get(text[0])
        return {"SS": "SH", "SZ": "SZ", "BJ": "BJ"}.get(suffix.lstrip(".")) if suffix else None
    match = _HAS_SUFFIX.search(text)
    if match:
        return {"SS": "SH", "SZ": "SZ", "BJ": "BJ"}.get(match.group(1).upper())
    return None
