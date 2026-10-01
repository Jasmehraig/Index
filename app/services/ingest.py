"""RSS -> anime metadata -> database ingestion pipeline."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Anime, Channel, Post
from . import index_parser, metadata, rss

log = logging.getLogger("index.ingest")


def _lookup_existing(db: Session, cleaned: str, meta: dict | None) -> Anime | None:
    """Find an already-indexed anime by exact title or provider id."""
    existing = db.scalar(select(Anime).where(Anime.title == cleaned))
    if existing:
        return existing
    if meta:
        if meta.get("mal_id"):
            found = db.scalar(select(Anime).where(Anime.mal_id == meta["mal_id"]))
            if found:
                return found
        if meta.get("external_id"):
            found = db.scalar(
                select(Anime).where(
                    Anime.source == meta.get("source"),
                    Anime.external_id == meta["external_id"],
                )
            )
            if found:
                return found
    return None


async def match_or_create_anime(db: Session, title: str) -> Anime | None:
    """Match a raw channel title to an anime row, creating one if needed."""
    cleaned = metadata.clean_title(title) or title.strip()
    if not cleaned:
        return None

    meta = await metadata.search_anime(cleaned)
    existing = _lookup_existing(db, cleaned, meta)
    if existing:
        return existing

    anime = Anime(**meta) if meta else Anime(title=cleaned)
    db.add(anime)
    db.flush()
    return anime


def build_episode_hint(title: str, summary: str | None = None) -> str | None:
    episode, season = metadata.extract_hints(title)
    if not episode and not season and summary:
        episode, season = metadata.extract_hints(summary)
    if season and episode:
        return f"{season} · {episode}"
    return season or episode


async def ingest_channel(db: Session, channel: Channel) -> list[Post]:
    """Pull a channel's feed and index any posts we haven't seen yet."""
    if not channel.rss_url:
        return []
    entries = await rss.fetch_feed(channel.rss_url)
    return await ingest_entries(db, channel, entries)


async def ingest_entries(db: Session, channel: Channel, entries: list[dict]) -> list[Post]:
    """Index already-normalized entries (from RSS or a live Telegram update)."""
    if channel.kind == "index":
        return await _ingest_index_entries(db, channel, entries)
    return await _ingest_feed_entries(db, channel, entries)


async def _ingest_feed_entries(db: Session, channel: Channel, entries: list[dict]) -> list[Post]:
    """One post per feed item; the item title names the anime."""
    new_posts: list[Post] = []
    for entry in entries:
        message_id = entry.get("message_id")
        if message_id is None:
            continue
        if db.scalar(
            select(Post).where(
                Post.channel_id == channel.id,
                Post.message_id == message_id,
                Post.entry_index == 0,
            )
        ):
            continue

        raw_title = entry.get("title") or ""
        anime = await match_or_create_anime(db, raw_title)
        post = Post(
            channel_id=channel.id,
            anime_id=anime.id if anime else None,
            message_id=message_id,
            caption=entry.get("summary") or raw_title,
            episode_hint=build_episode_hint(raw_title, entry.get("summary")),
            posted_at=entry.get("published_at"),
        )
        db.add(post)
        new_posts.append(post)

    if new_posts:
        db.commit()
        log.info("Indexed %d new post(s) from %s", len(new_posts), channel.title)
    return new_posts


async def _ingest_index_entries(db: Session, channel: Channel, entries: list[dict]) -> list[Post]:
    """A curated channel: every name+link pair inside a post becomes an entry."""
    new_posts: list[Post] = []
    for entry in entries:
        message_id = entry.get("message_id")
        if message_id is None:
            continue
        found = index_parser.parse_entries(entry.get("summary"), entry.get("entities"))
        for item in found:
            if db.scalar(
                select(Post).where(
                    Post.channel_id == channel.id,
                    Post.message_id == message_id,
                    Post.entry_index == item["index"],
                )
            ):
                continue
            title = item["title"] or entry.get("title") or ""
            if not title:
                continue
            anime = await match_or_create_anime(db, title)
            post = Post(
                channel_id=channel.id,
                anime_id=anime.id if anime else None,
                message_id=message_id,
                entry_index=item["index"],
                url=item["url"],
                caption=title,
                episode_hint=build_episode_hint(title),
                posted_at=entry.get("published_at"),
            )
            db.add(post)
            new_posts.append(post)

    if new_posts:
        db.commit()
        log.info("Indexed %d new entr(y/ies) from %s", len(new_posts), channel.title)
    return new_posts


async def announce_new_posts(posts: list[Post]) -> None:
    """Optionally announce freshly indexed anime to a configured channel."""
    settings = get_settings()
    if not settings.announce_chat_id or not posts:
        return
    from .telegram import TelegramClient

    try:
        tg = TelegramClient()
    except RuntimeError:
        return

    seen: set[int] = set()
    for post in posts:
        anime = post.anime
        if not anime or anime.id in seen:
            continue
        seen.add(anime.id)
        caption = f"<b>{anime.title}</b>"
        if anime.genres:
            caption += f"\n<i>{anime.genres}</i>"
        if anime.episodes:
            caption += f"\n{anime.episodes} episodes"
        caption += f"\n\nNow streaming on {post.channel.title}"
        markup = {"inline_keyboard": [[{"text": "▶️ Open", "url": post.target_link}]]}
        try:
            await tg.send_message(settings.announce_chat_id, caption, reply_markup=markup)
        except Exception as exc:  # noqa: BLE001 - announcement is best effort
            log.warning("Announce failed: %s", exc)
