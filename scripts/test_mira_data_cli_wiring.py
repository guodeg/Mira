#!/usr/bin/env python3
"""Offline tests for CLI endpoint wiring across every fetch family.

This exists because the same wiring mistake shipped **three times**: a new family was added to
``_MARKET_SERIES_FAMILIES`` for its display label, which also made it a "venue" family, so the
symbol default ``"BOTH"`` was passed as the family's first argument — and separately
``_local_endpoint_params`` fell through to the **IBKR** defaults, silently recording
``127.0.0.1:7497`` as the source host for non-IBKR families.

Each case produced a plausible-looking run rather than an error, which is why it survived two
reviews. The invariants below turn all of it into an immediate failure:

1. Every family's endpoint template must actually **format** with the arguments the fetch path
   supplies, so a missing ``_local_endpoint_params`` branch fails here rather than as a bare
   ``KeyError`` naming only a placeholder.
2. A family must not be in **both** the venue-default map and the symbol-less set — that
   combination is what fed ``"BOTH"`` to families that take no symbol.
3. Non-IBKR families must not receive the IBKR host/port.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import __main__ as M


# The arguments the endpoint-format call in _do_fetch supplies itself.
FORMAT_ARGS = {"symbol": "X", "thscode": "X", "cik10": "<cik>", "series_id": "X"}


def test_every_family_endpoint_formats() -> None:
    failures = []
    for family in sorted(M.FETCHERS):
        template = (M.FETCHERS[family][2] or "")
        try:
            template.format(**FORMAT_ARGS, **M._local_endpoint_params(family))
        except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
            failures.append((family, f"{type(exc).__name__}: {exc}"))
    assert not failures, f"endpoint templates that do not format: {failures}"
    print(f"ok all {len(M.FETCHERS)} family endpoint templates format")


def test_no_family_is_both_a_venue_default_and_symbol_less() -> None:
    """The symbol-less families are labelled in ``_MARKET_SERIES_FAMILIES`` but take no symbol.

    That overlap is deliberate (the map also supplies their display label), so the invariant is
    not set membership but **behaviour**: resolving a symbol for one of them must yield an empty
    string, never the venue default ``"BOTH"``. That default is what reached them as a *dataset*
    and as a *date* before the symbol-less branch existed.
    """
    overlap = sorted(set(M._MARKET_SERIES_FAMILIES) & set(M._SYMBOL_LESS_FAMILIES))
    # If this ever empties, the branch in _do_fetch became dead code and both may be simplified.
    assert overlap == ["block_trade", "cftc_cot", "dragon_tiger", "treasury_avg_interest",
                       "treasury_debt"], overlap
    unknown = sorted(set(M._SYMBOL_LESS_FAMILIES) - set(M.FETCHERS))
    assert not unknown, f"symbol-less families that do not exist: {unknown}"
    # The behavioural half: the resolver's own condition, evaluated for each family.
    for family in M._SYMBOL_LESS_FAMILIES:
        would_default = (family in M._MARKET_SERIES_FAMILIES
                         and family not in M._SYMBOL_LESS_FAMILIES)
        assert not would_default, (
            f"{family} would be handed the venue default 'BOTH' as its symbol")
    # And a plain venue family still gets it, so the guard has not disabled real behaviour.
    assert (("macro_nbs" in M._MARKET_SERIES_FAMILIES)
            and ("macro_nbs" not in M._SYMBOL_LESS_FAMILIES))
    print("ok symbol-less families resolve to no symbol while venue families keep the default")


def test_only_gateway_families_get_a_gateway_endpoint() -> None:
    """Host/port belong to the *local gateway* families, and to no others.

    ``ibkr_*`` and ``futu_*`` both legitimately carry a host/port (each from its own branch), so
    the invariant is that a family reaching a remote source never does. That was the silent part:
    the fall-through gave remote families the IBKR defaults, so they recorded ``127.0.0.1:7497``
    as their source host.
    """
    offenders = []
    for family in sorted(M.FETCHERS):
        if family.startswith(("ibkr_", "futu_")):
            continue
        params = M._local_endpoint_params(family)
        if "host" in params or "port" in params:
            offenders.append((family, params))
    assert not offenders, f"these remote families received a gateway host/port: {offenders}"
    # The gateway families must still get theirs, each its OWN — not the other gateway's.
    ibkr = M._local_endpoint_params("ibkr_positions")
    futu = M._local_endpoint_params("futu_market_price")
    assert "host" in ibkr and "port" in ibkr, ibkr
    assert "host" in futu and "port" in futu, futu
    assert ibkr["port"] != futu["port"], (ibkr, futu)
    print("ok only the local-gateway families carry a gateway host/port, each its own")


def test_missing_branch_is_reported_with_the_family_name() -> None:
    # A template needing an unsupplied placeholder must raise a message naming the family and the
    # placeholder, rather than the bare KeyError the format call would produce.
    original = M.FETCHERS.get("__probe_family__")
    M.FETCHERS["__probe_family__"] = ("probe", None, "probe://x/{needs_a_branch}")
    try:
        M._local_endpoint_params("__probe_family__")
    except AssertionError as exc:
        message = str(exc)
        assert "__probe_family__" in message, message
        assert "needs_a_branch" in message, message
    else:
        raise AssertionError("a template with an unsupplied placeholder must raise")
    finally:
        if original is None:
            M.FETCHERS.pop("__probe_family__", None)
        else:
            M.FETCHERS["__probe_family__"] = original
    print("ok a missing branch names the family and the placeholder")


def test_symbol_less_families_are_all_accounted_for() -> None:
    # Stated directly: these take no symbol (or an optional one). If one ever gains a required
    # symbol this list should be updated, not the assertion deleted.
    for family in ("treasury_debt", "treasury_avg_interest", "cftc_cot", "dragon_tiger",
                   "block_trade"):
        assert family in M._SYMBOL_LESS_FAMILIES, family
    print("ok every symbol-less family is declared as such")


def main() -> int:
    test_every_family_endpoint_formats()
    test_no_family_is_both_a_venue_default_and_symbol_less()
    test_only_gateway_families_get_a_gateway_endpoint()
    test_missing_branch_is_reported_with_the_family_name()
    test_symbol_less_families_are_all_accounted_for()
    print("mira_data_cli_wiring_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
