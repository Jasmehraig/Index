"""Telegram webhook: bot commands, channel posts, and membership changes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Channel, Post, session_scope
from ..services import episode_parser, index_parser, ongoing_parser, rss
from ..services.ingest import build_episode_hint, ingest_entries, match_or_create_anime
from ..services.telegram import TelegramClient, TelegramError

log = logging.getLogger("index.webhook")
router = APIRouter(tags=["telegram"])


def _mini_app_keyboard() -> dict:
    settings = get_settings()
    url = settings.public_base_url.rstrip("/") + "/"
    return {"inline_keyboard": [[{"text": "🍿 Open Index", "url": url}]]}


def _derive_title(message: dict) -> str:
    text = (message.get("caption") or message.get("text") or "").strip()
    first_line = text.splitlines()[0] if text else ""
    return first_line[:400]


def _get_channel(db: Session, chat_id: int) -> Channel | None:
    return db.scalar(select(Channel).where(Channel.chat_id == chat_id))


async def _register_channel(chat: dict, added_by: int | None = None) -> Channel:
    """Create/update a channel row and try to discover an RSS feed."""
    db = session_scope()
    try:
        channel = _get_channel(db, chat["id"])
        title = chat.get("title") or chat.get("username") or str(chat["id"])
        if channel is None:
            channel = Channel(chat_id=chat["id"], title=title)
            db.add(channel)
        channel.title = title
        channel.username = chat.get("username")
        if added_by is not None:
            channel.owner_user_id = added_by
        if _is_index_channel(chat):
            channel.kind = "index"

        if not channel.rss_url:
            channel.rss_url = await _discover_rss(chat)
        if not channel.invite_link:
            try:
                tg = TelegramClient()
                channel.invite_link = await tg.export_chat_invite_link(chat["id"])
            except (RuntimeError, TelegramError) as exc:
                log.info("Could not export invite link for %s: %s", chat["id"], exc)
        db.commit()
        db.refresh(channel)
        return channel
    finally:
        db.close()


def _is_index_channel(chat: dict) -> bool:
    """True when this chat is listed in INDEX_CHANNELS (id or @username)."""
    refs = get_settings().index_channel_refs
    if not refs:
        return False
    username = (chat.get("username") or "").lower()
    chat_id = str(chat.get("id", "")).lower()
    return bool(
        (username and f"@{username}" in refs)
        or (username and username in refs)
        or (chat_id and chat_id in refs)
    )


async def _discover_rss(chat: dict) -> str | None:
    """Try to find the channel's RSS feed via configured RSS bridge templates."""
    settings = get_settings()
    username = chat.get("username")
    chat_id = chat.get("id")
    templates = [t.strip() for t in settings.rss_providers.split(",") if t.strip()]
    for template in templates:
        if "{username}" in template and not username:
            continue
        candidate = template.format(username=username or "", chat_id=str(chat_id).lstrip("-"))
        entries = await rss.fetch_feed(candidate)
        if entries:
            return candidate
    return None


async def _index_channel_post(db: Session, message: dict, channel: Channel) -> list[Post]:
    """Index a channel post the moment Telegram delivers it (no RSS needed)."""
    message_id = message.get("message_id")
    if message_id is None:
        return []
    text = message.get("caption") or message.get("text") or ""
    entities = message.get("caption_entities") or message.get("entities") or []
    title = _derive_title(message)
    if not title and channel.kind != "index":
        return []

    # A curated channel can be recognised on the fly by its name+link posts.
    if channel.kind != "index" and index_parser.looks_like_index(text, entities):
        channel.kind = "index"
        log.info("Detected index channel: %s", channel.title)

    entry = {
        "title": title,
        "summary": text,
        "entities": entities,
        "message_id": message_id,
        "link": None,
        "published_at": None,
    }
    posts = await ingest_entries(db, channel, [entry])
    await _attach_channel_file(db, message, channel, message_id)
    if posts:
        db.refresh(channel)
    return posts


