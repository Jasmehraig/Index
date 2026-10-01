"""Fetch and parse a channel's RSS/Atom feed into normalized entries."""
from __future__ import annotations

import logging
import re
from calendar import timegm
from datetime import datetime, timezone

import feedparser
import httpx

log = logging.getLogger("index.rss")

_MESSAGE_ID = re.compile(r"/(\d+)(?:[/?#]|$)")
_UA = "IndexMiniApp/1.0 (+https://t.me)"


def message_id_from_link(link: str | None) -> int | None:
    if not link:
        return None
    match = _MESSAGE_ID.search(link)
    return int(match.group(1)) if match else None


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc)
            except (ValueError, OverflowError):
                continue
    return None


async def fetch_feed(rss_url: str) -> list[dict]:
    """Download and parse a feed. Returns a list of normalized entry dicts."""
    if not rss_url:
        return []
    try:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            resp = await client.get(rss_url)
            resp.raise_for_status()
            raw = resp.content
    except httpx.HTTPError as exc:
        log.warning("RSS fetch failed for %s: %s", rss_url, exc)
        return []

    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        log.warning("RSS parse failed for %s: %s", rss_url, parsed.get("bozo_exception"))
        return []

    entries = []
    for entry in parsed.entries:
        link = entry.get("link")
        content = ""
        if entry.get("content"):
            content = entry["content"][0].get("value", "")
        summary = (entry.get("summary") or entry.get("description") or content or "").strip()
        entries.append(
            {
                "title": (entry.get("title") or "").strip(),
                "summary": summary,
                "entities": entry.get("entities") or [],
                "link": link,
                "message_id": message_id_from_link(link),
                "published_at": _published(entry),
            }
        )
    return entries
