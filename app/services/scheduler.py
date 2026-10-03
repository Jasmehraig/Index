"""Background RSS polling."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from ..config import get_settings
from ..models import Channel, session_scope
from .ingest import announce_new_posts, ingest_channel

log = logging.getLogger("index.scheduler")

_scheduler: AsyncIOScheduler | None = None


async def poll_all_channels() -> None:
    db = session_scope()
    try:
        channels = db.scalars(select(Channel).where(Channel.rss_url.is_not(None))).all()
        for channel in channels:
            try:
                posts = await ingest_channel(db, channel)
                await announce_new_posts(posts)
            except Exception as exc:  # noqa: BLE001 - keep polling other channels
                log.warning("Poll failed for %s: %s", channel.title, exc)
    finally:
        db.close()


async def refresh_catalog() -> None:
    """Re-read every configured index channel so the catalog tracks them."""
    settings = get_settings()
    if not settings.index_channel_usernames:
        return
    from . import catalog

    db = session_scope()
    try:
        await catalog.refresh_all(db)
    except Exception as exc:  # noqa: BLE001 - keep the scheduler alive
        log.warning("Catalog refresh failed: %s", exc)
    finally:
        db.close()


def start_scheduler() -> AsyncIOScheduler | None:
    global _scheduler
    settings = get_settings()
    if settings.poll_interval_minutes <= 0:
        return None
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        poll_all_channels,
        "interval",
        minutes=settings.poll_interval_minutes,
        id="poll_channels",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        refresh_catalog,
        "interval",
        minutes=max(settings.poll_interval_minutes, 30),
        id="refresh_catalog",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    log.info("Scheduler started (every %s min)", settings.poll_interval_minutes)
    return _scheduler


def stop_scheduler() -> None:
    if _scheduler:
        _scheduler.shutdown(wait=False)