async def _attach_channel_file(
    db: Session, message: dict, channel: Channel, message_id: int
) -> None:
    """Record a database-channel file against the anime its name matches."""
    from ..services import catalog

    media = message.get("document") or message.get("video") or message.get("audio") or {}
    filename = media.get("file_name")
    if not filename:
        return
    url = _message_link(channel, message_id)
    try:
        link = await catalog.attach_file(db, filename, url)
    except Exception as exc:  # noqa: BLE001 - matching is best effort
        log.warning("File attach failed for %r: %s", filename, exc)
        return
    if link is not None:
        log.info("Attached file %r to %s (%s)", filename, link.entry.raw_name, link.quality)


def _message_link(channel: Channel, message_id: int) -> str:
    """A tappable link to a specific message in a channel."""
    if channel.username:
        return f"https://t.me/{channel.username}/{message_id}"
    if channel.invite_link:
        return f"{channel.invite_link.rstrip('/')}/{message_id}"
    return f"https://t.me/c/{str(channel.chat_id).lstrip('-100')}/{message_id}"


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    settings = get_settings()
    if settings.webhook_secret and x_telegram_bot_api_secret_token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid secret token")

    update = await request.json()

    try:
        if "my_chat_member" in update:
            await _handle_membership(update["my_chat_member"])
        elif "channel_post" in update:
            await _handle_channel_post(update["channel_post"])
        elif "message" in update:
            await _handle_message(update["message"])
    except Exception as exc:  # noqa: BLE001 - never fail the webhook
        log.exception("Webhook handling error: %s", exc)

    return {"ok": True}


async def _handle_membership(update: dict) -> None:
    chat = update.get("chat") or {}
    status = (update.get("new_chat_member") or {}).get("status")
    if chat.get("type") != "channel":
        return

    if status in ("administrator", "member"):
        added_by = (update.get("from") or {}).get("id")
        channel = await _register_channel(chat, added_by)
        note = (
            f"✅ <b>{channel.title}</b> is now being indexed."
            + ("\nFeed discovered automatically." if channel.rss_url else "\nNo RSS feed found yet.")
        )
        await _notify(chat["id"], note)
    elif status in ("left", "kicked"):
        db = session_scope()
        try:
            channel = _get_channel(db, chat["id"])
            if channel:
                db.delete(channel)
                db.commit()
        finally:
            db.close()


async def _handle_channel_post(message: dict) -> None:
    chat = message.get("chat") or {}
    if chat.get("type") != "channel":
        return
    db = session_scope()
    try:
        channel = _get_channel(db, chat["id"])
        if channel is None:
            await _register_channel(chat)
            channel = _get_channel(db, chat["id"])
        if channel is None:
            return
        posts = await _index_channel_post(db, message, channel)
        if posts:
            log.info("Indexed %d entry(ies) from %s", len(posts), channel.title)
    finally:
        db.close()


async def _handle_message(message: dict) -> None:
    text = (message.get("text") or "").strip()
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id")
    if not text.startswith("/"):
        # A plain "ongoing anime" list replaces the Ongoing rail without a command.
        if ongoing_parser.looks_like_ongoing_message(text):
            await _send_ongoing(chat_id, text, user_id, message.get("entities") or [])
            return
        # A plain message in the episode format adds episodes without a command.
        if episode_parser.looks_like_episode_message(text):
            await _send_episodes(chat_id, text, user_id)
        return

    command = text.split()[0].split("@")[0].lower()
    if command in ("/start", "/app", "/index"):
        await _send_start(chat_id)
    elif command == "/help":
        await _send_help(chat_id)
    elif command == "/channels":
        await _send_channels(chat_id)
    elif command in ("/refresh", "/rescan"):
        await _send_refresh(chat_id, message.get("from", {}).get("id"))
    elif command == "/rss":
        await _send_set_rss(chat_id, text, message.get("from", {}).get("id"))
    elif command in ("/indexchannel", "/setindex"):
        await _send_set_kind(chat_id, text, message.get("from", {}).get("id"), "index")
    elif command in ("/feed", "/setfeed"):
        await _send_set_kind(chat_id, text, message.get("from", {}).get("id"), "feed")
    elif command == "/import":
        await _send_import(chat_id, text, message.get("from", {}).get("id"))
    elif command in ("/catalog", "/indexchannelimport"):
        await _send_catalog_import(chat_id, text)
    elif command == "/quality":
        await _send_quality(chat_id, text, message.get("from", {}).get("id"))
    elif command in ("/episodes", "/addepisodes"):
        await _send_episodes(chat_id, text, user_id)
    elif command in ("/ongoing", "/airing"):
        await _send_ongoing(chat_id, text, user_id, message.get("entities") or [])
    elif command == "/enrich":
        await _send_enrich(chat_id, user_id)


