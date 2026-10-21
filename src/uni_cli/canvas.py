"""A read-only Canvas REST client: GET requests only, to one Canvas host, never the inbox or quiz questions."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

USER_AGENT = "uni-cli/0.1 (read-only)"
LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}
# Never called: the Canvas inbox, and anything that opens a quiz's questions or a student's answers.
BLOCKED = re.compile(r"^/api/v1/(conversations\b|.*/quizzes/\d+/(questions|submissions|statistics|reports)|.*quiz_submissions)")
LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')
MAX_PAGES = 20


class CanvasError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ReadOnlyError(Exception):
    """A request this tool must never make. It is refused before anything is sent."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect would carry the token to wherever it points, so none is followed.
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def check_base_url(url: str) -> str:
    """The Canvas origin, e.g. https://canvas.instructure.com. Plain http only for a server on this machine."""
    parts = urlsplit(url.strip().rstrip("/"))
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError(f"{url!r} is not a Canvas address like https://canvas.instructure.com")
    if parts.scheme == "http" and parts.hostname not in LOOPBACK and f"[{parts.hostname}]" not in LOOPBACK:
        raise ValueError("the Canvas address must use https")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("the Canvas address must be just the site, with no path, query or login in it")
    return f"{parts.scheme}://{parts.netloc}"


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class Canvas:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30) -> None:
        self.base = check_base_url(base_url)
        self._token = token
        self.timeout = timeout
        self.requests = 0

    def _guard(self, method: str, url: str) -> None:
        parts = urlsplit(url)
        if method != "GET":
            raise ReadOnlyError(f"refused {method}: uni only reads")
        if f"{parts.scheme}://{parts.netloc}" != self.base:
            raise ReadOnlyError(f"refused a request to {parts.scheme}://{parts.netloc}: not {self.base}")
        if not parts.path.startswith("/api/v1/") or BLOCKED.search(parts.path):
            raise ReadOnlyError(f"refused {parts.path}: uni never reads the inbox or quiz questions")

    def _open(self, url: str) -> tuple[Any, Any]:
        self._guard("GET", url)
        for attempt in range(4):
            request = urllib.request.Request(url, method="GET", headers={
                "Authorization": f"Bearer {self._token}", "Accept": "application/json", "User-Agent": USER_AGENT})
            try:
                with _opener.open(request, timeout=self.timeout) as response:
                    self.requests += 1
                    body, headers = response.read(), response.headers
            except urllib.error.HTTPError as error:
                self.requests += 1
                with error:
                    text = error.read().decode("utf-8", "replace").lower()
                # Canvas answers 403 "Rate Limit Exceeded" when its request budget runs out: wait and try again.
                if error.code in (403, 429) and "rate limit" in text and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise CanvasError(f"Canvas answered HTTP {error.code} for {urlsplit(url).path}", error.code) from None
            except urllib.error.URLError as error:
                raise CanvasError(f"could not reach {self.base}: {error.reason}") from None
            remaining = headers.get("X-Rate-Limit-Remaining")
            if remaining and float(remaining) < 50:
                time.sleep(1)
            return json.loads(body or b"null"), headers
        raise CanvasError("Canvas kept answering that its rate limit was exceeded")

    def url(self, path: str, params: dict[str, Any] | None = None) -> str:
        query = urlencode(params or {}, doseq=True)
        return f"{self.base}{path}{'?' + query if query else ''}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._open(self.url(path, params))[0]

    def get_all(self, path: str, params: dict[str, Any] | None = None) -> list[Any]:
        """Every page of a list, following Canvas's Link headers (each next page is checked like the first)."""
        url: str | None = self.url(path, {"per_page": 100, **(params or {})})
        out: list[Any] = []
        for _ in range(MAX_PAGES):
            if url is None:
                break
            data, headers = self._open(url)
            out.extend(data if isinstance(data, list) else [])
            match = LINK_NEXT.search(headers.get("Link") or "")
            url = match.group(1) if match else None
        return out
