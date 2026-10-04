"""Minimal stdlib HTTP helper for Mira data adapters.

No third-party dependencies: just ``urllib``. A descriptive User-Agent is
required by some official endpoints (notably SEC), so it is configurable via
``MIRA_HTTP_UA`` and defaults to a contactable string.
"""

from __future__ import annotations

import http.client
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from . import config

DEFAULT_TIMEOUT = 30
# 307 is the Wangsu-WAF challenge the NBS library answers when a request arrives without
# its cookie: the challenge response carries the cookie, so a retry through a session that
# kept it succeeds. Treating it as transient is what turns an intermittent failure into a
# slow success rather than a source gap.
RETRY_STATUSES = (307, 429, 500, 502, 503, 504)


class FetchError(config.ConfigGap):
    """Raised when an adapter cannot retrieve usable data.

    Adapters catch this and degrade the conclusion to a ``source_gap`` token
    rather than fabricating data. It is the type ``config`` raises for a missing API key
    (see ``config.bind_error_factory``), so those gaps land in the same handler.
    """

    def __init__(self, message: str, *, status: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status = status
        self.url = url


# Register the catchable type with the config layer. Without this, a missing key raises
# the base ``ConfigGap``, which ``except FetchError`` does NOT catch.
config.bind_error_factory(lambda message: FetchError(message))


def get(url: str, *, headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
        retries: int = 2, backoff: float = 1.5) -> bytes:
    """GET ``url`` and return raw bytes, retrying transient failures.

    Retries on 429 and 5xx; raises :class:`FetchError` on a hard failure so the
    caller can degrade gracefully.
    """
    hdrs = _base_headers(headers)
    return _send(lambda: urllib.request.Request(url, headers=hdrs), url, timeout, retries, backoff)


def get_json(url: str, **kwargs) -> dict:
    """GET ``url`` and parse JSON, raising :class:`FetchError` on bad payloads."""
    return _as_json(get(url, **kwargs), url)


def post_form_json(url: str, data: dict, *, headers: dict | None = None,
                   timeout: int = DEFAULT_TIMEOUT, retries: int = 2,
                   backoff: float = 1.5) -> dict:
    """POST an ``x-www-form-urlencoded`` body and parse JSON.

    Same retry and failure policy as :func:`get`. Some portals only expose an
    AJAX endpoint rather than a documentable REST API (CNINFO's announcement
    query is one), so the form POST has to be a first-class path rather than a
    special case inside an adapter.
    """
    hdrs = _base_headers(headers)
    hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    body = urllib.parse.urlencode(data).encode("utf-8")
    raw = _send(lambda: urllib.request.Request(url, data=body, headers=hdrs),
                url, timeout, retries, backoff)
    return _as_json(raw, url)


def post_json(url: str, payload: dict, *, headers: dict | None = None,
              timeout: int = DEFAULT_TIMEOUT, retries: int = 2,
              backoff: float = 1.5) -> dict:
    """POST a JSON body and parse the JSON response.

    The NBS data library is the reason this exists: its retired API was a GET, and the
    replacement only answers a POST with a JSON envelope (a GET comes back as an
    anti-bot HTML page, which is easy to misread as blocking rather than as a wrong
    verb). Same retry and failure policy as :func:`get`.
    """
    hdrs = _base_headers(headers)
    hdrs.setdefault("Content-Type", "application/json;charset=UTF-8")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    raw = _send(lambda: urllib.request.Request(url, data=body, headers=hdrs),
                url, timeout, retries, backoff)
    return _as_json(raw, url)


def post_form_text(url: str, data: dict, *, headers: dict | None = None,
                   timeout: int = DEFAULT_TIMEOUT, retries: int = 2,
                   backoff: float = 1.5) -> str:
    """POST an ``x-www-form-urlencoded`` body and return the decoded text.

    Some AJAX endpoints answer with HTML fragments rather than JSON (上证e互动's
    company feed is one), which still has to go through the same retry policy.
    """
    hdrs = _base_headers(headers)
    hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    body = urllib.parse.urlencode(data).encode("utf-8")
    raw = _send(lambda: urllib.request.Request(url, data=body, headers=hdrs),
                url, timeout, retries, backoff)
    return raw.decode("utf-8", errors="replace")


def _base_headers(headers: dict | None) -> dict:
    hdrs = {"User-Agent": config.contact_ua()[0], "Accept-Encoding": "gzip, deflate"}
    if headers:
        hdrs.update(headers)
    return hdrs


# Query-parameter names that carry a credential. A URL that is fine to *send* is not fine
# to *record*: provenance and evidence-log URLs are tracked artifacts, so a key embedded in
# one ends up committed. That happened for real — a live FRED fetch wrote `api_key=...`
# into evidence-log.csv, caught by reading the emitted row rather than by a test.
CREDENTIAL_PARAMS = (
    "api_key", "apikey", "api-key", "key", "userid", "user_id", "token", "access_token",
    "secret", "password", "pwd", "auth", "authorization", "client_secret", "subscription-key",
)


def redact_url(url: str, *, params: tuple[str, ...] = CREDENTIAL_PARAMS) -> str:
    """Return ``url`` with any credential-bearing query value replaced by ``REDACTED``.

    Provider-agnostic on purpose: the name is matched case-insensitively anywhere in the
    query string, so a new adapter inherits the protection without opting in.
    """
    if not url or "?" not in url:
        return url
    head, _, query = url.partition("?")
    lowered = {name.lower() for name in params}
    parts = []
    for chunk in query.split("&"):
        if not chunk:
            continue
        name, sep, _value = chunk.partition("=")
        parts.append(f"{name}{sep}REDACTED" if name.strip().lower() in lowered else chunk)
    return head + "?" + "&".join(parts)


class Session:
    """Cookie-preserving session for portals that gate on a challenge cookie.

    The NBS data library sits behind a Wangsu WAF: a request without its cookie is
    answered with an intermittent 307, and the site only settles once the challenge
    cookie (``wzws_cid``) is presented. Warming up on the site's own page once, and
    keeping cookies for the calls that follow, is the difference between a fetch that
    works and one that randomly reports a source gap. Same retry policy as the module
    functions.
    """

    def __init__(self, *, headers: dict | None = None, warmup_url: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT):
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar))
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._warmup_url = warmup_url
        self._warmed = False

    @property
    def cookies(self) -> list[str]:
        return sorted(cookie.name for cookie in self._jar)

    def warmup(self, url: str | None = None) -> bool:
        """GET the site page once so the WAF hands over its cookie. Never fatal."""
        target = url or self._warmup_url
        if not target or self._warmed:
            return False
        self._warmed = True
        try:
            req = urllib.request.Request(target, headers=_base_headers(self._headers))
            with self._opener.open(req, timeout=self._timeout) as resp:
                resp.read()
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            return False
        return True

    def _hdrs(self, headers: dict | None) -> dict:
        merged = dict(self._headers)
        merged.update(headers or {})
        return _base_headers(merged)

    def get_json(self, url: str, *, headers: dict | None = None,
                 retries: int = 2, backoff: float = 1.5) -> dict:
        self.warmup()
        hdrs = self._hdrs(headers)
        raw = _send(lambda: urllib.request.Request(url, headers=hdrs), url,
                    self._timeout, retries, backoff, opener=self._opener)
        return _as_json(raw, url)

    def post_json(self, url: str, payload: dict, *, headers: dict | None = None,
                  retries: int = 2, backoff: float = 1.5) -> dict:
        self.warmup()
        hdrs = self._hdrs(headers)
        hdrs.setdefault("Content-Type", "application/json;charset=UTF-8")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        raw = _send(lambda: urllib.request.Request(url, data=body, headers=hdrs), url,
                    self._timeout, retries, backoff, opener=self._opener)
        return _as_json(raw, url)