async def _send_episodes(chat_id: int, text: str, user_id: int | None) -> None:
    """Attach seasons and episodes from an episode-format message.

    The message names a title, then lists seasons, audio, subtitles and episode
    links. The body is the text after an optional ``/episodes`` command word.
    """
    from ..services import catalog as catalog_service

    body = text
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        body = parts[1] if len(parts) > 1 else ""

    parsed = episode_parser.parse_episode_message(body)
    if not parsed or not parsed["name"]:
        await _notify(chat_id, _EPISODE_HELP)
        return
    if not any(season["episodes"] for season in parsed["seasons"]):
        await _notify(
            chat_id,
            f"⚠️ Found <b>{parsed['name']}</b> but no episode links. "
            "Add lines like <code>Episode 1 - https://t.me/…</code>.",
        )
        return

    db = session_scope()
    try:
        if not _is_admin(db, user_id):
            await _notify(chat_id, "🔒 Only owners and admins can add episodes.")
            return
        entry = catalog_service.find_entry(db, parsed["name"])
        if entry is None:
            await _notify(
                chat_id,
                f"❌ No catalog entry matches <b>{parsed['name']}</b>.\n"
                "Build the catalog first with <code>/catalog @YourChannel</code>, "
                "or check the spelling.",
            )
            return
        result = catalog_service.upsert_episodes(db, entry, parsed)
        seasons = catalog_service.entry_seasons(entry)
    finally:
        db.close()

    lines = [f"✅ Episodes added to <b>{entry.raw_name}</b>"]
    for season in seasons:
        lines.append(f"• Season {season['number']:02d}: {len(season['episodes'])} episode(s)")
    lines.append(
        f"\n<b>+{result['seasons_added']}</b> season(s), "
        f"<b>+{result['episodes_added']}</b> episode(s)"
        + (f", {result['episodes_updated']} refreshed" if result["episodes_updated"] else "")
    )
    await _notify(chat_id, "\n".join(lines), reply_markup=_mini_app_keyboard())


_ONGOING_HELP = (
    "<b>Ongoing anime</b>\n\n"
    "Send a list like this (it replaces the current Ongoing list):\n"
    "<pre>ongoing anime\n"
    "Overgeared - https://t.me/overgeared_dual\n"
    "The Apothecary diaries - https://t.me/+r7zltHPqpOswY2Jl</pre>\n"
    "<b>Commands</b>\n"
    "<code>/ongoing</code> – show the current list\n"
    "<code>/ongoing add</code> + lines – add without replacing\n"
    "<code>/ongoing remove Title</code> – remove one title\n"
    "<code>/ongoing clear</code> – empty the list"
)


