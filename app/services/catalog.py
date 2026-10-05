"""Build the curated catalog from an index channel.

The catalog is the set of anime the Mini App shows: names taken from the index
channel's own posts, each enriched with AniList metadata and pointed at the
channel that actually hosts the files.
"""
from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..models import Anime, AnimeEntry, EntryChannel, Episode, QualityLink, Season
from . import metadata, telegram_web

log = logging.getLogger("index.catalog")

_QUALITY = re.compile(r"(480p|720p|1080p|2160p|4k|hd[-_ ]?rip|web[-_ ]?rip|bluray|bdrip)", re.I)
_BOT_DEEP_LINK = re.compile(r"t\.me/([A-Za-z0-9_]*bot)(?:[/?#]|$)", re.I)
_EXT = re.compile(r"\.(mkv|mp4|avi|mov|webm|zip|rar|7z|torrent)$", re.I)
_NOISE = re.compile(
    r"\b(1080p|720p|480p|2160p|4k|x264|x265|hevc|10bit|aac|dual|multi|sub|dub|"
    r"batch|complete|uncensored|bd|web|webrip|bluray|hdrip|amzn|cr|nf|eng|jpn)\b",
    re.I,
)


def normalize_name(raw: str) -> str:
    """Strip index-channel decoration from an entry name."""
    name = re.sub(r"^\[\s*\d+\s*\]\s*", "", raw or "").strip()
    name = re.sub(r"\s*[|│]\s*", " ", name)
    return re.sub(r"\s+", " ", name).strip(" -–—:.")


def quality_from_link(url: str) -> str | None:
    match = _QUALITY.search(url or "")
    return match.group(1).lower().replace(" ", "-").replace("_", "-") if match else None


def bot_from_link(url: str) -> str | None:
    match = _BOT_DEEP_LINK.search(url or "")
    return match.group(1) if match else None


async def import_from_channel(db: Session, username: str, enrich: bool = False) -> dict:
    """Seed/refresh the catalog from a public index channel's web preview.

    Entries are written immediately so the Mini App is usable right away;
    metadata enrichment is a separate, slower pass (``enrich_pending``).
    """
    entries, details = await telegram_web.fetch_channel(username)
    source = username.lstrip("@")
    result = await import_entries(db, source, entries, enrich=False)
    result["details"] = await apply_details(db, source, details)
    if enrich:
        await enrich_pending(db)
    return result


async def apply_details(db: Session, source: str, details: list[dict]) -> int:
    """Merge detail-card fields onto matching catalog rows.

    Detail cards carry season/episode counts, audio and synopsis but usually no
    channel link, so they enrich the entry the series list already created.
    """
    if not details:
        return 0
    rows = db.scalars(select(AnimeEntry).where(AnimeEntry.source_chat == source)).all()
    by_name = {_key(r.raw_name): r for r in rows}
    for r in rows:
        if r.match_key and r.match_key not in by_name:
            by_name[r.match_key] = r
    applied = 0
    for detail in details:
        row = by_name.get(_key(detail["name"]))
        if row is None:
            continue
        row.note = _detail_note(detail)
        if not row.match_key:
            row.match_key = _key(row.raw_name)
        if row.anime_id is None:
            anime = Anime(title=detail["name"], source="index")
            db.add(anime)
            db.flush()
            row.anime_id = anime.id
        anime = row.anime
        if anime is not None:
            if detail.get("genres"):
                anime.genres = detail["genres"]
            if detail.get("synopsis") and not anime.synopsis:
                anime.synopsis = detail["synopsis"]
        applied += 1
    db.commit()
    log.info("Applied %d detail card(s) from %s", applied, source)
    return applied


def _detail_note(detail: dict) -> str:
    parts = []
    if detail.get("season"):
        parts.append(f"Season {detail['season']}")
    if detail.get("episodes"):
        parts.append(f"{detail['episodes']} episodes")
    if detail.get("audio"):
        parts.append(detail["audio"])
    return " · ".join(parts)


