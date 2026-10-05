"""Anime metadata lookup via the AniList GraphQL API (primary provider).

AniList needs no API key and is far more reliable than Jikan. Results are
normalized to the same shape as the Jikan provider so the ingestion layer can
treat both interchangeably.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ..config import get_settings

log = logging.getLogger("index.anilist")

_MIN_INTERVAL = 0.7
_lock = asyncio.Lock()
_last_call = 0.0

_QUERY = """
query ($search: String) {
  Page(page: 1, perPage: 3) {
    media(search: $search, type: ANIME, sort: POPULARITY_DESC, isAdult: false) {
      id
      idMal
      title { romaji english native }
      description(asHtml: false)
      coverImage { extraLarge large }
      bannerImage
      genres
      episodes
      status
      averageScore
      startDate { year }
      studios(isMain: true) { nodes { name } }
    }
  }
}
"""

_STATUS = {
    "FINISHED": "Finished",
    "RELEASING": "Airing",
    "NOT_YET_RELEASED": "Upcoming",
    "CANCELLED": "Cancelled",
    "HIATUS": "Hiatus",
}


def normalize(media: dict) -> dict:
    title = media.get("title") or {}
    description = media.get("description") or ""
    cover = media.get("coverImage") or {}
    score = media.get("averageScore")
    studios = ((media.get("studios") or {}).get("nodes")) or []
    return {
        "source": "anilist",
        "external_id": media.get("id"),
        "mal_id": media.get("idMal"),
        "title": title.get("romaji") or title.get("english") or title.get("native") or "",
        "title_english": title.get("english"),
        "title_japanese": title.get("native"),
        "synopsis": description or None,
        "poster_url": cover.get("extraLarge") or cover.get("large"),
        "banner_url": media.get("bannerImage"),
        "genres": ", ".join(media.get("genres") or []) or None,
        "studio": ", ".join(s["name"] for s in studios if s.get("name")) or None,
        "episodes": media.get("episodes"),
        "status": _STATUS.get(media.get("status"), media.get("status")),
        "score": round(score / 10, 1) if score else None,
        "year": (media.get("startDate") or {}).get("year"),
    }


async def search_anime(title: str, _attempts: int = 3) -> dict | None:
    if not title or not title.strip():
        return None

    global _last_call
    settings = get_settings()
    async with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    settings.anilist_url,
                    json={"query": _QUERY, "variables": {"search": title.strip()}},
                )
            _last_call = time.monotonic()
            if resp.status_code == 429 or resp.status_code >= 500:
                if _attempts > 1:
                    log.info("AniList %s for %r, retrying", resp.status_code, title)
                    await asyncio.sleep(2.0)
                    return await search_anime(title, _attempts - 1)
                return None
            if resp.status_code >= 400:
                log.warning("AniList search failed (%s) for %r", resp.status_code, title)
                return None
            payload = resp.json()
            media_list = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
            media = media_list[0] if media_list else None
        except httpx.HTTPError as exc:
            if _attempts > 1:
                await asyncio.sleep(1.5)
                return await search_anime(title, _attempts - 1)
            log.warning("AniList request error: %s", exc)
            return None

    return normalize(media) if media else None