async def _send_ongoing(chat_id: int, text: str, user_id: int | None, entities: list[dict]) -> None:
    """Manage the Ongoing rail: replace, add, remove, clear or show the list."""
    from ..services import catalog as catalog_service

    body = text
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        body = parts[1] if len(parts) > 1 else ""
    first, _, rest = body.partition("\n")
    word = first.strip().split(maxsplit=1)
    action = word[0].lower() if word else ""
    if "://" in first:
        # "Clear - https://t.me/x" is a title, not the clear command.
        action = ""

    db = session_scope()
    try:
        # Showing the list is public; everything else needs an admin.
        if not body.strip():
            current = catalog_service.list_ongoing(db)
            if not current:
                await _notify(chat_id, "No ongoing anime yet.\n\n" + _ONGOING_HELP)
                return
            lines = [f"📺 <b>{len(current)} ongoing anime</b>", ""]
            for i, (name, url) in enumerate(current, 1):
                lines.append(f"{i}. <a href=\"{url}\">{name}</a>" if url else f"{i}. {name}")
            await _notify(chat_id, "\n".join(lines), reply_markup=_mini_app_keyboard())
            return

        if not _is_admin(db, user_id):
            await _notify(
                chat_id,
                "🔒 Only owners and admins can change the ongoing list. "
                "Add your Telegram id to <code>ADMIN_USER_IDS</code>.",
            )
            return

        if action == "clear":
            removed = catalog_service.clear_ongoing(db)
            await _notify(chat_id, f"🧹 Cleared {removed} title(s) from Ongoing.")
            return

        if action in ("remove", "delete", "rm"):
            name = first.strip()[len(word[0]) :].strip() or rest.strip()
            removed = catalog_service.remove_ongoing(db, name) if name else None
            if removed:
                await _notify(chat_id, f"✅ Removed <b>{removed}</b> from Ongoing.")
            else:
                await _notify(
                    chat_id,
                    "Could not find that title in the ongoing list. "
                    "Use <code>/ongoing</code> to see the exact names.",
                )
            return

        replace = action != "add"

        # Parse the full message: entity offsets are relative to it. Typed
        # "Name - link" lines and hyperlinked names both work.
        items = ongoing_parser.parse_ongoing(text, entities)
        if not items:
            await _notify(chat_id, "⚠️ I could not find any <i>Name - link</i> lines.\n\n" + _ONGOING_HELP)
            return

        result = catalog_service.set_ongoing(db, items, replace=replace)
    finally:
        db.close()

    lines = [
        f"✅ Ongoing list {'updated' if replace else 'extended'}: <b>{result['total']}</b> title(s)",
        f"• {result['linked']} already in the catalog, {result['created']} new",
        "• 🖼 Posters load in the background",
        "",
    ]
    lines += [f"{i}. {n}" for i, n in enumerate(result["names"], 1)]
    await _notify(chat_id, "\n".join(lines), reply_markup=_mini_app_keyboard())


async def _send_enrich(chat_id: int, user_id: int | None) -> None:
    """Start a poster/metadata pass in the background and say how many are pending.

    Not awaited here: a long pass inside the webhook would hit Telegram's timeout
    and the update would be redelivered.
    """
    from ..services import catalog as catalog_service

    db = session_scope()
    try:
        if not _is_admin(db, user_id):
            await _notify(chat_id, "🔒 Only owners and admins can run this.")
            return
        pending = catalog_service.count_unenriched(db)
    finally:
        db.close()
    if pending == 0:
        await _notify(chat_id, "✅ Every title already has a poster and details.")
        return
    catalog_service.schedule_enrichment()
    await _notify(
        chat_id,
        f"⏳ Fetching posters for <b>{pending}</b> title(s) in the background "
        "(about 1–2 per second). Send /enrich again later to see what is left. "
        "Titles that cannot be matched on AniList/MAL are retried every few hours.",
    )


_EPISODE_HELP = (
    "Send the title and its episodes in this format:\n\n"
    "<pre>Anime name - Demon Slayer\n"
    "Season - Season 01\n"
    "Language - English, Japanese\n"
    "Episode 1 - https://t.me/FileBot?start=…\n"
    "Episode 2 - https://t.me/FileBot?start=…</pre>\n"
    "Repeat the <b>Anime name</b>/<b>Season</b> block for more seasons. "
    "<b>Subtitle</b> and <b>Quality</b> lines are optional; every title shows "
    "480p, 720p, 1080p, HD-RIP and English Sub by default."
)


