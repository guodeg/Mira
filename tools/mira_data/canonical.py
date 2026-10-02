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
