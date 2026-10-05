"""Parse the bot's episode message into seasons and episodes.

The owner sends one plain-text message per title:

    Anime name - Demon Slayer
    Season - Season 01
    Language - English, Japanese
    Episode 1 - https://t.me/FileBot?start=abc
    Episode 2 - https://t.me/FileBot?start=def

    Anime name - Demon Slayer
    Season - Season 02
    Language - Japanese
    Episode 1 - https://t.me/FileBot?start=xyz

A title with several seasons is sent as several blocks. Field labels are matched
loosely (``Anime``/``Name``/``Title``, ``Language``/``Audio``, ``Ep``/``Episode``)
and the ``-``/``:`` separator is optional, so small format drift is tolerated.
"""
from __future__ import annotations

import re

_SEP = r"\s*[-–—:]\s*"
_ANIME = re.compile(rf"^\s*(?:anime\s*(?:name|title)?|name|title){_SEP}(.+?)\s*$", re.I)
_SEASON = re.compile(rf"^\s*season{_SEP}(.+?)\s*$", re.I)
_LANGUAGE = re.compile(rf"^\s*(?:language|audio|lang){_SEP}(.+?)\s*$", re.I)
_SUBTITLE = re.compile(rf"^\s*(?:subtitle|subtitles|sub|subs){_SEP}(.+?)\s*$", re.I)
_QUALITY = re.compile(rf"^\s*(?:quality|qualities){_SEP}(.+?)\s*$", re.I)
_EPISODE = re.compile(rf"^\s*(?:episode|ep)\s*(\d+)\s*{_SEP}(https?://\S+)\s*$", re.I)
_ANY_EPISODE = re.compile(rf"^\s*(?:episode|ep){_SEP}", re.I)
_URL = re.compile(r"https?://[^\s<>\"')]+")
_SEASON_NUM = re.compile(r"(\d+)")

DEFAULT_QUALITIES = ["480p", "720p", "1080p", "HD-RIP"]


def _season_number(value: str) -> int:
    match = _SEASON_NUM.search(value or "")
    return int(match.group(1)) if match else 1


def _trim_url(value: str) -> str:
    return value.strip().rstrip(".,;)]}\u200b")


def _split_list(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,/&]| and ", value or "") if part.strip()]


def parse_episode_message(text: str | None) -> dict | None:
    """Return ``{"name", "seasons": [...]}`` or None when nothing parses.

    Every field except the episode links is optional: a message may carry only
    ``Anime name - X`` plus ``Episode 1 - url`` lines.
    """
    if not text or not text.strip():
        return None

    name: str | None = None
    seasons: list[dict] = []
    current: dict | None = None
    orphans: list[dict] = []
    # Fields seen before any "Season -" line become defaults for seasons that
    # do not carry their own. Fields after a season line belong to that season.
    defaults = {"audio": [], "subtitles": [], "quality_tags": []}

    def new_season(number: int) -> dict:
        return {
            "number": number,
            "audio": [],
            "subtitles": [],
            "quality_tags": [],
            "episodes": [],
        }

    def bucket(key: str) -> list[str]:
        return current[key] if current is not None else defaults[key]

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if (match := _ANIME.match(line)) and not _ANY_EPISODE.match(line):
            name = match.group(1).strip()
            continue
        if match := _SEASON.match(line):
            current = new_season(_season_number(match.group(1)))
            seasons.append(current)
            continue
        if match := _LANGUAGE.match(line):
            bucket("audio").extend(_split_list(match.group(1)))
            continue
        if match := _SUBTITLE.match(line):
            bucket("subtitles").extend(_split_list(match.group(1)))
            continue
        if match := _QUALITY.match(line):
            bucket("quality_tags").extend(_split_list(match.group(1)))
            continue
        if match := _EPISODE.match(line):
            episode = {"number": int(match.group(1)), "url": _trim_url(match.group(2))}
            if current is not None:
                current["episodes"].append(episode)
            else:
                orphans.append(episode)
            continue

        # A bare URL line continues the current season as the next episode.
        if (urls := _URL.findall(line)) and current is not None:
            number = len(current["episodes"]) + 1
            current["episodes"].append({"number": number, "url": _trim_url(urls[0])})

    if name is None and not seasons and not orphans:
        return None
    if name is None:
        # Episodes without an explicit name: use the first non-field line.
        for raw in text.splitlines():
            candidate = raw.strip()
            if candidate and not _ANY_EPISODE.match(candidate) and not _URL.search(candidate):
                name = candidate.lstrip("-–—•* ").strip()
                break
    if not seasons:
        seasons = [new_season(1)]
    if orphans:
        seasons[0]["episodes"] = orphans + seasons[0]["episodes"]

    for season in seasons:
        for key in ("audio", "subtitles", "quality_tags"):
            values = season[key] or defaults[key]
            # De-duplicate while keeping the order the owner wrote them in.
            seen: list[str] = []
            for value in values:
                if value not in seen:
                    seen.append(value)
            season[key] = ", ".join(seen) or None
        season["episodes"].sort(key=lambda e: e["number"])

    return {"name": (name or "").strip(), "seasons": seasons}


def looks_like_episode_message(text: str | None) -> bool:
    """Heuristic: is this a plain-text message meant to add episodes?"""
    parsed = parse_episode_message(text)
    if not parsed or not parsed["name"]:
        return False
    return any(season["episodes"] for season in parsed["seasons"])
