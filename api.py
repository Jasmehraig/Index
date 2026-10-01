"""Public JSON API consumed by the Telegram Mini App."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..models import Anime, Channel, Post, get_session, session_scope
from ..services.ingest import ingest_channel
from ..services.webapp_auth import validate_init_data

router = APIRouter(prefix="/api", tags=["api"])


def _anime_card(anime: Anime, posts: list[Post]) -> dict:
    latest = posts[0] if posts else None
    return {
        "id": anime.id,
        "mal_id": anime.mal_id,
        "title": anime.title,
        "title_english": anime.title_english,
        "poster_url": anime.poster_url,
        "genres": anime.genres,
        "episodes": anime.episodes,
        "status": anime.status,
        "score": anime.score,
        "year": anime.year,
        "channel_count": len({p.channel_id for p in posts}),
        "post_count": len(posts),
        "latest_post": _post_card(latest) if latest else None,
    }


def _post_card(post: Post) -> dict:
    return {
        "id": post.id,
        "message_id": post.message_id,
        "caption": post.caption,
        "episode_hint": post.episode_hint,
        "posted_at": post.posted_at.isoformat() if post.posted_at else None,
        "link": post.target_link,
        "is_direct_link": bool(post.url),
        "channel": {
            "id": post.channel.id,
            "title": post.channel.title,
            "username": post.channel.username,
            "link": post.channel.link,
        },
    }


@router.get("/config")
def api_config():
    settings = get_settings()
    return {"app_name": "Index", "bot_username": None, "miniapp_url": settings.public_base_url}


@router.get("/home")
def home(
    q: str | None = Query(None, description="Search anime titles"),
    genre: str | None = None,
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
):
    stmt = select(Anime).order_by(Anime.title.asc())
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Anime.title.ilike(like),
                Anime.title_english.ilike(like),
                Anime.title_japanese.ilike(like),
            )
        )
    if genre:
        stmt = stmt.where(Anime.genres.ilike(f"%{genre}%"))
    anime_list = db.scalars(stmt.offset(offset).limit(limit)).all()

    ids = [a.id for a in anime_list]
    posts_by_anime: dict[int, list[Post]] = {i: [] for i in ids}
    if ids:
        posts = db.scalars(
            select(Post)
            .options(selectinload(Post.channel))
            .where(Post.anime_id.in_(ids))
            .order_by(Post.posted_at.desc().nullslast(), Post.id.desc())
        ).all()
        for post in posts:
            posts_by_anime.setdefault(post.anime_id, []).append(post)

    items = [_anime_card(a, posts_by_anime.get(a.id, [])) for a in anime_list]
    return {"items": items, "count": len(items), "offset": offset}


@router.get("/genres")
def genres(db: Session = Depends(get_session)):
    rows = db.scalars(select(Anime.genres).where(Anime.genres.is_not(None))).all()
    counts: dict[str, int] = {}
    for row in rows:
        for name in row.split(","):
            name = name.strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"items": [{"name": n, "count": c} for n, c in ordered]}


@router.get("/anime/{anime_id}")
def anime_detail(anime_id: int, db: Session = Depends(get_session)):
    anime = db.get(Anime, anime_id)
    if not anime:
        raise HTTPException(status_code=404, detail="Anime not found")
    posts = db.scalars(
        select(Post)
        .options(selectinload(Post.channel))
        .where(Post.anime_id == anime_id)
        .order_by(Post.posted_at.desc().nullslast(), Post.id.desc())
    ).all()
    card = _anime_card(anime, posts)
    card["synopsis"] = anime.synopsis
    card["banner_url"] = anime.banner_url
    card["posts"] = [_post_card(p) for p in posts]
    return card


@router.get("/channels")
def channels(db: Session = Depends(get_session)):
    rows = db.execute(
        select(Channel, func.count(Post.id))
        .outerjoin(Post, Post.channel_id == Channel.id)
        .group_by(Channel.id)
        .order_by(Channel.title.asc())
    ).all()
    return {
        "items": [
            {
                "id": ch.id,
                "title": ch.title,
                "username": ch.username,
                "link": ch.link,
                "rss_url": ch.rss_url,
                "kind": ch.kind,
                "post_count": count,
            }
            for ch, count in rows
        ]
    }


@router.post("/auth/validate")
def auth_validate(payload: dict):
    """Optional: verify Mini App initData server-side."""
    result = validate_init_data(payload.get("initData", ""))
    if not result:
        raise HTTPException(status_code=401, detail="Invalid initData")
    return {"ok": True, "user": result.get("user")}


@router.post("/admin/refresh")
async def admin_refresh(x_admin_token: str = Header(default="")):
    """Manually trigger a re-index of every channel."""
    settings = get_settings()
    if settings.admin_token and x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    db = session_scope()
    indexed = 0
    try:
        for channel in db.scalars(select(Channel)).all():
            posts = await ingest_channel(db, channel)
            indexed += len(posts)
    finally:
        db.close()
    return {"indexed": indexed}