async def _send_catalog_import(chat_id: int, text: str) -> None:
    """Read a public index channel and rebuild the catalog from its posts."""
    from ..services import catalog as catalog_service

    parts = text.split(maxsplit=1)
    ref = parts[1].strip() if len(parts) > 1 else ""
    settings = get_settings()
    if not ref:
        refs = settings.index_channel_usernames
        if not refs:
            await _notify(
                chat_id,
                "Usage: <code>/catalog @IndexChannel</code>\n\n"
                "I read that channel's public preview, take every anime name and "
                "link it lists, and build the catalog the mini app shows. "
                "Set <code>INDEX_CHANNELS</code> to make it automatic.",
            )
            return
        ref = refs[0]

    ref = ref.lstrip("@").replace("https://t.me/", "").strip("/")
    if ref.startswith("+"):
        await _notify(
            chat_id,
            "⚠️ I can only read <b>public</b> channels by username. "
            "Send a public index channel like <code>/catalog @MyIndex</code>.",
        )
        return

    await _notify(chat_id, f"⏳ Reading @{ref}…")
    db = session_scope()
    try:
        catalog_service.backfill_match_keys(db)
        result = await catalog_service.import_from_channel(db, ref)
        collapsed = catalog_service.collapse_duplicates(db)
        pending = catalog_service.count_unenriched(db)
        # Posters, scores and genres load in the background after the import.
        catalog_service.schedule_enrichment()
    except Exception as exc:  # noqa: BLE001 - surface any scrape failure to the user
        log.warning("Catalog import failed for %s: %s", ref, exc)
        await _notify(chat_id, f"❌ Could not read @{ref}. Is it public?")
        return
    finally:
        db.close()

    merged_line = ""
    if result.get("merged") or collapsed.get("merged"):
        merged_line = (
            f"• 🔀 {result.get('merged', 0) + collapsed.get('merged', 0)} duplicate(s) "
            "merged into existing entries\n"
        )
    await _notify(
        chat_id,
        f"✅ Catalog updated from <b>@{ref}</b>\n"
        f"• {result['created']} new, {result['updated']} refreshed "
        f"({result['total']} listed)\n"
        f"• {result.get('details', 0)} detail card(s) merged\n"
        f"{merged_line}"
        f"• 🖼 Fetching posters for {pending} title(s) in the background \n\n"
        "Open the mini app to browse it.",
        reply_markup=_mini_app_keyboard(),
    )


async def _send_quality(chat_id: int, text: str, user_id: int | None) -> None:
    """Attach a manual download link to a catalog entry: /quality <id> <quality> <url>."""
    from ..models import AnimeEntry
    from ..services.catalog import add_manual_quality

    parts = text.split()
    if len(parts) < 4 or not parts[1].isdigit():
        await _notify(
            chat_id,
            "Usage: <code>/quality &lt;entry id&gt; &lt;quality&gt; &lt;link&gt;</code>\n"
            "Example: <code>/quality 12 1080p https://t.me/YourBot?start=abc</code>\n\n"
            "Entry ids are shown when you open a title in the mini app.",
        )
        return
    entry_id, quality, url = int(parts[1]), parts[2], parts[3]

    db = session_scope()
    try:
        if not _is_admin(db, user_id):
            await _notify(chat_id, "🔒 Only owners and admins can add links.")
            return
        entry = db.get(AnimeEntry, entry_id)
        if entry is None:
            await _notify(chat_id, f"No catalog entry with id <b>{entry_id}</b>.")
            return
        await add_manual_quality(db, entry, quality, url)
        await _notify(
            chat_id,
            f"✅ Added <b>{quality}</b> link to <b>{entry.raw_name}</b>.",
        )
    finally:
        db.close()


async def _send_start(chat_id: int) -> None:
    text = (
        "🎬 <b>Index</b> — your anime channel index.\n\n"
        "Browse every anime shared across the channels I watch: search, tap a "
        "poster, and jump straight to the channel post.\n\n"
        "Add me to a channel as admin and I'll start indexing it automatically."
    )
    await _notify(chat_id, text, reply_markup=_mini_app_keyboard())


