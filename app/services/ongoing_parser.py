"""Parse the owner's "ongoing anime" message.

The owner writes a header line, then one ``channel/title - link`` pair per line:

    ongoing anime
    Overgeared - https://t.me/overgeared_dual
    The Apothecary diaries - https://t.me/+r7zltHPqpOswY2Jl

Telegram "text_link" entities (a name that is a hyperlink) are understood too.
"""
from __future__ import annotations

import re

from . import index_parser

# "ongoing", "ongoing anime", "Ongoing Anime:", "ongoing anime." ... on its own line.
_HEADER = re.compile(r"^\s*(?:\W*)ongoing(?:\s+anime[s]?)?(?:\s+list)?\s*[:.\-–—!]*\s*$", re.I)


def split_header(text: str | None) -> tuple[bool, str]:
    """Return ``(has_header, body)`` where body is the text after the header line."""
    lines = (text or "").strip().splitlines()
    if lines and _HEADER.match(lines[0]):
        return True, "\n".join(lines[1:])
    return False, text or ""


def looks_like_ongoing_message(text: str | None) -> bool:
    """A plain message that starts with an "ongoing anime" header and has links."""
    has_header, body = split_header(text)
    return has_header and bool(parse_ongoing(body))


def parse_ongoing(body: str | None, entities: list[dict] | None = None) -> list[dict]:
    """Return ``[{"title", "url"}]``; entries without a title are dropped.

    Only ``text_link`` entities (a hyperlinked name) are used. Telegram also tags
    every typed URL as a ``url`` entity; passing those through would hide the
    plain ``Name - link`` lines, so they are ignored. Hyperlinked names and typed
    lines can be mixed in one message.
    """
    text = body or ""
    hyperlinks = [
        e for e in (entities or []) if isinstance(e, dict) and e.get("type") == "text_link"
    ]
    candidates: list[dict] = []
    if hyperlinks:
        candidates += index_parser.parse_entries(text, hyperlinks)
    candidates += index_parser.parse_entries(text, [])

    items: list[dict] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for entry in candidates:
        title = (entry.get("title") or "").strip()
        url = (entry.get("url") or "").strip()
        if not title or not url:
            continue
        key = re.sub(r"[^a-z0-9]", "", title.lower())
        if url in seen_urls or key in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(key)
        items.append({"title": title, "url": url})
    return items
