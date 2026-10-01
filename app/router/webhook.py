"""Telegram webhook: bot commands, channel posts, and membership changes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Channel, Post, session_scope
from ..services import index_parser, rss
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
    if posts:
        db.refresh(channel)
    return posts


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
    if not text.startswith("/"):
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
        "/import – paste a name+link list to index it manually"
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
