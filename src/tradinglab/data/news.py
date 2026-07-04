"""Best-effort current news headlines via yfinance.

LOOKAHEAD WARNING: Yahoo only serves *current* headlines, so this module is
meaningful only for live-mode decisions (analysis date ~ today). Injecting
current news into a historical backtest leaks future information.
"""
from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf


def _item_date(item: dict, content: dict) -> str:
    """Extract a YYYY-MM-DD date string from either yfinance news schema."""
    for key in ("pubDate", "displayTime"):
        value = content.get(key) or item.get(key)
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
    ts = item.get("providerPublishTime") or content.get("providerPublishTime")
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    return ""


def _item_publisher(item: dict, content: dict) -> str:
    """Extract the publisher name from either yfinance news schema."""
    publisher = item.get("publisher") or content.get("publisher")
    if isinstance(publisher, str) and publisher:
        return publisher
    provider = content.get("provider") or item.get("provider")
    if isinstance(provider, dict):
        name = provider.get("displayName") or provider.get("name")
        if isinstance(name, str):
            return name
    return ""


def fetch_current_news(ticker: str, limit: int = 8) -> list[dict]:
    """Fetch up to ``limit`` current headlines for ``ticker``.

    Returns ``[{"title", "publisher", "date"}]`` with ``date`` as "YYYY-MM-DD"
    (empty string when unknown). Tolerates both the flat and the
    "content"-nested yfinance news schemas; returns ``[]`` on any failure.

    Live-mode only: current headlines leak future information into historical
    backtests (see module warning).
    """
    try:
        items = yf.Ticker(ticker).news or []
    except Exception:
        return []
    if not isinstance(items, list):
        return []

    headlines: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, dict):
            content = {}
        title = content.get("title") or item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        headlines.append(
            {
                "title": title.strip(),
                "publisher": _item_publisher(item, content),
                "date": _item_date(item, content),
            }
        )
        if len(headlines) >= limit:
            break
    return headlines
