"""News **pointers** — discovery only, deliberately not an evidence channel.

This is the ``2c`` design decision: news is ingested as a *pointer* (headline, outlet,
timestamp, URL, plus the primary route to check), never as a claim. Consequences, all
intentional:

- No ``CanonicalRecord`` is produced, so nothing lands in ``evidence-log.csv`` and no
  ``claim_area`` family is invented for media. The repo's existing media sources are
  registered at L4 with ``content_type=sentiment``/``opinion`` and carry no canonical
  family; this channel keeps that convention instead of widening the protocol.
- The output is one artifact, ``news-pointers.csv`` — no manifest, no ingestion log, and
  explicitly no evidence log. A pointer says "something happened, go read the primary";
  it is not a fact, not a market price and not an issuer claim, and the substrate refuses
  to pretend otherwise.
- Every row carries the **primary route** to check: the L1 command that reads the
  disclosure index around that headline's date. The value of news here is routing, and
  the claim that ends up in a memo should come from the primary source, not the article.
- A pointer that reports a rating or target change maps to the existing
  ``estimate_revision`` family through its own channel; one that reports a filing is
  already covered by the L1 index. Nothing here needs a new family.

Source contract (probed live 2026-10-02): Eastmoney search, keyless, JSONP-wrapped::

    GET https://search-api-web.eastmoney.com/search/jsonp?cb=cb&param=<urlencoded json>
    param = {"uid":"", "keyword":"600519", "type":["cmsArticleWebOld"], "client":"web",
             "clientType":"web", "clientVersion":"curr",
             "param":{"cmsArticleWebOld":{"searchScope":"default","sort":"default",
                      "pageIndex":1,"pageSize":30,"preTag":"<em>","postTag":"</em>"}}}
    -> cb({"code":0,"result":{"cmsArticleWebOld":[{"date","mediaName","title","url",...}]}})

Live example: 600519 returned 证券时报网 / 21世纪经济报道 headlines with dates and URLs.
The body is **not** stored beyond the headline: article text is third-party copyright, and
DATA_POLICY forbids turning this into a bulk crawl, so reads stay per-name and on demand.
"""

from __future__ import annotations

import csv
import datetime as _dt
import html
import json
import re
import urllib.parse
from pathlib import Path
from typing import Optional

from .. import config, net
from .hithink_finance import resolve_thscode

SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
ENDPOINT = "eastmoney://news-search/{symbol}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://so.eastmoney.com/",
}
DEFAULT_DAYS = 30
DEFAULT_LIMIT = 30
POINTER_COLUMNS = ("date", "outlet", "title", "url", "thscode", "primary_route_hint",
                   "retrieved_at")
_JSONP = re.compile(r"^[^(]*\((.*)\)\s*$", re.S)
_TAGS = re.compile(r"<[^>]+>")


def _setting(name: str, default: str) -> str:
    return (config.get(name) or default).strip()


def _limit(limit: Optional[int]) -> int:
    if limit is not None:
        return max(1, int(limit))
    try:
        return max(1, int(_setting("MIRA_NEWS_MAX_ITEMS", str(DEFAULT_LIMIT))))
    except ValueError:
        return DEFAULT_LIMIT


def _days(days: Optional[int]) -> int:
    if days is not None:
        return max(1, int(days))
    try:
        return max(1, int(_setting("MIRA_NEWS_WINDOW_DAYS", str(DEFAULT_DAYS))))
    except ValueError:
        return DEFAULT_DAYS


def fetch_news_pointers(symbol: str, *, days: Optional[int] = None,
                        limit: Optional[int] = None,
                        as_of: Optional[str] = None) -> list[dict]:
    """Headline pointers for one A-share name. Plain rows — never canonical records."""
    as_of = as_of or _dt.date.today().isoformat()
    thscode = resolve_thscode(symbol)
    code = thscode.split(".")[0]
    window = _days(days)
    cap = _limit(limit)

    param = {
        "uid": "", "keyword": code, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default", "pageIndex": 1,
            "pageSize": min(100, max(cap * 2, 20)),
            "preTag": "<em>", "postTag": "</em>",
        }},
    }
    url = SEARCH_URL + "?" + urllib.parse.urlencode(
        {"cb": "cb", "param": json.dumps(param, ensure_ascii=False)})
    raw = net.get(url, headers=HEADERS, retries=2, backoff=1.5).decode("utf-8", "replace")
    match = _JSONP.search(raw.strip())
    if not match:
        raise net.FetchError(
            f"news_source_gap: search endpoint answered non-JSONP payload from {SEARCH_URL}")
    try:
        payload = json.loads(match.group(1))
    except ValueError as exc:
        raise net.FetchError(f"news_source_gap: unparsable search payload ({exc})") from exc
    if payload.get("code") not in (0, "0", None):
        raise net.FetchError(f"news_source_gap: search returned code {payload.get('code')}")

    cutoff = (_dt.date.fromisoformat(as_of) - _dt.timedelta(days=window)).isoformat()
    pointers: list[dict] = []
    seen: set[str] = set()
    for item in ((payload.get("result") or {}).get("cmsArticleWebOld") or []):
        date = _date(item.get("date"))
        title = _clean(item.get("title"))
        link = str(item.get("url") or "").strip()
        if not date or not title or not link or date < cutoff:
            continue
        key = link or f"{date}|{title}"
        if key in seen:
            continue
        seen.add(key)
        pointers.append({
            "date": date,
            "outlet": _clean(item.get("mediaName")) or "unknown",
            "title": title,
            "url": link,
            "thscode": thscode,
            "primary_route_hint": primary_route_hint(code, date),
            "retrieved_at": as_of,
        })
        if len(pointers) >= cap:
            break
    if not pointers:
        raise net.FetchError(
            f"news_source_gap: no headlines for {thscode} in the last {window} days "
            "(the search index is per-name and on demand; widen --days or check the code)")
    return pointers


def primary_route_hint(code: str, date: str, *, slack_days: int = 5) -> str:
    """The L1 read that would confirm (or kill) whatever a headline is gesturing at."""
    try:
        day = _dt.date.fromisoformat(date)
    except (TypeError, ValueError):
        return f"mira_data fetch cninfo_announcements {code}"
    since = (day - _dt.timedelta(days=slack_days)).isoformat()
    until = (day + _dt.timedelta(days=slack_days)).isoformat()
    return f"mira_data fetch cninfo_announcements {code} --since {since} --until {until}"


def write_pointers(pointers: list[dict], out_dir: str | Path) -> Path:
    """Write ``news-pointers.csv`` and nothing else.

    Deliberately no manifest, no ingestion log and no evidence log: this directory is a
    discovery artifact, and a file that looks like a bundle would invite treating pointers
    as claims.
    """
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / "news-pointers.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(POINTER_COLUMNS))
        writer.writeheader()
        for pointer in pointers:
            writer.writerow({column: pointer.get(column, "") for column in POINTER_COLUMNS})
    return path


def _clean(text) -> str:
    value = html.unescape(str(text or ""))
    value = _TAGS.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def _date(text) -> str:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(text or "").strip())
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    try:
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return ""
