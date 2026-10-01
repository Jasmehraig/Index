"""Parse curated "name + link" entries out of index-channel posts.

Index channels (the kind that post ``Haikyuu`` next to a t.me link) are not
release feeds: one post can list many anime. This module turns a post body or a
Telegram message entity list into ``{"title", "url", "index"}`` entries, covering
the formats such channels actually use:

* Telegram ``text_link`` entities (the "create link" feature),
* HTML anchors as emitted by RSS bridges,
* Markdown ``[name](url)``,
* plain ``name - url`` / ``name | url`` / ``name url`` lines,
* a bare URL on the line after its name.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

_ANCHOR_RE = re.compile(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_MD_RE = re.compile(r"\[([^\]]+)\]\(\s*(https?://[^)\s]+)\s*\)")
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
_BULLET_RE = re.compile(r"^[\s\-–—•*·>#\d.)\]]+")
_SKIP_HOSTS = {"t.me", "telegram.me", "telegram.dog"}


def _clean_title(value: str) -> str:
    text = _TAG_RE.sub("", value)
    text = html.unescape(text)
    text = text.replace("\u200b", "").strip()
    text = _BULLET_RE.sub("", text).strip()
    return re.sub(r"\s+", " ", text).strip(" -–—:|•·")


def _trim_url(value: str) -> str:
    return html.unescape(value.strip()).rstrip(".,;)]}>\u200b")


def _looks_like_link(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _is_telegram_post(url: str) -> bool:
    return urlsplit(url).netloc.lower() in _SKIP_HOSTS


def _utf16_slice(text: str, offset: int, length: int) -> str:
    """Slice using Telegram's UTF-16 code-unit offsets."""
    encoded = text.encode("utf-16-le")
    chunk = encoded[offset * 2 : (offset + length) * 2]
    return chunk.decode("utf-16-le", errors="ignore")


def _from_entities(text: str, entities: list[dict]) -> list[dict]:
    entries: list[dict] = []
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        if entity.get("type") == "text_link" and entity.get("url"):
            title = _clean_title(_utf16_slice(text, entity.get("offset", 0), entity.get("length", 0)))
            url = _trim_url(entity["url"])
        elif entity.get("type") == "url":
            url = _trim_url(_utf16_slice(text, entity.get("offset", 0), entity.get("length", 0)))
            title = ""
        else:
            continue
        if _looks_like_link(url):
            entries.append({"title": title, "url": url})
    return entries


def _from_anchors(text: str) -> list[dict]:
    return [
        {"title": _clean_title(inner), "url": _trim_url(url)}
        for url, inner in _ANCHOR_RE.findall(text)
        if _looks_like_link(_trim_url(url))
    ]


def _from_markdown(text: str) -> list[dict]:
    return [
        {"title": _clean_title(name), "url": _trim_url(url)}
        for name, url in _MD_RE.findall(text)
    ]


def _from_plain_lines(text: str) -> list[dict]:
    """Name-then-link on one line, or a name line followed by a bare link."""
    entries: list[dict] = []
    pending: str | None = None
    for raw in _TAG_RE.sub("", html.unescape(text)).splitlines():
        line = raw.strip()
        if not line:
            continue
        urls = _URL_RE.findall(line)
        if not urls:
            pending = _clean_title(line) or None
            continue
        url = _trim_url(urls[0])
        remainder = _clean_title(_URL_RE.sub("", line))
        title = remainder or pending or ""
        pending = None
        if _looks_like_link(url):
            entries.append({"title": title, "url": url})
    return entries


def parse_entries(text: str | None, entities: list[dict] | None = None) -> list[dict]:
    """Return de-duplicated ``{"title", "url", "index"}`` entries for a post."""
    text = text or ""
    found = _from_entities(text, entities or [])
    if not found:
        # A post can mix HTML anchors and markdown links; collect both.
        found = _from_anchors(text) + _from_markdown(text)
    if not found:
        found = _from_plain_lines(text)

    seen: set[str] = set()
    entries: list[dict] = []
    for item in found:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        entries.append(
            {
                "title": item["title"],
                "url": url,
                "index": len(entries),
                "is_telegram": _is_telegram_post(url),
            }
        )
    return entries


def looks_like_index(text: str | None, entities: list[dict] | None = None) -> bool:
    """Heuristic: does this post look like a list of names and links?"""
    return len(parse_entries(text, entities)) >= 2