async def _send_help(chat_id: int) -> None:
    text = (
        "<b>How Index works</b>\n\n"
        "• Add me to a channel as an admin → I read new posts and index the anime.\n"
        "• Titles are matched against AniList for posters and details.\n"
        "• Tap the mini app button to browse and search the index.\n\n"
        "<b>Two kinds of channels</b>\n"
        "• <b>Feed</b> — release channels: each post is one anime.\n"
        "• <b>Index</b> — curated channels that post lists of "
        "<i>name + link</i>. Every pair becomes a searchable entry that links "
        "straight to the target.\n\n"
        "<b>Commands</b>\n"
        "/start – open the mini app\n"
        "/channels – list indexed channels\n"
        "/refresh – re-scan feeds (owners/admins)\n"
        "/rss &lt;url&gt; – set a custom feed for your channel\n"
        "/indexchannel – treat your channel as a name+link index\n"
        "/feed – treat your channel as a release feed\n"
        "/import – paste a name+link list to index it manually\n"
        "/catalog &lt;@channel&gt; – build the catalog from an index channel\n"
        "/quality &lt;id&gt; &lt;quality&gt; &lt;link&gt; – add a download link\n"
        "/ongoing – manage the Ongoing rail (send an <i>ongoing anime</i> list)\n"
        "/enrich – fetch missing posters now\n\n"
        "<b>Adding episodes</b>\n"
        "Send the bot a message in this format and the episodes are attached to "
        "the matching title:\n"
        "<pre>Anime name - Demon Slayer\n"
        "Season - Season 01\n"
        "Language - English, Japanese\n"
        "Episode 1 - https://t.me/FileBot?start=…\n"
        "Episode 2 - https://t.me/FileBot?start=…</pre>\n"
        "Repeat the block for more seasons. <code>/episodes</code> does the same "
        "when the text is sent as a command."
    )
    await _notify(chat_id, text, reply_markup=_mini_app_keyboard())


async def _send_channels(chat_id: int) -> None:
    db = session_scope()
    try:
        channels = db.scalars(select(Channel).order_by(Channel.title.asc())).all()
        if not channels:
            await _notify(chat_id, "No channels indexed yet. Add me to a channel as admin.")
            return
        lines = [f"📚 <b>{len(channels)} channel(s) indexed</b>", ""]
        for ch in channels:
            lines.append(f"• <a href=\"{ch.link}\">{ch.title}</a>")
        await _notify(chat_id, "\n".join(lines), reply_markup=_mini_app_keyboard())
    finally:
        db.close()


def _is_admin(db: Session, user_id: int | None) -> bool:
    if user_id is None:
        return False
    settings = get_settings()
    if user_id in settings.admin_ids:
        return True
    return db.scalar(select(Channel).where(Channel.owner_user_id == user_id)) is not None


async def _send_refresh(chat_id: int, user_id: int | None) -> None:
    db = session_scope()
    try:
        if not _is_admin(db, user_id):
            await _notify(chat_id, "🔒 Only channel owners and admins can trigger a re-scan.")
            return

        from ..services.ingest import ingest_channel

        channels = db.scalars(select(Channel)).all()
        total = 0
        for channel in channels:
            total += len(await ingest_channel(db, channel))
        await _notify(chat_id, f"🔄 Re-scanned {len(channels)} channel(s), {total} new post(s).")
    finally:
        db.close()