def _key(name: str) -> str:
    """Loose comparison key so 'Attack on Titan' matches 'Attack On Titan'."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def match_entry(db: Session, filename: str) -> AnimeEntry | None:
    """Find the catalog row a file name belongs to.

    Database channels store files with messy names (release tags, quality,
    episode numbers), so match on the longest catalog name contained in the
    cleaned file name.
    """
    cleaned = normalize_name(_NOISE.sub(" ", _EXT.sub("", filename or "")))
    key = _key(cleaned)
    if len(key) < 3:
        return None

    best: AnimeEntry | None = None
    best_len = 0
    for row in db.scalars(select(AnimeEntry)).all():
        row_key = _key(row.raw_name)
        if len(row_key) < 3:
            continue
        if row_key in key and len(row_key) > best_len:
            best, best_len = row, len(row_key)
    return best


async def attach_file(
    db: Session, filename: str, url: str, quality: str | None = None
) -> QualityLink | None:
    """Record a database-channel file against the anime it names."""
    entry = match_entry(db, filename)
    if entry is None:
        return None
    quality = quality or quality_from_link(filename) or "unknown"
    return await add_manual_quality(db, entry, quality, url)


async def add_manual_quality(
    db: Session, entry: AnimeEntry, quality: str, url: str
) -> QualityLink:
    """Attach (or refresh) a download link on a catalog entry."""
    quality = (quality or "unknown").strip().lower()
    existing = db.scalar(
        select(QualityLink).where(QualityLink.entry_id == entry.id, QualityLink.url == url)
    )
    if existing is not None:
        existing.quality = quality
        db.commit()
        return existing

    link = QualityLink(
        entry_id=entry.id,
        quality=quality,
        url=url,
        batch=bool(re.search(r"batch|complete", url, re.I)),
        via_bot=bot_from_link(url),
    )
    db.add(link)
    db.commit()
    return link


def display_title(entry: AnimeEntry) -> str:
    """The English title the Mini App shows.

    AniList gives an English title, a romaji title, and a native (Japanese)
    one; we only ever show the first two so the grid stays in English.
    """
    anime = entry.anime
    if anime is None:
        return entry.raw_name
    return anime.title_english or anime.title or entry.raw_name


def _resolve_row(
    db: Session, source: str, index: int, name: str, match_key: str
) -> tuple[AnimeEntry, bool]:
    """Find the row this entry belongs to, merging duplicates across channels.

    A row is identified first by the channel+ordinal it came from, then by its
    match key so an anime your friend also lists updates the same row instead of
    creating a second card.
    """
    row = db.scalar(
        select(AnimeEntry).where(
            AnimeEntry.source_chat == source, AnimeEntry.entry_index == index
        )
    )
    if row is not None:
        return row, False

    row = db.scalar(select(AnimeEntry).where(AnimeEntry.match_key == match_key))
    if row is not None:
        return row, False

    row = AnimeEntry(source_chat=source, entry_index=index, raw_name=name)
    db.add(row)
    return row, True


def _record_channel(
    db: Session, row: AnimeEntry, source: str, url: str | None, kind: str | None
) -> None:
    """Attach (or refresh) the link a given index channel points at."""
    link = db.scalar(
        select(EntryChannel).where(
            EntryChannel.entry_id == row.id, EntryChannel.source_chat == source
        )
    )
    if link is None:
        if row.id is None:
            db.flush()
        link = EntryChannel(entry_id=row.id, source_chat=source)
        db.add(link)
    link.url = url
    link.kind = kind


async def import_entries(
    db: Session, source: str, entries: list[dict], enrich: bool = False
) -> dict:
    """Upsert catalog rows for every scraped entry.

    Rows are keyed by a normalized name so the same anime listed in two index
    channels merges into one card carrying both links.
    """
    created = updated = merged = 0
    for item in entries:
        name = normalize_name(item.get("name", ""))
        if not name:
            continue
        index = item.get("index", 0)
        url = item.get("url")
        kind = item.get("kind") or telegram_web.channel_kind(url or "")
        key = _key(name)

        row, is_new = _resolve_row(db, source, index, name, key)
        if is_new:
            created += 1
        else:
            updated += 1
            if row.source_chat != source:
                merged += 1

        # First listing wins for display order; the channel's own name is kept.
        if is_new:
            row.raw_name = name
            row.channel_link = url
            row.channel_kind = kind
        if not row.match_key:
            row.match_key = key
        _record_channel(db, row, source, url, kind)

    db.commit()
    if enrich:
        await enrich_pending(db)
    log.info(
        "Catalog import from %s: %d new, %d updated, %d merged", source, created, updated, merged
    )
    return {"created": created, "updated": updated, "merged": merged, "total": len(entries)}


def backfill_match_keys(db: Session) -> int:
    """Give older rows a match key and a channel link so they merge going forward."""
    rows = db.scalars(select(AnimeEntry)).all()
    changed = 0
    for row in rows:
        if row.match_key:
            continue
        row.match_key = _key(row.raw_name)
        if row.source_chat and row.channel_link:
            _record_channel(db, row, row.source_chat, row.channel_link, row.channel_kind)
        changed += 1
    if changed:
        db.commit()
        log.info("Backfilled %d catalog row(s)", changed)
    return changed


async def enrich_pending(db: Session, limit: int | None = None, workers: int = 6) -> dict:
    """Fill in AniList metadata for catalog rows that have none yet.

    AniList enforces a global request spacing, so this is deliberately a
    background pass: rows are committed in batches and the UI shows whatever is
    already enriched.
    """
    stmt = (
        select(AnimeEntry)
        .options(selectinload(AnimeEntry.anime))
        .order_by(AnimeEntry.id.asc())
    )
    rows = [r for r in db.scalars(stmt).all() if _needs_enrichment(r)]
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        return {"enriched": 0, "remaining": 0}

    semaphore = asyncio.Semaphore(workers)

    async def fetch(row: AnimeEntry) -> tuple[int, dict | None]:
        # Network only: SQLite takes a write lock the moment the session has
        # pending changes, so it must not be held across an await. The timeout
        # stops one unsearchable title from stalling the whole batch.
        async with semaphore:
            try:
                meta = await asyncio.wait_for(metadata.search_anime(row.raw_name), timeout=20)
            except Exception:  # noqa: BLE001 - an unsearchable title is not fatal
                meta = None
            return row.id, meta

    for start in range(0, len(rows), 25):
        batch = rows[start : start + 25]
        for row_id, meta in await asyncio.gather(*(fetch(r) for r in batch)):
            row = db.get(AnimeEntry, row_id)
            if row is None:
                continue
            if meta is None:
                # A miss is often transient (rate limit, hiccup). Keep the
                # placeholder row but leave it pending so the next pass retries.
                if row.anime_id is None:
                    anime = Anime(title=row.raw_name, source="index")
                    db.add(anime)
                    db.flush()
                    row.anime_id = anime.id
            else:
                anime = _upsert_anime(db, meta, row.raw_name)
                row.anime_id = anime.id
        db.commit()

    log.info("Enriched %d catalog entr(y/ies)", len(rows))
    return {"enriched": len(rows), "remaining": 0}


def _needs_enrichment(row: AnimeEntry) -> bool:
    """True when a row has no metadata, or only a placeholder from a miss.

    Placeholder rows (``source == "index"``) carry no poster, so they are retried
    on later passes instead of being treated as done.
    """
    if row.anime_id is None:
        return True
    return row.anime is not None and row.anime.source == "index"


def _upsert_anime(db: Session, meta: dict, fallback_title: str) -> Anime:
    """Find or create the Anime row for a metadata result."""
    anime = None
    mal_id = meta.get("mal_id")
    if mal_id:
        anime = db.scalar(select(Anime).where(Anime.mal_id == mal_id))
    if anime is None:
        anime = Anime(title=meta.get("title") or fallback_title)
        db.add(anime)
    anime.mal_id = mal_id or anime.mal_id
    anime.source = meta.get("source")
    anime.external_id = meta.get("external_id")
    anime.title = meta.get("title") or anime.title
    anime.title_english = meta.get("title_english") or anime.title_english
    # Never surface the native (Japanese) title in the app; keep it for reference.
    anime.title_japanese = meta.get("title_japanese")
    anime.synopsis = meta.get("synopsis")
    anime.poster_url = meta.get("poster_url")
    anime.banner_url = meta.get("banner_url")
    anime.genres = meta.get("genres")
    anime.studio = meta.get("studio") or anime.studio
    anime.episodes = meta.get("episodes")
    anime.status = meta.get("status")
    anime.score = meta.get("score")
    anime.year = meta.get("year")
    db.flush()
    return anime


def _register_quality(db: Session, row: AnimeEntry, url: str | None, name: str) -> None:
    """Turn a quality-suffixed link into a QualityLink row.

    Curated channels often point 720p/1080p entries at their own channel, so the
    link itself is the download destination; we only record it when it clearly
    names a quality.
    """
    if not url:
        return
    quality = quality_from_link(url) or quality_from_link(name)
    if not quality:
        return
    bot = bot_from_link(url)
    exists = db.scalar(
        select(QualityLink).where(
            QualityLink.entry_id == row.id,
            QualityLink.quality == quality,
            QualityLink.url == url,
        )
    )
    if exists:
        return
    db.add(
        QualityLink(
            entry_id=row.id,
            quality=quality,
            url=url,
            via_bot=bot,
            batch=bool(re.search(r"batch|complete", url, re.I)),
        )
    )


def _channel_links(db: Session, row: AnimeEntry) -> list[dict]:
    """Every index channel that lists this anime, with its link."""
    links = db.scalars(
        select(EntryChannel).where(EntryChannel.entry_id == row.id).order_by(EntryChannel.id)
    ).all()
    return [
        {"source": link.source_chat, "url": link.url, "kind": link.kind}
        for link in links
        if link.url
    ]


def collapse_duplicates(db: Session) -> dict:
    """Merge rows that share a match key, keeping the oldest as canonical.

    Runs after imports so an anime listed by two index channels becomes one card
    with a link per channel instead of two near-identical cards.
    """
    rows = db.scalars(select(AnimeEntry).order_by(AnimeEntry.id.asc())).all()
    canonical: dict[str, AnimeEntry] = {}
    merged = 0
    for row in rows:
        key = row.match_key or _key(row.raw_name)
        row.match_key = key
        if not key:
            continue
        keeper = canonical.get(key)
        if keeper is None:
            canonical[key] = row
            continue
        _merge_entries(db, keeper, row)
        merged += 1
    if merged:
        db.commit()
        log.info("Collapsed %d duplicate catalog row(s)", merged)
    return {"merged": merged}


def _merge_entries(db: Session, keeper: AnimeEntry, dup: AnimeEntry) -> None:
    """Move a duplicate row's channels and qualities onto the canonical row."""
    for link in db.scalars(select(EntryChannel).where(EntryChannel.entry_id == dup.id)).all():
        existing = db.scalar(
            select(EntryChannel).where(
                EntryChannel.entry_id == keeper.id, EntryChannel.source_chat == link.source_chat
            )
        )
        if existing is None:
            link.entry_id = keeper.id
        else:
            existing.url = link.url or existing.url
            existing.kind = link.kind or existing.kind
            db.delete(link)

    for quality in db.scalars(select(QualityLink).where(QualityLink.entry_id == dup.id)).all():
        existing = db.scalar(
            select(QualityLink).where(
                QualityLink.entry_id == keeper.id,
                QualityLink.quality == quality.quality,
                QualityLink.url == quality.url,
            )
        )
        if existing is None:
            quality.entry_id = keeper.id
        else:
            db.delete(quality)

    if keeper.anime_id is None and dup.anime_id is not None:
        keeper.anime_id = dup.anime_id
    if not keeper.note and dup.note:
        keeper.note = dup.note
    if not keeper.channel_link and dup.channel_link:
        keeper.channel_link = dup.channel_link
        keeper.channel_kind = dup.channel_kind
    db.flush()
    db.delete(dup)


