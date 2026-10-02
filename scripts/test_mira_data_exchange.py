#!/usr/bin/env python3
"""Regression tests for the exchange-direct disclosure channel (SSE + SZSE).

Offline: HTTP is patched. The tests pin the routing (each name goes to its own listing venue,
and a Beijing name spends no request), the two vendors' different payload shapes, the
metadata-only policy that keeps the document as a link, the bounded window/cap, and the ports
of the two document hosts that the vendor returns as bare site paths.
"""

from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import exchange_disclosure as ex
from tools.mira_data.emit import emit_bundle


SSE_PAYLOAD = {"pageHelp": {"total": 3}, "result": [
    {"SECURITY_CODE": "600519", "SECURITY_NAME": "贵州茅台",
     "TITLE": "贵州茅台第五届董事会2026年度第三次会议决议公告",
     "URL": "/disclosure/listedinfo/announcement/c/new/2026-08-15/600519_20260815_ABC.pdf",
     "SSEDATE": "2026-08-15", "ADDDATE": "2026-08-14 20:41:26"},
    {"SECURITY_CODE": "600519", "SECURITY_NAME": "贵州茅台",
     "TITLE": "贵州茅台2026年半年度报告摘要",
     "URL": "/disclosure/listedinfo/announcement/c/new/2026-08-15/600519_20260815_DEF.pdf",
     "SSEDATE": "2026-08-15", "ADDDATE": "2026-08-14 20:41:27"},
    {"SECURITY_CODE": "600519", "SECURITY_NAME": "贵州茅台", "TITLE": "重复行",
     "URL": "/disclosure/listedinfo/announcement/c/new/2026-08-15/600519_20260815_ABC.pdf",
     "SSEDATE": "2026-08-15", "ADDDATE": "2026-08-14 20:41:28"},
]}

SZSE_PAYLOAD = {"announceCount": 2, "data": [
    {"id": "fc47bd25", "annId": 1225587230, "title": "平安银行：董事会决议公告",
     "publishTime": "2026-09-30 00:00:00", "attachFormat": "PDF",
     "attachPath": "/disc/disk03/finalpage/2026-09-30/67c83f70.PDF",
     "secCode": ["000001"], "secName": ["平安银行"]},
    {"id": "aaaa", "annId": 1225587231, "title": "平安银行：关于召开股东大会的通知",
     "publishTime": "2026-09-28 00:00:00", "attachFormat": "PDF",
     "attachPath": "/disc/disk03/finalpage/2026-09-28/11223344.PDF",
     "secCode": ["000001"], "secName": ["平安银行"]},
]}


def _run(symbol, *, payload=None, patch_kwargs=None, **kwargs):
    captured = {}

    def fake_get_json(url, **options):
        captured["url"] = url
        captured["method"] = "GET"
        return SSE_PAYLOAD if payload is None else payload

    def fake_post_json(url, body, **options):
        captured["url"] = url
        captured["method"] = "POST"
        captured["body"] = body
        return SZSE_PAYLOAD if payload is None else payload

    with mock.patch.object(ex.net, "get_json", side_effect=fake_get_json), \
            mock.patch.object(ex.net, "post_json", side_effect=fake_post_json):
        result = ex.fetch_exchange_announcements(symbol, as_of="2026-10-02", **kwargs)
    return result, captured


def test_shanghai_names_use_the_sse_endpoint() -> None:
    result, captured = _run("600519", since="2026-07-01", until="2026-10-02", max_items=10)
    assert captured["method"] == "GET" and ex.SSE_URL in captured["url"]
    assert "productId=600519" in captured["url"]
    assert "securityType=0101%2C120100%2C020100%2C020200%2C120200" in captured["url"]
    assert "beginDate=2026-07-01" in captured["url"] and "endDate=2026-10-02" in captured["url"]
    assert "pageHelp.pageSize=10" in captured["url"]
    assert len(result.records) == 2, "the repeated document path must be de-duplicated"
    record = result.records[0]
    assert record.family == "issuer_disclosure" and record.research_object == "600519.SH"
    assert record.metric == "announcement" and record.unit == "announcement_id"
    assert record.period == "2026-08-15" and record.source_date == "2026-08-15"
    posture = record.posture
    assert posture.source_id == "sse_announcement_api"
    assert posture.authority_level == "L1" and posture.claim_type == "fact"
    assert record.provenance["exchange"] == "SSE"
    assert record.provenance["channel"] == "exchange_direct"
    assert record.provenance["bodyRetrieval"] == "link_only"
    assert record.provenance["documentUrl"].startswith(ex.SSE_DOC_HOST)
    assert record.provenance["disclosureTime"] == "2026-08-14 20:41:26"
    assert result.series is None
    print("ok a Shanghai name reads the SSE index, dedupes and keeps the body as a link")


