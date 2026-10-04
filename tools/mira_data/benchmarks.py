"""Benchmark routing for ``technical``: pick the right index for the instrument's market.

The default benchmark was ``SPY`` unconditionally, which silently mis-measured every A-share. For
生益科技 (600183) the technical context score reads **44 against SPY** but **56 against 沪深300
(000300.SS)** — a 12-point swing that changes the trend characterisation from weak to constructive.
Nothing errored; the number was simply computed against the wrong market.

**Priority, in order:**

1. an explicit ``--benchmark`` always wins, and is normalised but never overridden;
2. otherwise the instrument's own market decides: A-share -> ``000300.SS`` (沪深300), Hong Kong ->
   ``^HSI``, everything else -> ``SPY``.

The routing reuses :mod:`mira_data.cn_symbols`, so a bare ``000300`` becomes ``000300.SS`` here
exactly as it does at the market-price adapter boundary — the same rule, one implementation.

**The resolved value must be reported, not just used.** A relative-strength figure is meaningless
without its baseline, and an auto-selected baseline is the easiest one to misread as the old
default. So :func:`resolve_benchmark` returns whether the choice was automatic, and every surface
that prints a benchmark says which one it is and whether the caller asked for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import cn_symbols

DEFAULT_US = "SPY"
DEFAULT_A_SHARE = "000300.SS"      # 沪深300, the standard A-share market baseline
DEFAULT_HK = "^HSI"                # 恒生指数

# Bare six-digit INDEX codes, mapped to the suffix the index is actually served under.
#
# This exists because the generic A-share rule is "0xxxxx -> .SZ", which is right for Shenzhen
# EQUITIES and wrong for index codes: the CSI and SSE indices are published under .SS. The failure
# is not a 404 that announces itself — probing showed bare `000905` resolves to **"XPD"**,
# `000016` to **"KONKA GROUP"** and `000852` to **"SOFE"**, i.e. real Shenzhen *stocks* that happen
# to share the code. A benchmark silently swapped for an unrelated small-cap would corrupt every
# relative-strength figure while looking entirely healthy, so bare index codes are resolved here
# first and never through the equity rule.
BARE_INDEX_CODES = {
    "000001": "000001.SS",     # 上证指数 (NOT 平安银行 000001.SZ — see the collision note below)
    "000016": "000016.SS",     # 上证50
    "000300": "000300.SS",     # 沪深300
    "000688": "000688.SS",     # 科创50
    "000852": "000852.SS",     # 中证1000
    "000905": "000905.SS",     # 中证500
    "000906": "000906.SS",     # 中证800
    "399001": "399001.SZ",     # 深证成指
    "399006": "399006.SZ",     # 创业板指
}

# Yahoo suffixes that identify a non-A-share, non-US market we route explicitly.
_HK_SUFFIXES = (".HK",)


@dataclass(frozen=True)
class BenchmarkChoice:
    """The benchmark to use, and where the choice came from."""

    symbol: str
    automatic: bool
    reason: str

    @property
    def annotation(self) -> str:
        """``"000300.SS [auto]"`` / ``"SPY [explicit]"``, for CLI and report output."""
        return f"{self.symbol} [{'auto' if self.automatic else 'explicit'}]"


def resolve_benchmark(ticker: str, benchmark: Optional[str] = None) -> BenchmarkChoice:
    """Choose the benchmark for ``ticker``.

    ``benchmark`` given (non-empty) is honoured exactly, merely normalised. Otherwise the market is
    inferred from the ticker. ``None`` or an empty string means "not supplied", which is what an
    unspecified CLI flag yields.
    """
    text = str(benchmark or "").strip()
    if text:
        return BenchmarkChoice(resolve_index(text), False, "supplied by the caller")

    market = market_of(ticker)
    if market == "CN":
        return BenchmarkChoice(
            DEFAULT_A_SHARE, True,
            "A-share instrument, so the A-share market baseline is used rather than SPY")
    if market == "HK":
        return BenchmarkChoice(
            DEFAULT_HK, True,
            "Hong Kong instrument, so the Hong Kong market baseline is used rather than SPY")
    return BenchmarkChoice(DEFAULT_US, True, "no market-specific baseline applies")


def resolve_index(code: str) -> str:
    """Normalise a benchmark symbol, preferring the index map over the equity rule.

    Order matters. A bare code in :data:`BARE_INDEX_CODES` resolves to its index suffix; only codes
    outside that map fall through to :mod:`mira_data.cn_symbols`. This is a benchmark-layer concern
    rather than a symbol-layer one: ``000905`` genuinely *is* a Shenzhen equity to the generic
    rule, and the generic rule is not wrong — it simply cannot know the caller meant the index.
    """
    text = str(code or "").strip()
    if not text:
        return text
    direct = BARE_INDEX_CODES.get(text.upper())
    if direct:
        return direct
    return cn_symbols.to_yahoo_symbol(text)


def bare_index_code(symbol: str) -> Optional[str]:
    """Inverse of :data:`BARE_INDEX_CODES`: ``"000300.SS"`` -> ``"000300"``, else ``None``.

    Lets a caller decide "is this one of the indices we have an official channel for?" without
    duplicating the map.
    """
    text = str(symbol or "").strip().upper()
    for code, resolved in BARE_INDEX_CODES.items():
        if text == resolved.upper() or text == code:
            return code
    return None


def market_of(ticker: str) -> str:
    """``"CN"`` / ``"HK"`` / ``"US"`` for a ticker, using the shared A-share rules.

    A bare six-digit code is an A-share by definition; a suffixed ticker is classified by its
    suffix. Anything else — including a bare US symbol like ``AAPL`` — is US.
    """
    text = str(ticker or "").strip().upper()
    if cn_symbols.is_bare_a_share(text):
        return "CN"
    if text.endswith(_HK_SUFFIXES):
        return "HK"
    if text.endswith((".SS", ".SZ", ".BJ")):
        return "CN"
    return "US"