async def refresh_all(db: Session) -> dict:
    """Re-import every index channel listed in settings."""
    refs = get_settings().index_channel_refs
    backfill_match_keys(db)
    total = {"created": 0, "updated": 0, "merged": 0}
    for ref in sorted(refs):
        result = await import_from_channel(db, ref)
        total["created"] += result["created"]
        total["updated"] += result["updated"]
        total["merged"] += result.get("merged", 0)
    total["collapsed"] = collapse_duplicates(db)["merged"]
    return total


def find_entry(db: Session, name: str) -> AnimeEntry | None:
    """Find the catalog row a free-text title refers to.

    Matches on the normalized key first, then on the raw name, then on the
    longest catalog name contained in the query, so "Demon Slayer" finds
    "Demon Slayer: Kimetsu no Yaiba".
    """
    key = _key(name)
    if len(key) < 3:
        return None

    exact = db.scalar(select(AnimeEntry).where(AnimeEntry.match_key == key))
    if exact is not None:
        return exact

    for row in db.scalars(select(AnimeEntry)).all():
        if _key(row.raw_name) == key:
            return row

    best: AnimeEntry | None = None
    best_len = 0
    for row in db.scalars(select(AnimeEntry)).all():
        row_key = _key(row.raw_name)
        if len(row_key) >= 3 and row_key in key and len(row_key) > best_len:
            best, best_len = row, len(row_key)
    return best


