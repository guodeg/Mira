#!/usr/bin/env python3
"""Regression tests for the news-pointer channel (2c: discovery only).

The load-bearing test here is the negative one: this channel must stay out of the evidence
layer. It therefore asserts that no canonical records exist, that the writer produces
``news-pointers.csv`` and nothing else (no manifest, no ingestion log, and above all no
evidence log), and that every pointer routes to the primary disclosure read. The rest pins
the JSONP parsing, the title cleaning, the window/dedupe/cap behaviour and the registry rows.
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import urllib.parse
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import news_pointers as np_


ITEMS = [
    {"date": "2026-09-30 16:23:00", "mediaName": "证券时报网",
     "title": "9月30日18只股盘后交易额超500万元",
     "url": "http://stock.eastmoney.com/a/202609303887719317.html"},
    {"date": "2026-09-28 15:48:00", "mediaName": "21世纪经济报道",
     "title": "<em>段永平</em>发帖称买了3万股，贵州茅台股价拉升翻红",
     "url": "http://finance.eastmoney.com/a/202609283885196115.html"},
    {"date": "2026-09-28 15:10:00", "mediaName": "财闻",
     "title": "越跌越买！&#34;十年之约&#34;后再度加仓",
     "url": "http://finance.eastmoney.com/a/202609283885221822.html"},
    {"date": "2026-09-28 15:09:00", "mediaName": "转载",
     "title": "同一篇文章被另一个栏目重复收录",
     "url": "http://finance.eastmoney.com/a/202609283885196115.html"},   # same URL: dedupe
    {"date": "2025-01-05 09:00:00", "mediaName": "旧闻",
     "title": "窗口之外的旧标题",
     "url": "http://finance.eastmoney.com/a/old.html"},
]


def _body(items=None) -> bytes:
    payload = {"code": 0, "result": {"cmsArticleWebOld": ITEMS if items is None else items}}
    return ("cb(" + json.dumps(payload, ensure_ascii=False) + ")").encode("utf-8")


def _fetch(items=None, **kwargs):
    with mock.patch.object(np_.net, "get", return_value=_body(items)):
        return np_.fetch_news_pointers("600519", as_of="2026-10-02", **kwargs)


def test_the_channel_produces_pointers_not_claims() -> None:
    pointers = _fetch()
    assert pointers, "expected live-shaped pointers from the canned payload"
    assert all(isinstance(pointer, dict) for pointer in pointers)
    assert not hasattr(np_, "CanonicalRecord"), \
        "a pointer channel must not construct canonical records"
    assert not hasattr(np_, "POSTURES"), \
        "a pointer channel must not carry an evidence posture"
    record = pointers[0]
    assert set(record) == set(np_.POINTER_COLUMNS)
    assert record["thscode"] == "600519.SH"
    print("ok the channel yields plain pointer rows and constructs no canonical records")


def test_writer_emits_only_the_pointer_csv() -> None:
    pointers = _fetch()
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        path = np_.write_pointers(pointers, out)
        written = sorted(p.name for p in Path(out).iterdir())
    assert written == ["news-pointers.csv"], written
    assert path.name == "news-pointers.csv"
    for forbidden in ("evidence-log.csv", "ingestion-log.csv", "dataset-manifest.json",
                      "calculation-ledger.csv"):
        assert forbidden not in written, f"{forbidden} must not be written by a pointer run"
    print("ok a run writes news-pointers.csv and nothing that looks like a bundle")


def test_jsonp_parsing_cleaning_window_and_cap() -> None:
    pointers = _fetch()
    titles = [pointer["title"] for pointer in pointers]
    # <em> stripped, entities unescaped, and the 2025 item filtered by the 30-day window
    assert "<em>" not in "".join(titles) and "旧标题" not in "".join(titles)
    assert any("段永平发帖称" in title for title in titles)
    assert any('"十年之约"后再度加仓' in title for title in titles)
    # dedupe by URL: the fourth row repeats the second row's link
    assert "重复收录" not in "".join(titles), "the duplicate URL must be dropped"
    urls = [pointer["url"] for pointer in pointers]
    assert len(urls) == len(set(urls)) == 3, urls
    assert [pointer["date"] for pointer in pointers] == ["2026-09-30", "2026-09-28", "2026-09-28"]

    assert len(_fetch(limit=1)) == 1
    print("ok JSONP parses, titles are cleaned, and window/dedupe/cap all hold")


def test_pointer_routes_to_the_primary_disclosure_read() -> None:
    pointers = _fetch()
    hint = pointers[0]["primary_route_hint"]
    assert hint.startswith("mira_data fetch cninfo_announcements 600519 --since ")
    assert "--until 2026-10-05" in hint, hint
    assert "2026-09-25" in hint, hint
    print("ok each pointer carries the L1 read to confirm it around that date")


def test_payload_failures_degrade_to_a_labelled_gap() -> None:
    with mock.patch.object(np_.net, "get", return_value=b"<html>nope</html>"):
        try:
            np_.fetch_news_pointers("600519", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "news_source_gap" in str(exc) and "non-JSONP" in str(exc)
        else:
            raise AssertionError("a non-JSONP body must be a labelled source gap")

    with mock.patch.object(np_.net, "get", return_value=b'cb({"code":500,"result":{}})'):
        try:
            np_.fetch_news_pointers("600519", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "news_source_gap" in str(exc)
        else:
            raise AssertionError("a vendor error code must be a labelled source gap")

    try:
        _fetch(items=[])
    except net.FetchError as exc:
        assert "news_source_gap" in str(exc) and "widen --days" in str(exc)
    else:
        raise AssertionError("no headlines must be a labelled source gap")
    print("ok non-JSONP, vendor errors and empty results all degrade with a clear token")


def test_registry_rows_are_registered_and_stay_out_of_the_evidence_layer() -> None:
    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = list(csv.DictReader(fh))
    row = next((entry for entry in registry
                if entry["source_id"] == "eastmoney_news_search_api"), None)
    assert row is not None, "the news pointer source must be registered"
    assert sum(1 for entry in registry
               if entry["source_id"] == "eastmoney_news_search_api") == 1
    assert row["authority_level"] == "L4" and row["content_type"] == "sentiment"
    assert "pointer" in row["notes"].lower() and "no evidence-log" in row["notes"].lower()

    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {entry["source_id"]: entry for entry in csv.DictReader(fh)}
    assert mapping["eastmoney_news_search_api"]["source_class"] == "professional_media"
    assert mapping["eastmoney_news_search_api"]["review_status"] == "reviewed"
    print("ok the source is registered at L4 with a 1:1 class-map row marked discovery-only")


def test_the_search_request_shape_is_the_verified_one() -> None:
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _body()

    with mock.patch.object(np_.net, "get", side_effect=fake_get):
        np_.fetch_news_pointers("600519", as_of="2026-10-02")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)
    param = json.loads(query["param"][0])
    assert query["cb"] == ["cb"]
    assert param["keyword"] == "600519"
    assert param["type"] == ["cmsArticleWebOld"]
    assert param["param"]["cmsArticleWebOld"]["preTag"] == "<em>"
    print("ok the request keeps the probed search contract (cb + json param envelope)")


def main() -> int:
    test_the_channel_produces_pointers_not_claims()
    test_writer_emits_only_the_pointer_csv()
    test_jsonp_parsing_cleaning_window_and_cap()
    test_pointer_routes_to_the_primary_disclosure_read()
    test_payload_failures_degrade_to_a_labelled_gap()
    test_registry_rows_are_registered_and_stay_out_of_the_evidence_layer()
    test_the_search_request_shape_is_the_verified_one()
    print("mira_data_news_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