def _as_json(raw: bytes, url: str) -> dict:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        # A JS anti-bot challenge or HTML error page lands here (e.g. Stooq).
        raise FetchError(f"non-JSON response from {url}: {exc}", url=url) from exc


def _send(make_request, url: str, timeout: int, retries: int, backoff: float,
          opener=None) -> bytes:
    """Issue ``make_request()`` with the shared retry policy; return raw bytes."""
    last_exc: Exception | None = None
    # A session passes its cookie-keeping opener; plain calls keep using urlopen so the
    # shared retry path stays monkey-patchable in tests.
    sender = opener.open if opener is not None else urllib.request.urlopen
    for attempt in range(retries + 1):
        req = make_request()
        try:
            with sender(req, timeout=timeout) as resp:
                try:
                    return _read_body(resp)
                except http.client.IncompleteRead as exc:
                    try:
                        partial = _decode_body(exc.partial, resp.headers.get("Content-Encoding"))
                    except BODY_DECODE_ERRORS:
                        raise
                    else:
                        if _is_complete_json(partial, url, resp.headers.get("Content-Type")):
                            return partial
                    raise
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in RETRY_STATUSES and attempt < retries:
                _sleep(backoff * (attempt + 1))
                continue
            raise FetchError(f"HTTP {exc.code} for {url}", status=exc.code, url=url) from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < retries:
                _sleep(backoff * (attempt + 1))
                continue
            raise FetchError(f"network error for {url}: {exc.reason}", url=url) from exc
        except BODY_READ_ERRORS as exc:
            last_exc = exc
            if attempt < retries:
                _sleep(backoff * (attempt + 1))
                continue
            raise FetchError(f"incomplete or invalid response from {url}: {exc}", url=url) from exc

    raise FetchError(f"exhausted retries for {url}: {last_exc}", url=url)


def _read_body(resp) -> bytes:
    body = resp.read()
    return _decode_body(body, resp.headers.get("Content-Encoding"))


def _decode_body(body: bytes, content_encoding: str | None) -> bytes:
    encoding = (content_encoding or "").lower()
    if encoding == "gzip":
        import gzip

        body = gzip.decompress(body)
    elif encoding == "deflate":
        body = zlib.decompress(body)
    return body


def _is_complete_json(body: bytes, url: str, content_type: str | None) -> bool:
    if "json" not in (content_type or "").lower() and not url.split("?", 1)[0].endswith(".json"):
        return False
    try:
        json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return True


def _sleep(seconds: float) -> None:
    # Wrapped so tests can monkeypatch; kept tiny and bounded.
    time.sleep(min(seconds, 10.0))


BODY_DECODE_ERRORS = (OSError, EOFError, zlib.error)
BODY_READ_ERRORS = (http.client.HTTPException, *BODY_DECODE_ERRORS)