def upsert_episodes(db: Session, entry: AnimeEntry, parsed: dict) -> dict:
    """Attach parsed seasons and episodes to a catalog entry.

    Seasons and episodes are matched by number, so re-sending a corrected
    message updates the links in place instead of duplicating them.
    """
    seasons_added = seasons_updated = episodes_added = episodes_updated = 0

    for season_data in parsed.get("seasons", []):
        number = int(season_data.get("number") or 1)
        season = db.scalar(
            select(Season).where(Season.entry_id == entry.id, Season.number == number)
        )
        if season is None:
            season = Season(entry_id=entry.id, number=number)
            db.add(season)
            db.flush()
            seasons_added += 1
        else:
            seasons_updated += 1

        for field in ("audio", "subtitles", "quality_tags", "poster_url", "synopsis"):
            value = season_data.get(field)
            if value:
                setattr(season, field, value)
        # Entry-level details mirror the first season that carried them, so the
        # grid and hero can show them without walking the season list.
        if season_data.get("audio") and not entry.audio:
            entry.audio = season_data["audio"]
        if season_data.get("subtitles") and not entry.subtitles:
            entry.subtitles = season_data["subtitles"]
        if season_data.get("quality_tags") and not entry.quality_tags:
            entry.quality_tags = season_data["quality_tags"]

        for episode_data in season_data.get("episodes", []):
            ep_number = int(episode_data.get("number") or 1)
            url = episode_data.get("url")
            if not url:
                continue
            episode = db.scalar(
                select(Episode).where(Episode.season_id == season.id, Episode.number == ep_number)
            )
            if episode is None:
                db.add(
                    Episode(
                        season_id=season.id,
                        number=ep_number,
                        url=url,
                        title=episode_data.get("title"),
                    )
                )
                episodes_added += 1
            else:
                episode.url = url
                if episode_data.get("title"):
                    episode.title = episode_data["title"]
                episodes_updated += 1

    db.commit()
    log.info(
        "Episodes for %s: +%d season(s), %d updated, +%d episode(s), %d updated",
        entry.raw_name,
        seasons_added,
        seasons_updated,
        episodes_added,
        episodes_updated,
    )
    return {
        "seasons_added": seasons_added,
        "seasons_updated": seasons_updated,
        "episodes_added": episodes_added,
        "episodes_updated": episodes_updated,
    }


def entry_seasons(entry: AnimeEntry) -> list[dict]:
    """Seasons with their episodes, ready for the Mini App."""
    result = []
    for season in sorted(entry.seasons, key=lambda s: s.number):
        result.append(
            {
                "id": season.id,
                "number": season.number,
                "title": season.title or f"Season {season.number:02d}",
                "poster_url": season.poster_url
                or (entry.anime.poster_url if entry.anime else None),
                "synopsis": season.synopsis,
                "audio": season.audio or entry.audio,
                "subtitles": season.subtitles or entry.subtitles,
                "quality_tags": season.quality_tags or entry.quality_tags,
                "episodes": [
                    {"id": ep.id, "number": ep.number, "title": ep.title, "url": ep.url}
                    for ep in sorted(season.episodes, key=lambda e: e.number)
                ],
            }
        )
    return result