def test_shenzhen_names_use_the_szse_post_contract() -> None:
    result, captured = _run("000001", since="2026-07-01", until="2026-10-02", max_items=25)
    assert captured["method"] == "POST" and ex.SZSE_URL in captured["url"]
    body = captured["body"]
    assert body["seDate"] == ["2026-07-01", "2026-10-02"]
    assert body["channelCode"] == ["listedNotice_disc"] and body["stock"] == ["000001"]
    assert body["pageSize"] == 25 and body["pageNum"] == 1
    assert len(result.records) == 2
    record = result.records[0]
    assert record.research_object == "000001.SZ" and record.value == "1225587230"
    assert record.period == "2026-09-30", "publishTime is a datetime and only the day is used"
    assert record.posture.source_id == "szse_announcement_api"
    assert record.provenance["documentUrl"] == (
        ex.SZSE_DOC_HOST + "/disc/disk03/finalpage/2026-09-30/67c83f70.PDF")
    assert record.provenance["documentFormat"] == "PDF"
    assert record.provenance["secCodes"] == ["000001"]
    assert "not fetched or redistributed" in record.provenance["publisherNote"]
    print("ok a Shenzhen name posts the reviewed body shape and resolves the document host")


def test_routing_and_bounds() -> None:
    calls = {"n": 0}

    def spy(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("a Beijing name must not spend a request")

    with mock.patch.object(ex.net, "get_json", side_effect=spy), \
            mock.patch.object(ex.net, "post_json", side_effect=spy):
        try:
            ex.fetch_exchange_announcements("830799", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "exchange_disclosure_source_gap" in str(exc) and "CNINFO" in str(exc)
        else:
            raise AssertionError("expected a labelled gap for a Beijing name")
    assert calls["n"] == 0

    result, _captured = _run("000001", since="2026-09-01", until="2026-10-02", max_items=1)
    assert len(result.records) == 1, "the item cap must bound the returned rows"

    with mock.patch.object(ex.net, "get_json", return_value={"pageHelp": {"total": 0},
                                                             "result": []}):
        try:
            ex.fetch_exchange_announcements("600519", as_of="2026-10-02")
        except net.FetchError as exc:
            assert "exchange_disclosure_source_gap" in str(exc) and "widen --since" in str(exc)
        else:
            raise AssertionError("an empty window must be a gap that says how to widen it")
    print("ok routing is per venue, Beijing costs nothing, and caps/gaps are explicit")


def test_rows_without_a_usable_document_are_dropped() -> None:
    dirty = {"pageHelp": {"total": 2}, "result": [
        {"SECURITY_CODE": "600519", "TITLE": "无链接的公告", "URL": "", "SSEDATE": "2026-08-15"},
        {"SECURITY_CODE": "600519", "TITLE": "", "URL": "/disclosure/listedinfo/x.pdf",
         "SSEDATE": "2026-08-15"},
        {"SECURITY_CODE": "600519", "TITLE": "正常公告",
         "URL": "/disclosure/listedinfo/announcement/c/new/2026-08-15/ok.pdf",
         "SSEDATE": "2026-08-15"},
    ]}
    result, _captured = _run("600519", payload=dirty, since="2026-08-01", max_items=10)
    assert len(result.records) == 1
    assert "正常公告" in result.records[0].claim_text
    print("ok incomplete rows are skipped instead of producing a claim without a source link")


def test_emitted_rows_are_l1_and_registry_rows_exist() -> None:
    result, _captured = _run("000001", since="2026-09-01", until="2026-10-02", max_items=10)
    scratch = ROOT / "local"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as out:
        emitted = emit_bundle(result.records, out_dir=out, research_object="000001.SZ",
                              market_scope="CN",
                              endpoint=ex.endpoint_for(ex.SZSE_ENDPOINT, "000001.SZ"))
        assert emitted["calculation_ledger"] is None
        with open(Path(out) / "evidence-log.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    assert {row["claim_type"] for row in rows} == {"fact"}
    assert {row["authority_level"] for row in rows} == {"L1"}
    assert {row["verification_status"] for row in rows} == {"verified"}
    assert {row["source_id"] for row in rows} == {"szse_announcement_api"}
    assert {row["source_speaker"] for row in rows} == {"company"}
    assert all("bodyRetrieval=link_only" in row["notes"] for row in rows)

    with (ROOT / "data" / "source-registry.csv").open(encoding="utf-8", newline="") as fh:
        registry = [row for row in csv.DictReader(fh)]
    with (ROOT / "data" / "source-class-map.csv").open(encoding="utf-8", newline="") as fh:
        mapping = {row["source_id"]: row for row in csv.DictReader(fh)}
    for source_id, city in (("sse_announcement_api", "Shanghai"),
                            ("szse_announcement_api", "Shenzhen")):
        rows = [row for row in registry if row["source_id"] == source_id]
        assert len(rows) == 1 and rows[0]["authority_level"] == "L1"
        assert "metadata only" in rows[0]["notes"].lower()
        assert mapping[source_id]["source_class"] == "issuer_primary_disclosure"
        assert city in rows[0]["publisher"] and city in rows[0]["source_name"]
    print("ok exchange rows emit as L1 verified issuer disclosure with both sources registered")


def main() -> int:
    test_shanghai_names_use_the_sse_endpoint()
    test_shenzhen_names_use_the_szse_post_contract()
    test_routing_and_bounds()
    test_rows_without_a_usable_document_are_dropped()
    test_emitted_rows_are_l1_and_registry_rows_exist()
    print("mira_data_exchange_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
