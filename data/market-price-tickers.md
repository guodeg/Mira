# Market Price Tickers

`mira_data fetch market_price <ticker>` reads any Yahoo v8 chart ticker, so coverage is far
wider than the "US + CN" the L5 gap list assumed. This file records tickers **verified live**, so
that reaching another market is a lookup rather than a guess. It is a pointer list, not a source
registry: the source record is `yahoo_chart_api_v8` in [source-registry.csv](source-registry.csv),
and every read is L5 `market_pricing` — a relayed quote, **not** the controlling exchange.

All rows below were fetched on 2026-10-04 and returned a close for 2026-10-02.

## Verified tickers by market

| market | suffix | examples verified | currency | exchange timezone |
| --- | --- | --- | --- | --- |
| US | — | `AAPL` | USD | `America/New_York` |
| Hong Kong | `.HK` | `0700.HK`, `9988.HK` | HKD | `Asia/Hong_Kong` |
| Japan | `.T` | `7203.T`, `6758.T` | JPY | `Asia/Tokyo` |
| Taiwan | `.TW` | `2330.TW`, `2317.TW` | TWD | `Asia/Taipei` |
| Korea | `.KS` | `005930.KS`, `000660.KS` | KRW | `Asia/Seoul` |
| Germany | `.DE` | `SAP.DE`, `SIE.DE` | EUR | `Europe/Berlin` |
| Netherlands | `.AS` | `ASML.AS` | EUR | `Europe/Amsterdam` |
| France | `.PA` | `MC.PA` | EUR | `Europe/Paris` |
| China A | `.SS` / `.SZ` | `000300.SS` | CNY | `Asia/Shanghai` |

Indices use a leading caret and read through the same family: `^GSPC` 7,722.72,
`^IXIC` 27,190.863, `^N225` 68,309.46, `^HSI` 23,972.29, `^VIX` 15.31.

Two index quirks, both verified: `^N225` reports `exchangeTimezoneName=Asia/Tokyo` but exchange
code **`OSA`** (Osaka) — its `gmtoffset` is the Tokyo one, so use the *timezone* field rather
than the exchange code when resolving a date. And `^VIX` is `America/Chicago`, not New York,
because CBOE is a Chicago venue; an options- or volatility-related series anchored to New York
would be an hour off.

## The timezone travels with every claim

The adapter records `exchangeTimezoneName` and `gmtoffsetSeconds` in provenance, because
resolving a *relative* date (`今天`, `latest`) has to happen in the instrument's own market
timezone — "today" for `005930.KS` is an `Asia/Seoul` date, not the user's or New York's. A quote
without its market timezone cannot be re-checked for freshness later.

## What this channel does and does not give

- **Does**: last close, 52-week high/low, last volume as evidence rows, plus the full daily OHLCV
  history as a side series.
- **Does not**: fundamentals, index composition, or index valuation. For A-share indices use
  `index_benchmark` / `index_valuation` (L2, official CSI); for the volatility complex use
  `cboe_volatility` (L2, official CBOE). Those are controlling sources, so prefer them over a
  relayed quote when the question is about the index itself.
- **Tier**: L5 `market_pricing`, `confidence=medium`, `conflict_status=not_checked`. Cross-check
  against the exchange before a durable conclusion — the same VIX read from Yahoo and from CBOE
  agreed (15.31), but that agreement is a check, not a guarantee.
