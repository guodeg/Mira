# Macro Series IDs

`mira_data fetch macro_fred <series_id>` reads any of FRED's ~800,000 series. An id that is not
written down somewhere is indistinguishable from an unsupported one, so this file records the
ids the L2 gap work relies on. It is a **pointer list, not a source registry**: the source record
is `fred_macro_series_api` in [source-registry.csv](source-registry.csv), and every read must
still record the series id, the observation date and the retrieval date.

Each id below was fetched live on 2026-10-04 and returned a value. FRED units are the provider's:
check `units` assumptions before converting (an index is not a percent, and a spread in
percentage points is not a price).

## Rates and the curve

| series id | what | value read |
| --- | --- | --- |
| `DGS10` | 10-year Treasury yield, constant maturity | 5.24 |
| `DGS2` | 2-year Treasury yield | 4.78 |
| `DFF` | effective federal funds rate | 3.88 |
| `T10Y2Y` | 10-year minus 2-year spread | 0.45 |

## Credit

| series id | what | value read |
| --- | --- | --- |
| `BAMLH0A0HYM2` | US high-yield option-adjusted spread | 3.24 |
| `BAMLC0A0CM` | US investment-grade corporate spread | 0.86 |

## Inflation, labour, activity

| series id | what | value read |
| --- | --- | --- |
| `CPIAUCSL` | CPI, all urban consumers, seasonally adjusted | 334.131 |
| `UNRATE` | civilian unemployment rate | 4.2 |
| `GDP` | gross domestic product, billions of dollars | 32,563.03 |

## FX and commodity

| series id | what | value read |
| --- | --- | --- |
| `DEXCHUS` | China / US foreign exchange rate, CNY per USD | 6.711 |
| `DTWEXBGS` | nominal broad US dollar index | 120.33 |
| `DCOILWTICO` | WTI crude oil, spot | 96.16 |

## Notes that are easy to get wrong

- **`DGS10` is a yield in percent, not a price.** FRED publishes it with a `percent` unit, and a
  missing observation is the literal `"."` — the adapter drops it rather than reading zero.
- **A spread series is already a difference.** `BAMLH0A0HYM2` is the spread in percentage points;
  do not subtract a Treasury yield from it again.
- **FX convention matters.** `DEXCHUS` is CNY per USD, so a *rise* is CNY weakness. Record the
  convention with the value or the sign will be read backwards.
- **Vintages.** FRED returns the currently-known revision unless a vintage is requested. The
  adapter records `realtime_start`/`realtime_end` in provenance for this reason.

## Not on FRED, and therefore separate adapters

Two item-6 sources are genuinely different rather than more series, and each has its own family:

- `treasury_debt`, `treasury_avg_interest` — the Treasury's own debt-stock and average-interest
  statistics (`treasury_fiscal_api`).
- `cftc_cot` — the CFTC's weekly Commitments of Traders positioning (`cftc_cot_api`).

## NBS named series (the 产量 side of the industry gap)

`macro_nbs` takes **named** series rather than indicator ids, and it already covers much of what
the L2 gap list called "industry association 销量/装机/库存 data" — on the production (产量) side,
from the statistical agency instead of an association. Verified live 2026-10-04, 28 monthly rows
each:

| series | what | value read (2026-08) |
| --- | --- | --- |
| `POWER_GENERATION` | 发电量, 亿千瓦时 | 9,437.8 |
| `AUTO_OUTPUT` | 汽车产量, 万辆 | 269.7 |
| `NEV_OUTPUT` | 新能源汽车产量, 万辆 | 164.7 |
| `CRUDE_STEEL` | 粗钢产量, 万吨 | 7,461.4 |
| `STEEL_PRODUCTS` | 钢材产量, 万吨 | 11,475.0 |
| `NONFERROUS_OUTPUT` | 十种有色金属产量, 万吨 | 710.4 |
| `CAPACITY_UTILIZATION` | 产能利用率, % (quarterly) | 73.0 |

`mira_data nbs search <keyword>` lists the indicator ids behind these and any others (28 matches
for 发电, 186 for 汽车, 484 for 产量), and the `macro_nbs` family is the fetch route. Check it
before concluding an industry series is missing: the agency covers far more than the named
shortcuts suggest, and `nbs search` is how to find the rest.
