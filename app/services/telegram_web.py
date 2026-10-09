"""Read a public Telegram channel's web preview (``t.me/s/<channel>``).

Curated index channels publish two kinds of post:

* **series lists** -- numbered entries where the name is the link text, e.g.
  ``├➢[01] 91 Days`` pointing at ``https://t.me/Anime_91_Days_720p``. These
  carry the channel a user should be sent to.
* **detail cards** -- ``⧉ Handa-kun + Barakamon`` followed by ``Season:``,
  ``Episodes:``, ``Audio:``, ``Genres:`` and ``Synopsis:`` lines. These carry
  metadata, and occasionally an AniList link.

Both are readable without a bot token, so the catalog can be seeded from the
index channel itself rather than only from the bot's own posts.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re
from urllib.parse import urlparse

import httpx

log = logging.getLogger("index.telegram_web")

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
_ANCHOR = re.compile(r"<a\s+[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_ORDINAL = re.compile(r"\[\s*(\d+)\s*\]")
_NUMERIC_TAIL = re.compile(r"/(\d+)(?:[/?#]|$)")
_SKIP_TEXT = re.compile(r"^(click here|video tutorial|join here|comments|update|note)\b", re.I)
_BARE = re.compile(r"^\s*([A-Za-z0-9][^|\n]{1,120}?)\s*\|\s*(https?://\S+)\s*$")

_UI_TEXT = {"view in telegram", "preview channel", "join", "share", "comments", "update", "note"}
_PLACEHOLDER = re.compile(r"^\[?\s*(channel\s*link|click here|link)\s*\]?$", re.I)
_DECOR = re.compile(r"^[\s┏┣┗━╭╰│├➢⧉➥‣•·*\-–—>]+")

_FIELD = {
    "season": re.compile(r"S[eᴇ][aᴀ]?[sꜱ][oᴏ][nɴ]\s*[:\-]\s*(.+)", re.I),
    "episodes": re.compile(r"E[pᴘ][iɪ][sꜱ][oᴏ][dᴅ][eᴇ][sꜱ]\s*[:\-]\s*(.+)", re.I),
    "audio": re.compile(r"A[uᴜ][dᴅ][iɪ][oᴏ]\s*[:\-]\s*(.+)", re.I),
    "genres": re.compile(r"G[eᴇ][nɴ][rʀ][eᴇ][sꜱ]\s*[:\-]\s*(.+)", re.I),
    "synopsis": re.compile(r"S[yʏ][nɴ][oᴏ][pᴘ][sꜱ][iɪ][sꜱ]\s*[:\-]\s*(.+)", re.I | re.S),
}


def _clean(value: str) -> str:
    text = _html.unescape(_TAG.sub("", value))
    text = text.replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _plain(html_block: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html_block)
    text = _html.unescape(_TAG.sub("", text)).replace("\u200b", "").replace("\xa0", " ")
    return "\n".join(line.rstrip() for line in text.split("\n"))


def channel_kind(url: str) -> str:
    """Classify where an entry link sends the user."""
    parsed = urlparse(url)
    if parsed.netloc not in {"t.me", "telegram.me", "telegram.dog"}:
        return "external"
    path = parsed.path.strip("/")
    low = path.lower()
    if path.startswith("+"):
        return "private_invite"
    if low.endswith("bot"):
        return "file_bot" if parsed.query else "bot"
    if _NUMERIC_TAIL.search(url):
        return "channel_post"
    return "channel"


def _looks_like_entry(name: str, url: str, source: str) -> bool:
    if not name or not url.startswith(("https://t.me/", "http://t.me/", "https://telegram.me/")):
        return False
    low = name.lower()
    if low in _UI_TEXT or _SKIP_TEXT.match(name) or _PLACEHOLDER.match(name):
        return False
    if "subscriber" in low or name.startswith("@"):
        return False
    path = urlparse(url).path.strip("/").lower()
    ref = source.lower()
    if path == ref or path.startswith(ref + "/"):
        return False
    return True


def parse_detail(text: str) -> dict | None:
    """Parse a detail card into name + season/episodes/audio/genres/synopsis."""
    fields: dict[str, str] = {}
    for key, pattern in _FIELD.items():
        match = pattern.search(text)
        if match:
            fields[key] = re.sub(r"\s+", " ", match.group(1)).strip()
    if not fields:
        return None

    first = next((ln for ln in text.split("\n") if ln.strip()), "")
    name = _DECOR.sub("", first).strip().split("|")[0].strip()
    if not name or _PLACEHOLDER.match(name):
        return None
    fields["name"] = name
    return fields


def parse_message(html_block: str, source: str) -> tuple[list[dict], dict | None]:
    """Return (list entries, detail card) found in one message block."""
    body = re.search(
        r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', html_block, re.S
    )
    if not body:
        return [], None
    raw = body.group(1)
    text = _plain(raw)
    source = source.lstrip("@")

    entries: list[dict] = []
    # A numbered series list: each anchor whose text is a real name is an entry.
    if _ORDINAL.search(text):
        for url, inner in _ANCHOR.findall(raw):
            name = _ORDINAL.sub("", _clean(inner)).strip()
            if _looks_like_entry(name, url, source):
                entries.append({"name": name, "url": url, "kind": channel_kind(url)})
        if entries:
            return entries, None

    for name, url in _BARE.findall(text):
        name = _ORDINAL.sub("", _clean(name)).strip()
        if name and _looks_like_entry(name, url, source):
            entries.append({"name": name, "url": url, "kind": channel_kind(url)})
    if entries:
        return entries, None

    return [], parse_detail(text)


def parse_channel(html: str, source: str) -> tuple[list[dict], list[dict]]:
    """Parse a channel page into (entries, details)."""
    entries: list[dict] = []
    details: list[dict] = []
    seen_entry: set[tuple[str, str]] = set()
    seen_detail: set[str] = set()

    for block in re.split(r'data-post="', html)[1:]:
        got_entries, detail = parse_message(block, source)
        for item in got_entries:
            key = (item["name"].lower(), item["url"])
            if key not in seen_entry:
                seen_entry.add(key)
                entries.append(item)
        if detail and detail["name"].lower() not in seen_detail:
            seen_detail.add(detail["name"].lower())
            details.append(detail)

    for index, item in enumerate(entries):
        item["index"] = index
    return entries, details


async def fetch_channel(username: str, max_pages: int = 40) -> tuple[list[dict], list[dict]]:
    """Page through ``t.me/s/<username>`` and return (entries, details)."""
    username = username.lstrip("@")
    entries: list[dict] = []
    details: list[dict] = []
    seen_entry: set[tuple[str, str]] = set()
    seen_detail: set[str] = set()
    before: int | None = None
    headers = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
        for _ in range(max_pages):
            url = f"https://t.me/s/{username}"
            if before is not None:
                url += f"?before={before}"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("Channel preview fetch failed for %s: %s", username, exc)
                break

            page_entries, page_details = parse_channel(resp.text, username)
            for item in page_entries:
                key = (item["name"].lower(), item["url"])
                if key not in seen_entry:
                    seen_entry.add(key)
                    entries.append(item)
            for detail in page_details:
                if detail["name"].lower() not in seen_detail:
                    seen_detail.add(detail["name"].lower())
                    details.append(detail)

            ids = [
                int(m)
                for m in re.findall(rf'data-post="{re.escape(username)}/(\d+)"', resp.text)
            ]
            if not ids:
                break
            oldest = min(ids)
            if before == oldest:
                break
            before = oldest
            # Yield to the event loop between pages: a synchronous sleep here
            # would stall every other request on the same worker while paging.
            await asyncio.sleep(0.3)

    for index, item in enumerate(entries):
        item["index"] = index
    log.info(
        "Read %d entr(y/ies) and %d detail card(s) from @%s",
        len(entries),
        len(details),
        username,
    )
    return entries, details
