"""Parse release-style channel posts into seasons and episodes.

A file/release channel posts one block per episode (or several blocks sharing a
single "START" deep link), for example::

    ➥ Episode : 13
    ➥ Season : 01
    ➥ Audio : Japanese [Eng Sub]
    ┃█████████████████
    ┃480p 720p 1080p  HD-RIP
    ┃█████████████████
    Click Press START

Channels dress this up with decorative fonts. ``unicodedata.normalize("NFKC")``
folds the mathematical-bold / fullwidth letters back to ASCII first, so
``𝟰𝟴𝟬𝗽`` becomes ``480p`` before any pattern runs.

The deep link (``https://t.me/<bot>?start=...``) is often attached only once at
the bottom of the post. When a block carries no link of its own it inherits the
post's link, so two episode blocks can share one URL.
"""
from __future__ import annotations

import re
import unicodedata

_SEASON = re.compile(r"season\s*[:\-|]?\s*(\d+)", re.I)
_EPISODE = re.compile(r"episode\s*[:\-|]?\s*(\d+)", re.I)
_AUDIO = re.compile(r"audio\s*[:\-]\s*(.+)", re.I)
_TME = re.compile(r"https?://t\.me/[^\s<>\"')]+", re.I)
_START = re.compile(r"[?&]start=", re.I)
_QUALITY = re.compile(r"\b(2160p|1080p|720p|480p|360p|4k|hd-?rip|blu-?ray|web-?dl)\b", re.I)
# A line of box-drawing / dashes that visually separates episode blocks.
_RULE = re.compile(r"^[\s─═━—–\-_▬■□]{3,}$")
# A box-drawing banner line, e.g. "┃█████████████████" — never an anime name.
_BOX_ONLY = re.compile(r"^[\s┃┏┣┗━│╭╰█░▒▓]+$")
_LEADING_DECOR = re.compile(r"^[\s┏┣┗━╭╰│├➢➤➥⧉‣•·*💠✦✧\-\u200b]+")

DEFAULT_QUALITIES = ["480p", "720p", "1080p", "HD-RIP"]
DEFAULT_SUBTITLES = "English"

_DUAL = re.compile(r"\bdual\s*audio\b|\bjap\s*\+\s*eng\b|\beng\s*\+\s*jap\b", re.I)
_ENG = re.compile(r"\beng(?:lish)?\b|\bjap(?:anese)?\b", re.I)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _audio_and_subtitles(audio_text: str) -> tuple[str, str]:
    """Split an ``Audio`` line into the audio track and the subtitle language.

    ``Japanese [Eng Sub]`` -> ("Japanese", "English");
    ``Jap + Eng [Dual audio]`` -> ("Japanese, English", "English");
    ``English`` / ``Japanese`` alone -> ("...", "English").
    """
    value = (audio_text or "").strip()
    if not value:
        return "", DEFAULT_SUBTITLES

    # Only the part before any bracket describes the audio track; the bracketed
    # tail is the subtitle marker ("[Eng Sub]", "[Dual audio]").
    audio_part = re.sub(r"[\[(].*", "", value)
    langs: list[str] = []
    for word in re.findall(r"[A-Za-z]+", audio_part):
        low = word.lower()
        if low.startswith("jap"):
            langs.append("Japanese")
        elif low.startswith("eng"):
            langs.append("English")
    langs = _dedupe(langs)

    if _DUAL.search(value) or {"Japanese", "English"} <= set(langs):
        audio = "Japanese, English"
    else:
        audio = langs[0] if langs else audio_part.strip()

    return audio, DEFAULT_SUBTITLES


def _qualities(text: str) -> list[str]:
    found = _dedupe([m.group(1) for m in _QUALITY.finditer(text)])
    # Normalise the odd spelling out of "HD-RIP"/"HDRIP" and keep the common
    # order the rest of the app uses.
    mapped = []
    for q in found:
        low = q.lower().replace(" ", "")
        if low in ("hdrip", "hd-rip"):
            mapped.append("HD-RIP")
        elif low == "4k":
            mapped.append("2160p")
        else:
            mapped.append(low)
    order = {q.lower(): i for i, q in enumerate(DEFAULT_QUALITIES)}
    mapped.sort(key=lambda q: order.get(q.lower(), 99))
    return mapped or list(DEFAULT_QUALITIES)


