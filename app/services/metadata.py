"""Unified anime metadata lookup.

AniList is tried first; Jikan (MyAnimeList) is used as a fallback. Both return
the same normalized dict shape, so callers do not care which provider answered.
"""
from __future__ import annotations

import logging
import re

from . import anilist, jikan
from .jikan import clean_title, extract_hints  # re-exported for callers

log = logging.getLogger("index.metadata")

__all__ = ["search_anime", "clean_title", "extract_hints", "search_candidates"]

_MAIN_TITLE = re.compile(r"^(.+?)\s*[:\-–—]\s+.+$")
_PUNCT = re.compile(r"[^\w\s]")


def search_candidates(cleaned: str) -> list[str]:
    """Progressively looser query strings to try against metadata providers."""
    candidates: list[str] = []

    def add(value: str) -> None:
        value = (value or "").strip()
        if len(value) >= 2 and value.lower() not in {c.lower() for c in candidates}:
            candidates.append(value)

    add(cleaned)
    main = _MAIN_TITLE.match(cleaned)
    if main:
        add(main.group(1))
    add(_PUNCT.sub(" ", cleaned))
    return candidates


async def search_anime(title: str) -> dict | None:
    cleaned = clean_title(title) or title.strip()
    if not cleaned:
        return None

    candidates = search_candidates(cleaned)
    for candidate in candidates:
        meta = await anilist.search_anime(candidate)
        if meta:
            return meta

    log.info("AniList miss for %r, falling back to Jikan", cleaned)
    for candidate in candidates:
        meta = await jikan.search_anime(candidate)
        if meta:
            return meta
    return None
