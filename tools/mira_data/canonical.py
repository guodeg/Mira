"""Canonical data contract + evidence-tier postures.

The canonical families mirror ``templates/ingestion-layer/field-map.yaml``. Every
adapter returns :class:`CanonicalRecord` objects already stamped with the
registry posture for its source, so downstream emit/evidence code never has to
guess an evidence tier (architecture/data-acquisition-upgrade.md §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Canonical families — must stay in sync with field-map.yaml's enum.
CANONICAL_FAMILIES = frozenset(
    {
        "company_financials",
        "market_price",
        "valuation_snapshot",
        "consensus_estimate",
        "estimate_revision",
        "transcript_claim",
        "ownership_short_interest",
        "options_surface",
        "portfolio_position",
        "macro_series",
        # The disclosure record itself (announcement/filing identity + event type),
        # as opposed to the numbers extracted from it. Added for A-share L1 coverage:
        # 业绩预告/回购/减持/中标/诉讼/调研纪要 are neither statements nor management
        # claims, so neither company_financials nor transcript_claim fits them.
        "issuer_disclosure",
    }
)


@dataclass(frozen=True)
class Posture:
    """The fixed evidence posture a source confers on every datum it yields."""

    source_id: str           # must exist in data/source-registry.csv
    source_class: str        # taxonomy bucket (data/source-class-map.csv)
    authority_level: str     # L1-L6
    claim_type: str          # data/claim-taxonomy.md
    evidence_category: str   # data/evidence-posture-taxonomy.md
    access_method: str       # public_api / web_read / ...
    acquisition_mode: str    # free / free_with_key / paid / manual
    latency_class: str       # live / delayed / filing_cycle / archival


# Posture presets keyed by a short adapter id. This is the single place that
# encodes "Yahoo is L5 market_pricing, SEC/BLS are official fact-grade".
POSTURES: dict[str, Posture] = {
    "sec_companyfacts": Posture(
        source_id="sec_companyfacts_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="filing_cycle",
    ),
    "yahoo_chart": Posture(
        source_id="yahoo_chart_api_v8",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="market_pricing",
        evidence_category="market_pricing",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "bls": Posture(
        source_id="bls_public_data_api",
        source_class="official_macro_and_industry",
        authority_level="L2",
        claim_type="fact",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "ibkr_gateway": Posture(
        source_id="ibkr_gateway_local",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="market_pricing",
        evidence_category="market_pricing",
        access_method="local_gateway",
        acquisition_mode="authorized_provider",
        latency_class="live",
    ),
    "ibkr_gateway_positions": Posture(
        source_id="ibkr_gateway_local",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="fact",
        evidence_category="reported_fact",
        access_method="local_gateway",
        acquisition_mode="authorized_provider",
        latency_class="live",
    ),
    "futu_opend": Posture(
        source_id="futu_opend_local",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="market_pricing",
        evidence_category="market_pricing",
        access_method="local_gateway",
        acquisition_mode="authorized_provider",
        latency_class="live",
    ),
    # Licensed vendor read through the hithink-finance CLI. Two postures share one
    # data route but differ by taxonomy bucket: quotes are market pricing, while
    # vendor-standardized statements are aggregated financial data that cannot
    # replace issuer/CNINFO disclosure (source-taxonomy.md).
    "hithink_finance_market": Posture(
        source_id="hithink_finance_api",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="market_pricing",
        evidence_category="market_pricing",
        access_method="public_api",
        acquisition_mode="free_with_key",
        latency_class="delayed",
    ),
    # Same licensed A-share route as the quote posture above, but for the listed ETF
    # option surface: still market pricing (L5), quoted per contract rather than per name.
    "hithink_finance_options": Posture(
        source_id="hithink_finance_api",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="market_pricing",
        evidence_category="market_pricing",
        access_method="public_api",
        acquisition_mode="free_with_key",
        latency_class="delayed",
    ),
    # Stock-level valuation ratios (PE/PB/PS/PCF) on the same licensed route. The family also
    # has an index-level channel from the CSI compiler; this is the per-name half, and it is the
    # vendor's ratio computation rather than an issuer figure, so it stays market pricing at L5.
    "hithink_finance_valuation": Posture(
        source_id="hithink_finance_api",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="market_pricing",
        evidence_category="market_pricing",
        access_method="public_api",
        acquisition_mode="free_with_key",
        latency_class="delayed",
    ),
    # 董监高持股变动 through the Eastmoney relay: the A-share counterpart of a Form 4 feed. An
    # aggregator is the publisher of this structured copy, so it is L5 and the claim is the
    # reported metric; the issuer's own filing at the exchange/CNINFO is the L2 upgrade path.
    "em_executive_holdings": Posture(
        source_id="eastmoney_insider_api",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # 限售解禁 (lock-up expiry) on the same relay. Only the date, the share type and the batch
    # holder count are claimed, because the relay's quantity/ratio/value fields did not reconcile
    # in the live probe; those travel as raw vendor data with a unit caveat.
    "em_lockup_schedule": Posture(
        source_id="eastmoney_lockup_api",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "hithink_finance_financials": Posture(
        source_id="hithink_finance_financials_api",
        source_class="aggregated_financial_data",
        authority_level="L5",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free_with_key",
        latency_class="filing_cycle",
    ),
    # CNINFO is the disclosure portal designated for mainland listed companies, so a
    # specific announcement cited with its own identity (secCode + announcementId +
    # title + disclosure date + PDF URL) is issuer primary disclosure — the same rule
    # that makes a specific SEC filing L1 while the companyfacts dataset stays L2.
    "cninfo_disclosure": Posture(
        source_id="cninfo_announcement_api",
        source_class="issuer_primary_disclosure",
        authority_level="L1",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="filing_cycle",
    ),
    # Sell-side expectations relayed by a portal: L5 consensus, never an issuer fact.
    # claim_type=forecast is deliberate for the whole endpoint — the EPS estimates,
    # the six-month rating tallies and the target-price range are all analyst
    # expectations, which claim-taxonomy.md says may describe expectation but not fact.
    "eastmoney_consensus": Posture(
        source_id="eastmoney_consensus_api",
        source_class="consensus_and_estimates",
        authority_level="L5",
        claim_type="forecast",
        evidence_category="estimate",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # Exchange-published margin statistics: the exchanges are the controlling source
    # for their own market-structure data, so this is genuine L2 (unlike the 龙虎榜 /
    # 大宗 / 北向 paths that every open-source library reaches via an aggregator).
    "sse_margin": Posture(
        source_id="sse_margin_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "szse_margin": Posture(
        source_id="szse_margin_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # Investor Q&A platforms. The channel is exchange-designated (L2) while the content
    # is company commentary, so claim_type stays company_claim / company_statement: per
    # claim-taxonomy.md a company claim is 公司口径 that still needs cross-checking.
    "irm_cninfo": Posture(
        source_id="irm_cninfo_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="company_claim",
        evidence_category="company_statement",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "sse_einteraction": Posture(
        source_id="sse_einteraction_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="company_claim",
        evidence_category="company_statement",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # CFETS benchmark rates: official interbank benchmarks, so genuine L2 macro data
    # reachable without third-party dependencies. 国家统计局 is deliberately absent —
    # its endpoints answer with a 服务异常 anti-bot page (see the adapter docstring).
    "chinamoney_rates": Posture(
        source_id="chinamoney_rates_api",
        source_class="official_macro_and_industry",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # National accounts (CPI/PPI/GDP/PMI) read from an aggregator's table because the NBS
    # endpoints are anti-bot blocked for a stdlib client. The content is official
    # statistics but the channel is not the controlling source, so the tier is L5 and the
    # claim is a reported metric rather than a fact.
    "eastmoney_macro": Posture(
        source_id="eastmoney_macro_api",
        source_class="official_macro_and_industry",
        authority_level="L5",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # Genuine official national accounts: the statistical agency's own new data library
    # (2026-03-27), keyless over a documented JSON API. This is the L2 source the relay
    # above stands in for, and both can be read together as a cross-check.
    "nbs_stats": Posture(
        source_id="nbs_stats_api",
        source_class="official_macro_and_industry",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # The index compiler publishing its own index level and composition. The class default is
    # L5, but for a benchmark the compiler is the authoritative source, so the registry
    # overrides the level to L2 and says why (the same pattern the SEC dataset rows use).
    "csindex_index": Posture(
        source_id="csindex_index_api",
        source_class="market_price_and_trading",
        authority_level="L2",
        claim_type="market_pricing",
        evidence_category="market_pricing",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # Same source and class, but the claim is the compiler's own valuation ratio for the index
    # (P/E and dividend yield on two share-capital bases) rather than a traded price.
    "csindex_index_valuation": Posture(
        source_id="csindex_index_api",
        source_class="market_price_and_trading",
        authority_level="L2",
        claim_type="market_pricing",
        evidence_category="market_pricing",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # Stock Connect turnover as published by each exchange: official aggregate market
    # statistics, so L2 like the margin rows, and never a substitute for the net flow that the
    # exchanges stopped publishing on 2024-08-16.
    "sse_northbound": Posture(
        source_id="sse_northbound_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "szse_northbound": Posture(
        source_id="szse_northbound_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # Exchange-hosted announcement indexes: the same issuer filings the CNINFO channel reads,
    # from the listing venue itself, so the two can be cross-checked rather than trusted singly.
    "sse_announcement": Posture(
        source_id="sse_announcement_api",
        source_class="issuer_primary_disclosure",
        authority_level="L1",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "szse_announcement": Posture(
        source_id="szse_announcement_api",
        source_class="issuer_primary_disclosure",
        authority_level="L1",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # Shareholder count lifted from the filed periodic report: a metric the issuer states
    # outright, so L1 reported_fact rather than a derived figure. Same source row as the
    # announcement index, because the body comes from that same filing.
    "cninfo_holders": Posture(
        source_id="cninfo_announcement_api",
        source_class="issuer_primary_disclosure",
        authority_level="L1",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # Filed IR-activity record (投资者关系活动记录表): what the company said in a 调研 / 业绩说明会.
    # The channel is the issuer's own filing (L1) and the content is company commentary, so the
    # claim stays company_claim / company_statement exactly as on the 互动易 platform.
    "cninfo_ir_activity": Posture(
        source_id="cninfo_announcement_api",
        source_class="issuer_primary_disclosure",
        authority_level="L1",
        claim_type="company_claim",
        evidence_category="company_statement",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # FRED and BEA: the official US macro publishers behind a free key. L2 fact-grade like
    # BLS, but the credential is mandatory (see config.require_api_key), so a missing key is
    # a labelled setup gap rather than a silent empty read.
    "fred": Posture(
        source_id="fred_macro_series_api",
        source_class="official_macro_and_industry",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free_with_key",
        latency_class="delayed",
    ),
    "bea": Posture(
        source_id="bea_data_api",
        source_class="official_macro_and_industry",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free_with_key",
        latency_class="delayed",
    ),
    # Official futures exchanges publishing their own member-position rankings. The exchange
    # is the controlling source for its own market, so this is genuine L2 - unlike the
    # vendor's `futures.*` reads, which relay the same tables at L5.
    "shfe_member_rank": Posture(
        source_id="shfe_member_rank_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "czce_member_rank": Posture(
        source_id="czce_member_rank_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "cffex_member_rank": Posture(
        source_id="cffex_member_rank_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # 分红送配: the relay's view of the issuer's own dividend declaration. L5 because the
    # issuer's formal disclosure (cninfo/exchange) remains the controlling source.
    "em_dividend": Posture(
        source_id="eastmoney_dividend_api",
        source_class="market_data",
        authority_level="L5",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # 大宗交易: the relay's view of the exchanges' negotiated block prints. L5 because the
    # exchanges published the same prints and remain controlling.
    "em_block_trade": Posture(
        source_id="eastmoney_block_trade_api",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # FINRA's own consolidated short interest file: the regulator publishes it, keyless, so this
    # is L2 and corrects the gap list's "no free source" for US names.
    "finra_short_interest": Posture(
        source_id="finra_short_interest_api",
        source_class="regulatory_and_exchange",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # 龙虎榜: the vendor's structured view of the exchanges' daily seat disclosure. L5 because
    # the SSE/SZSE published the same lists and remain the controlling source.
    "hithink_dragon_tiger": Posture(
        source_id="hithink_finance_api",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free_with_key",
        latency_class="delayed",
    ),
    # CBOE delayed quotes carry the published 30-day implied volatility, which the history
    # files do not; still the exchange, still keyless, still L2.
    "cboe_quote": Posture(
        source_id="cboe_volatility_api",
        source_class="market_price_and_trading",
        authority_level="L2",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # CBOE volatility indices: the exchange that calculates the index publishes the history
    # file, so this is an official L2 statistic rather than the L5 relayed price.
    "cboe_volatility": Posture(
        source_id="cboe_volatility_api",
        source_class="market_price_and_trading",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # The US Treasury's own public API and the CFTC's own COT endpoint: keyless official
    # statistics, so L2 fact-grade rather than a relay of the same ground.
    "treasury_fiscal": Posture(
        source_id="treasury_fiscal_api",
        source_class="official_macro_and_industry",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "cftc_cot": Posture(
        source_id="cftc_cot_api",
        source_class="official_macro_and_industry",
        authority_level="L2",
        claim_type="fact",
        evidence_category="verified_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    # Futures warehouse receipts and basis: aggregator tier, and labelled as such because the
    # exchange stock files are unreachable (see futures_inventory's module docstring).
    "em_futures_inventory": Posture(
        source_id="eastmoney_futures_inventory_api",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
    "hithink_futures_basis": Posture(
        source_id="hithink_finance_api",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free_with_key",
        latency_class="delayed",
    ),
    # 限售股份变动情况 lifted from the periodic report body: the issuer's own table of
    # restricted-share movement (年初/解除/增加/年末 plus the 限售原因 and 解除日期), so a
    # reported metric at L1 - the same source row as the announcement index, because the
    # body comes from that same filing.
    "cninfo_lockup_change": Posture(
        source_id="cninfo_announcement_api",
        source_class="issuer_primary_disclosure",
        authority_level="L1",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="filing_cycle",
    ),
    # 十大股东 / 十大流通股东 relayed by an aggregator. The content is the issuer's own
    # disclosed table, but the publisher is the relay rather than the exchange or the portal,
    # so the tier is L5 reported_fact with a medium-confidence aggregator speaker - the same
    # treatment em_insider and em_lockup get, and every record names the issuer's filing as
    # the L1 route to the same numbers.
    "em_shareholders": Posture(
        source_id="eastmoney_shareholders_api",
        source_class="market_price_and_trading",
        authority_level="L5",
        claim_type="reported_metric",
        evidence_category="reported_fact",
        access_method="public_api",
        acquisition_mode="free",
        latency_class="delayed",
    ),
}


@dataclass
class FetchResult:
    """What an adapter returns: claim-level records + an optional bulk series.

    ``records`` become evidence-log rows (a handful of point claims). ``series``
    is an optional time-series dataset (e.g. full OHLCV) too large for the
    evidence log — it is written as a side CSV that the manifest points at.
    Shape: ``{"name": str, "columns": list[str], "rows": list[dict]}``.
    """

    records: list["CanonicalRecord"]
    series: Optional[dict] = None


def derived_posture(base: Posture) -> Posture:
    """Posture for a Mira-computed number: derived_calculation / L6 / derived."""
    return Posture(
        source_id=base.source_id,
        source_class="mira_derived_analysis",
        authority_level="L6",
        claim_type="derived_calculation",
        evidence_category="inference",
        access_method=base.access_method,
        acquisition_mode="derived",
        latency_class=base.latency_class,
    )


@dataclass
class CanonicalRecord:
    """One canonical datum, carrying everything emit/evidence code needs.

    ``derived`` is the hinge for the ledger rule (arch doc §8): issuer-disclosed
    values are ``derived=False`` (reported, no ledger); only numbers Mira itself
    computes and that affect a judgment are ``derived=True`` (ledger required).
    """

    family: str
    research_object: str
    market_scope: str
    metric: str                       # canonical field / claim_area
    value: Any
    unit: str
    period: str                       # e.g. "FY2025" or "2026-03-31"
    period_type: str                  # point_in_time | fiscal_period | ...
    as_of_date: str                   # retrieval as-of (YYYY-MM-DD)
    source_date: str                  # filing / observation date (YYYY-MM-DD)
    posture: Posture
    url_or_path: str
    currency: Optional[str] = None
    claim_text: str = ""
    confidence: str = "medium"
    freshness_status: str = "current"  # current/acceptable_for_period/preliminary/stale/unknown
    # Derived-only fields (ignored unless derived=True):
    derived: bool = False
    upstream_sources: str = ""         # ";"-joined source_ids feeding the calc
    formula: str = ""
    cross_check: str = ""
    provenance: dict = field(default_factory=dict)  # raw vendor tag/form/fy/fp

    def __post_init__(self) -> None:
        if self.family not in CANONICAL_FAMILIES:
            raise ValueError(f"unknown canonical family: {self.family!r}")
        if self.derived and self.posture.claim_type != "derived_calculation":
            # A Mira-computed, judgment-affecting number must be claim-typed
            # derived_calculation so validate_repo.py:678 (Formula/ledger
            # requirement) actually fires. Promote the posture as a safety net.
            self.posture = derived_posture(self.posture)
        if not self.claim_text:
            self.claim_text = (
                f"{self.research_object} {self.metric} = "
                f"{self.value} {self.unit} ({self.period})"
            )

    @property
    def claim_type(self) -> str:
        return self.posture.claim_type