def _blocks(text: str) -> list[str]:
    """Split a post into per-episode blocks at the separator rules."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if _RULE.match(line):
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _block_episode(block: str) -> dict | None:
    ep = _EPISODE.search(block)
    if not ep:
        return None
    season = _SEASON.search(block)
    audio = _AUDIO.search(block)
    audio_value, subtitles = _audio_and_subtitles(audio.group(1) if audio else "")
    urls = [u for u in _TME.findall(block) if _START.search(u)]
    return {
        "number": int(ep.group(1)),
        "season": int(season.group(1)) if season else 1,
        "audio": audio_value,
        "subtitles": subtitles,
        "url": urls[0] if urls else None,
    }


def _last_start_link(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        for url in _TME.findall(line):
            if _START.search(url):
                return url
    return None


def _leading_name(text: str) -> str | None:
    """The anime name some channels put on the first line, above the first rule."""
    for line in text.splitlines():
        if _RULE.match(line):
            break
        clean = _LEADING_DECOR.sub("", _norm(line)).strip()
        if not clean or _BOX_ONLY.match(clean):
            continue
        low = clean.lower()
        if low.startswith(("click", "powered", "join", "subscribe", "start")):
            continue
        # Skip the post's own field lines; they are not the anime name.
        if (_EPISODE.search(clean) or _SEASON.search(clean) or _AUDIO.search(clean)
                or _QUALITY.search(clean) or _TME.search(clean)):
            continue
        return clean
    return None


def parse_release_post(text: str | None, fallback_urls: list[str] | None = None) -> dict | None:
    """Return ``{"name", "seasons": [...]}`` for a release channel post.

    The shape matches ``episode_parser.parse_episode_message`` so the result can
    go straight into ``catalog.upsert_episodes``. Returns None when the post is
    not a release post (no episode marker).

    ``fallback_urls`` are the post's anchors. Telegram shows some deep links as a
    link-preview card whose URL is not in the message text, so a block with no
    inline link falls back to those anchors.
    """
    if not text or not text.strip():
        return None
    normalized = _norm(text)

    episodes: list[dict] = []
    for block in _blocks(normalized):
        found = _block_episode(block)
        if found:
            episodes.append(found)
    if not episodes:
        # No separator rules: treat the whole post as one block.
        found = _block_episode(normalized)
        if found:
            episodes.append(found)
    if not episodes:
        return None

    # A link anywhere in the post is shared by blocks that carry none of their own.
    shared = _last_start_link(normalized)
    if shared is None and fallback_urls:
        shared = next((u for u in fallback_urls if _START.search(u)), None)
    qualities = _qualities(normalized)

    by_season: dict[int, dict] = {}
    seen: set[tuple[int, int]] = set()
    for ep in episodes:
        url = ep["url"] or shared
        if not url:
            continue
        if (ep["season"], ep["number"]) in seen:
            continue
        seen.add((ep["season"], ep["number"]))
        season = by_season.setdefault(
            ep["season"],
            {
                "number": ep["season"],
                "audio": ep["audio"],
                "subtitles": ep["subtitles"],
                "quality_tags": ", ".join(qualities),
                "episodes": [],
            },
        )
        if ep["audio"] and not season["audio"]:
            season["audio"] = ep["audio"]
        season["episodes"].append(
            {"number": ep["number"], "url": url, "title": f"Episode {ep['number']:02d}"}
        )

    for season in by_season.values():
        season["episodes"].sort(key=lambda e: e["number"])

    seasons = [by_season[n] for n in sorted(by_season)]
    if not seasons:
        return None
    return {"name": _leading_name(normalized), "seasons": seasons}


def looks_like_release_post(text: str | None, fallback_urls: list[str] | None = None) -> bool:
    """Cheap check: an episode marker plus a START deep link."""
    if not text:
        return False
    normalized = _norm(text)
    if not _EPISODE.search(normalized):
        return False
    if any(_START.search(u) for u in _TME.findall(normalized)):
        return True
    return bool(fallback_urls and any(_START.search(u) for u in fallback_urls))


def mentions_name(text: str | None, name: str) -> bool:
    """Does the post's text carry the anime's name (used to post-filter matches)?

    Compared loosely: alphanumerics only and lowercase, so punctuation, case and
    decorative fonts do not matter.
    """
    if not text or not name:
        return False
    haystack = re.sub(r"[^a-z0-9]+", "", _norm(text).lower())
    needle = re.sub(r"[^a-z0-9]+", "", name.lower())
    if len(needle) < 4:
        return False
    return needle in haystack