async def _send_set_rss(chat_id: int, text: str, user_id: int | None) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await _notify(chat_id, "Usage: <code>/rss https://example.com/feed.xml</code>")
        return
    url = parts[1].strip()
    if not url.startswith(("http://", "https://")):
        await _notify(chat_id, "That doesn't look like a valid feed URL.")
        return

    db = session_scope()
    try:
        if not _is_admin(db, user_id):
            await _notify(chat_id, "🔒 Only channel owners and admins can set a feed.")
            return
        channel = db.scalar(
            select(Channel).where(Channel.owner_user_id == user_id).order_by(Channel.id.asc())
        )
        if channel is None:
            await _notify(chat_id, "No channel is linked to your account yet.")
            return
        entries = await rss.fetch_feed(url)
        if not entries:
            await _notify(chat_id, "⚠️ Could not read any entries from that feed URL.")
            return
        channel.rss_url = url
        db.commit()
        from ..services.ingest import ingest_channel

        new_posts = await ingest_channel(db, channel)
        await _notify(
            chat_id,
            f"✅ Feed set for <b>{channel.title}</b>.\n"
            f"Found {len(entries)} entries, indexed {len(new_posts)} new post(s).",
        )
    finally:
        db.close()


async def _send_set_kind(chat_id: int, text: str, user_id: int | None, kind: str) -> None:
    """Switch an owner's channel between 'feed' and 'index' handling."""
    db = session_scope()
    try:
        if not _is_admin(db, user_id):
            await _notify(chat_id, "🔒 Only channel owners and admins can change the mode.")
            return
        channel = _owner_channel(db, user_id, text)
        if channel is None:
            await _notify(chat_id, "No channel is linked to your account yet.")
            return
        channel.kind = kind
        db.commit()
        label = "name+link index" if kind == "index" else "release feed"
        note = f"✅ <b>{channel.title}</b> is now treated as a {label}."
        if kind == "index":
            note += (
                "\nNew posts will be read as lists of <i>name + link</i> pairs and "
                "each pair becomes its own entry."
            )
        if channel.rss_url:
            from ..services.ingest import ingest_channel

            new_posts = await ingest_channel(db, channel)
            note += f"\nRe-scanned its feed: {len(new_posts)} new entry(ies)."
        await _notify(chat_id, note)
    finally:
        db.close()


async def _send_import(chat_id: int, text: str, user_id: int | None) -> None:
    """Index a pasted list of name+link lines into the owner's channel."""
    import time

    body = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
    entries_found = index_parser.parse_entries(body)
    if not entries_found:
        await _notify(
            chat_id,
            "Usage: send <code>/import</code> followed by lines of "
            "<code>Name | https://t.me/...</code> or a list copied from your channel.",
        )
        return

    db = session_scope()
    try:
        if not _is_admin(db, user_id):
            await _notify(chat_id, "🔒 Only channel owners and admins can import entries.")
            return
        channel = _owner_channel(db, user_id, text)
        if channel is None:
            await _notify(chat_id, "No channel is linked to your account yet.")
            return
        channel.kind = "index"
        entry = {
            "title": "",
            "summary": body,
            "entities": [],
            "message_id": -int(time.time()),
            "link": None,
            "published_at": None,
        }
        new_posts = await ingest_entries(db, channel, [entry])
        await _notify(
            chat_id,
            f"✅ Imported {len(new_posts)} of {len(entries_found)} entr(y/ies) into "
            f"<b>{channel.title}</b>.",
        )
    finally:
        db.close()


def _owner_channel(db: Session, user_id: int | None, text: str) -> Channel | None:
    """Find the owner's channel, honouring an optional @username/chat id argument."""
    parts = text.split()
    if len(parts) > 1 and (parts[1].startswith("@") or parts[1].lstrip("-").isdigit()):
        ref = parts[1].lstrip("@").lower()
        for channel in db.scalars(select(Channel).where(Channel.owner_user_id == user_id)):
            if ref == (channel.username or "").lower() or ref == str(channel.chat_id):
                return channel
    return db.scalar(
        select(Channel).where(Channel.owner_user_id == user_id).order_by(Channel.id.asc())
    )


async def _notify(chat_id: int, text: str, reply_markup=None) -> None:
    try:
        tg = TelegramClient()
        await tg.send_message(chat_id, text, reply_markup=reply_markup)
    except (RuntimeError, TelegramError) as exc:
        log.info("Notify failed: %s", exc)
