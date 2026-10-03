#!/usr/bin/env python3
"""Regression tests for the CNINFO PDF body-extraction path.

Offline: the HTTP layer is patched and ``pypdf`` is replaced by a stub module, so the suite
runs without the optional dependency installed. What is pinned here is the *honesty* of the
path: every way of failing returns a machine-routable gap token instead of an empty string,
because an empty extraction reads exactly like "the filed document does not contain this
section" - and that is the one confusion Mira's ingestion rules forbid. The happy path also
proves that truncation is reported rather than hidden.
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mira_data import net
from tools.mira_data.adapters import cninfo_disclosure as cn


class _FakePage:
    def __init__(self, text=None, error=None):
        self._text = text
        self._error = error

    def extract_text(self):
        if self._error:
            raise RuntimeError(self._error)
        return self._text


class _FakeReader:
    def __init__(self, pages):
        self.pages = pages


def _fake_pypdf(pages):
    module = types.ModuleType("pypdf")

    def _reader(_stream):
        if isinstance(pages, Exception):
            raise pages
        return _FakeReader(pages)

    module.PdfReader = _reader
    return module


def _extract(pages, *, max_pages=40, max_bytes=cn.PDF_MAX_BYTES, raw=b"%PDF-1.4 fake"):
    with mock.patch.dict(sys.modules, {"pypdf": _fake_pypdf(pages)}), \
            mock.patch.object(cn.net, "get", return_value=raw):
        return cn.extract_pdf_text("https://static.cninfo.com.cn/finalpage/x.PDF",
                                   max_pages=max_pages, max_bytes=max_bytes)


def test_happy_path_reports_truncation_and_char_count() -> None:
    pages = [_FakePage(f"第{index}页正文") for index in range(1, 11)]
    got = _extract(pages, max_pages=4)
    assert got["page_count"] == 10 and got["pages_read"] == 4
    assert got["truncated"] is True, "a partial read must say so"
    assert got["chars"] == len(got["text"])
    assert "第1页正文" in got["text"] and "第4页正文" in got["text"]
    assert "第5页正文" not in got["text"]
    assert got["url"].endswith(".PDF")

    full = _extract(pages, max_pages=10)
    assert full["truncated"] is False and full["pages_read"] == full["page_count"]
    print("ok text, page counts, truncation and character count are all reported")


def test_missing_pypdf_is_a_dependency_gap_not_an_empty_document() -> None:
    with mock.patch.dict(sys.modules, {"pypdf": None}):
        try:
            cn.extract_pdf_text("https://static.cninfo.com.cn/finalpage/x.PDF")
        except net.FetchError as exc:
            assert "cninfo_dependency_gap" in str(exc) and "pypdf" in str(exc)
        else:
            raise AssertionError("a missing PDF library must be a labelled gap")
    print("ok a missing pypdf reports cninfo_dependency_gap")


def test_oversized_and_unparsable_bodies_are_named() -> None:
    try:
        _extract([_FakePage("x")], max_bytes=10, raw=b"x" * 50)
    except net.FetchError as exc:
        assert "cninfo_pdf_too_large" in str(exc) and "50 bytes" in str(exc)
    else:
        raise AssertionError("an oversized body must be refused with its size")

    try:
        _extract(ValueError("not a pdf"))
    except net.FetchError as exc:
        assert "cninfo_pdf_unparsable" in str(exc)
    else:
        raise AssertionError("unparsable bytes must be a labelled gap")
    print("ok oversized and unparsable bodies carry their own tokens")


def test_image_only_scan_is_a_text_gap_never_an_empty_success() -> None:
    try:
        _extract([_FakePage("") for _ in range(3)])
    except net.FetchError as exc:
        assert "cninfo_pdf_text_gap" in str(exc)
        assert "image-only scan" in str(exc), "the reason must be stated, not implied"
    else:
        raise AssertionError("a scan without a text layer must not look like an empty section")

    got = _extract([_FakePage(""), _FakePage("有文字的一页")])
    assert "有文字的一页" in got["text"], "one empty page must not fail the whole read"
    print("ok a scan-only filing is a labelled gap while a blank page alone is tolerated")


def test_one_bad_page_does_not_discard_the_others() -> None:
    pages = [_FakePage("前言"), _FakePage(error="broken xref"), _FakePage("结论")]
    got = _extract(pages)
    assert "前言" in got["text"] and "结论" in got["text"]
    assert "[[page 2 extract failed" in got["text"], "the loss must be visible in the text"
    print("ok a single failing page is marked in place instead of losing the document")


def test_find_announcements_filters_and_builds_the_pdf_url() -> None:
    rows = [
        {"announcementTitle": "贵州茅台2026年<em>年度报告</em>", "adjunctUrl": "finalpage/a.PDF",
         "announcementTime": 1786723200000, "secCode": "600519", "announcementId": "1"},
        {"announcementTitle": "贵州茅台关于分红的公告", "adjunctUrl": "finalpage/b.PDF",
         "announcementTime": 1786723200000, "secCode": "600519", "announcementId": "2"},
    ]
    with mock.patch.object(cn, "_iter_announcements", return_value=iter(rows)), \
            mock.patch.object(cn, "resolve_org_id", return_value="gssh0600519"):
        found = cn.find_announcements("600519", since="2026-01-01", until="2026-10-02",
                                      title_contains="年度报告")
    assert len(found) == 1, "the title filter must drop the unrelated filing"
    item = found[0]
    assert item["announcementTitle"] == "贵州茅台2026年年度报告", "em tags are cleaned"
    assert item["pdf_url"] == cn.PDF_BASE + "finalpage/a.PDF"
    assert item["date_bj"] == "2026-08-15"

    with mock.patch.object(cn, "_iter_announcements", return_value=iter(rows)), \
            mock.patch.object(cn, "resolve_org_id", return_value="gssh0600519"):
        assert len(cn.find_announcements("600519", since="2026-01-01",
                                         until="2026-10-02")) == 2
    print("ok filing lookup filters by title and hands over a ready PDF url")


def test_pdf_caps_are_explicit_and_bounded() -> None:
    assert cn.PDF_MAX_BYTES == 40 * 1024 * 1024, "the size cap is a deliberate constant"
    assert 1 <= cn.PDF_DEFAULT_MAX_PAGES <= 200, "the page cap stays a bounded default"
    assert cn.PDF_BASE.endswith("cninfo.com.cn/"), "the PDF host is the verified one"
    print("ok the PDF size and page caps are explicit constants")


def main() -> int:
    test_happy_path_reports_truncation_and_char_count()
    test_missing_pypdf_is_a_dependency_gap_not_an_empty_document()
    test_oversized_and_unparsable_bodies_are_named()
    test_image_only_scan_is_a_text_gap_never_an_empty_success()
    test_one_bad_page_does_not_discard_the_others()
    test_find_announcements_filters_and_builds_the_pdf_url()
    test_pdf_caps_are_explicit_and_bounded()
    print("mira_data_cninfo_pdf_tests: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
