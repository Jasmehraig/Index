"""Anime metadata lookup via the Jikan (MyAnimeList) public API.

Jikan is free and needs no API key. It rate-limits to ~3 req/s, so requests are
serialised through a small async lock plus a minimum interval.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from ..config import get_settings

log = logging.getLogger("index.jikan")

_MIN_INTERVAL = 0.4
_lock = asyncio.Lock()
_last_call = 0.0

# Brackets used by release groups and CJK fansub tags.
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|【[^】]*】|「[^」]*」|〈[^〉]*〉|《[^》]*》")
_FILE_EXT = re.compile(r"\.(?:mkv|mp4|avi|mov|webm|flv|wmv|m4v|ts)\b", re.IGNORECASE)
_EPISODE = re.compile(r"\b(?:ep|episode|e)\s*[-:]?\s*(\d{1,4})(?:\s*[-~]\s*\d{1,4})?\b", re.IGNORECASE)
_SEASON = re.compile(r"\b(?:season|s)\s*(\d{1,2})\b", re.IGNORECASE)
_SXXEXX = re.compile(r"\bS(\d{1,2})E(\d{1,4})\b", re.IGNORECASE)
# Bare "Title - 05" / "Title_05" release numbering.
_TRAILING_EP = re.compile(r"[-–_]\s*(\d{1,4})\s*(?:\[|\()?\s*$")
_QUALITY = re.compile(
    r"\b(?:1080p|720p|480p|360p|2160p|4k|hevc|x264|x265|h\.?264|h\.?265|aac|flac|10bit|"
    r"sub|subs|subbed|dub|dubbed|dual|multi|batch|complete|raw|jpn|eng|english|"
    r"bluray|bd|web-?dl|webrip|hdrip|repack|v\d+)\b",
    re.IGNORECASE,
)
_TRAILING_NUM = re.compile(r"(?:[\-–_|]\s*|\(\s*)\d{1,4}\s*\)?\s*$")


def clean_title(raw: str) -> str:
    """Turn a noisy channel post title into a searchable anime title."""
    text = raw or ""
    text = _BRACKETS.sub(" ", text)
    text = _FILE_EXT.sub(" ", text)
    text = _SXXEXX.sub(" ", text)
    text = _EPISODE.sub(" ", text)
    text = _SEASON.sub(" ", text)
    text = _QUALITY.sub(" ", text)
    text = _TRAILING_NUM.sub("", text)
    text = re.sub(r"[_\-.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|:,.")
    return text


def extract_hints(raw: str) -> tuple[str | None, str | None]:
    """Return (episode_hint, season_hint) found in a raw title/caption."""
    text = raw or ""
    sxxexx = _SXXEXX.search(text)
    if sxxexx:
        return (f"Episode {sxxexx.group(2)}", f"Season {sxxexx.group(1)}")

    ep = _EPISODE.search(text)
    season = _SEASON.search(text)
    if ep:
        return (ep.group(0).strip(), season.group(0).strip() if season else None)

    # "Title - 05" style numbering: strip bracketed/quality noise first.
    trimmed = _FILE_EXT.sub(" ", _BRACKETS.sub(" ", text))
    trailing = _TRAILING_EP.search(trimmed)
    if trailing:
        return (f"Episode {trailing.group(1)}", season.group(0).strip() if season else None)

    return (None, season.group(0).strip() if season else None)


def _pick_image(images: dict, key: str) -> str | None:
    node = images.get(key) or {}
    return node.get("large_image_url") or node.get("image_url")


def normalize(item: dict) -> dict:
    genres = [g.get("name") for g in (item.get("genres") or []) if g.get("name")]
    for theme in item.get("themes") or []:
        if theme.get("name"):
            genres.append(theme["name"])
    studios = [s.get("name") for s in (item.get("studios") or []) if s.get("name")]
    year = None
    aired = (item.get("aired") or {}).get("prop", {}).get("from", {})
    if aired.get("year"):
        year = aired["year"]
    elif item.get("year"):
        year = item["year"]
    return {
        "source": "jikan",
        "external_id": item.get("mal_id"),
        "mal_id": item.get("mal_id"),
        "title": item.get("title") or "",
        "title_english": item.get("title_english"),
        "title_japanese": item.get("title_japanese"),
        "synopsis": item.get("synopsis"),
        "poster_url": _pick_image(item.get("images") or {}, "jpg") or _pick_image(item.get("images") or {}, "webp"),
        "banner_url": None,
        "genres": ", ".join(dict.fromkeys(genres)) or None,
        "studio": ", ".join(dict.fromkeys(studios)) or None,
        "episodes": item.get("episodes"),
        "status": item.get("status"),
        "score": item.get("score"),
        "year": year,
    }


async def search_anime(title: str, limit: int = 1, _attempts: int = 2) -> dict | None:
    """Search Jikan for the best matching anime. Returns a normalized dict or None."""
    query = clean_title(title)
    if not query:
        return None

    global _last_call
    settings = get_settings()
    async with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(
                timeout=8.0,
                headers={"Accept": "application/json", "Connection": "close"},
            ) as client:
                resp = await client.get(
                    f"{settings.jikan_base_url}/anime",
                    params={"q": query, "limit": max(1, min(limit, 5)), "sfw": "true"},
                )
            _last_call = time.monotonic()
            # Jikan intermittently returns 429/5xx; back off and retry.
            if resp.status_code == 429 or resp.status_code >= 500:
                if _attempts > 1:
                    backoff = 1.5 if resp.status_code == 429 else 1.0
                    log.info("Jikan %s for %r, retrying (%d left)", resp.status_code, query, _attempts - 1)
                    await asyncio.sleep(backoff)
                    return await search_anime(title, limit, _attempts - 1)
                log.warning("Jikan unavailable (%s) for %r", resp.status_code, query)
                return None
            if resp.status_code >= 400:
                log.warning("Jikan search failed (%s) for %r", resp.status_code, query)
                return None
            data = resp.json().get("data") or []
        except httpx.HTTPError as exc:
            if _attempts > 1:
                await asyncio.sleep(1.0)
                return await search_anime(title, limit, _attempts - 1)
            log.warning("Jikan request error: %s", exc)
            return None

    if not data:
        return None
    return normalize(data[0])
