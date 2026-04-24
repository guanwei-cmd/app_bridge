"""Shared scraper utilities — rate limit, retry, UA, HTML fetch."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 bridge-analyst/0.1"
)


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    headers: dict = field(default_factory=dict)


def _delay_seconds() -> float:
    try:
        return float(os.environ.get("SCRAPER_DELAY", "1.5"))
    except ValueError:
        return 1.5


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def fetch(url: str, *, cookies: Optional[dict] = None, timeout: float = 15.0) -> FetchResult:
    """Polite GET with retry + rate limit. Raises on non-2xx."""
    time.sleep(_delay_seconds())
    with httpx.Client(
        headers={"User-Agent": DEFAULT_UA},
        cookies=cookies or {},
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return FetchResult(
            url=str(resp.url),
            status=resp.status_code,
            text=resp.text,
            headers=dict(resp.headers),
        )
