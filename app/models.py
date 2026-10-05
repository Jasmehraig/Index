from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import get_settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Channel(Base):
    """A Telegram channel the bot has been added to and that it indexes."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    # "feed" = a channel whose posts are release files (default).
    # "index" = a curated channel whose posts list anime names + links.
    kind: Mapped[str] = mapped_column(String(16), default="feed")
    rss_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    posts: Mapped[list["Post"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )

    @property
    def link(self) -> str:
        if self.username:
            return f"https://t.me/{self.username}"
        return self.invite_link or f"https://t.me/c/{self.chat_id}"


class Anime(Base):
    """An anime entry, enriched from the Jikan (MyAnimeList) API."""

    __tablename__ = "anime"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mal_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(512), index=True)
    title_english: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title_japanese: Mapped[str | None] = mapped_column(String(512), nullable=True)
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    poster_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    banner_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    genres: Mapped[str | None] = mapped_column(String(512), nullable=True)
    studio: Mapped[str | None] = mapped_column(String(256), nullable=True)
    episodes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    posts: Mapped[list["Post"]] = relationship(back_populates="anime")


class Post(Base):
    """A single channel post that has been matched to an anime."""

    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("channel_id", "message_id", "entry_index", name="uq_post_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    anime_id: Mapped[int | None] = mapped_column(ForeignKey("anime.id"), nullable=True)
    message_id: Mapped[int] = mapped_column(Integer)
    # For index channels: the outbound link the post points at, and the ordinal
    # of this entry within its post.
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    entry_index: Mapped[int] = mapped_column(Integer, default=0)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    episode_hint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    channel: Mapped["Channel"] = relationship(back_populates="posts")
    anime: Mapped["Anime | None"] = relationship(back_populates="posts")

    @property
    def telegram_link(self) -> str:
        if self.channel.username:
            return f"https://t.me/{self.channel.username}/{self.message_id}"
        return f"https://t.me/c/{self.channel.chat_id}/{self.message_id}"

    @property
    def target_link(self) -> str:
        """Where a tap should send the user: the entry link when present."""
        return self.url or self.telegram_link


class AnimeEntry(Base):
    """One anime in the curated catalog, sourced from an index channel.

    This is the canonical "what the Mini App shows" row. It is seeded from the
    name + link pairs an index channel publishes, then enriched with AniList
    metadata. ``channel_link`` is the destination users tap to reach the anime;
    it may be a public channel, a private invite, or a file-share bot deep link.
    """

    __tablename__ = "anime_entries"
    __table_args__ = (
        UniqueConstraint("source_chat", "entry_index", name="uq_entry_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anime_id: Mapped[int | None] = mapped_column(ForeignKey("anime.id"), nullable=True)
    # Where the row came from, e.g. "Anime_Index_swordsmith" and its ordinal.
    source_chat: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    entry_index: Mapped[int] = mapped_column(Integer, default=0)
    raw_name: Mapped[str] = mapped_column(String(512), index=True)
    # Loose comparison key so the same anime from two index channels merges.
    match_key: Mapped[str | None] = mapped_column(String(256), index=True)
    channel_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    channel_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Manual release details, set from the bot's episode message. They apply to
    # every post for this title, so they live on the entry, not on a season.
    audio: Mapped[str | None] = mapped_column(String(256), nullable=True)
    subtitles: Mapped[str | None] = mapped_column(String(256), nullable=True)
    quality_tags: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    anime: Mapped["Anime | None"] = relationship()
    channels: Mapped[list["EntryChannel"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )
    qualities: Mapped[list["QualityLink"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )
    seasons: Mapped[list["Season"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="Season.number"
    )


class Season(Base):
    """One season of a catalog entry, with its own poster and release details.

    A title with several seasons shows one poster per season; each season carries
    its own episodes, audio, subtitles and quality tags.
    """

    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("entry_id", "number", name="uq_season_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("anime_entries.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    poster_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio: Mapped[str | None] = mapped_column(String(256), nullable=True)
    subtitles: Mapped[str | None] = mapped_column(String(256), nullable=True)
    quality_tags: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    entry: Mapped["AnimeEntry"] = relationship(back_populates="seasons")
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="season", cascade="all, delete-orphan", order_by="Episode.number"
    )


class Episode(Base):
    """One episode of a season, pointing at the bot deep link that serves it."""

    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("season_id", "number", name="uq_episode_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    url: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    season: Mapped["Season"] = relationship(back_populates="episodes")


class EntryChannel(Base):
    """One index channel that lists an anime, and the link it points at.

    An anime can appear in several index channels (yours and a friend's), so the
    links live here rather than on the entry: the detail view offers one button
    per channel instead of a single destination.
    """

    __tablename__ = "entry_channels"
    __table_args__ = (
        UniqueConstraint("entry_id", "source_chat", name="uq_entry_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("anime_entries.id", ondelete="CASCADE"), index=True
    )
    source_chat: Mapped[str] = mapped_column(String(128), index=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    entry: Mapped["AnimeEntry"] = relationship(back_populates="channels")


class QualityLink(Base):
    """A quality-specific download link for a catalog entry.

    ``url`` is either a direct link or, when ``via_bot`` is set, a file-share
    bot deep link that resolves to one or many files.
    """

    __tablename__ = "quality_links"
    __table_args__ = (
        UniqueConstraint("entry_id", "quality", "url", name="uq_quality_link"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("anime_entries.id", ondelete="CASCADE"), index=True
    )
    quality: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(String(1024))
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    via_bot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    batch: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    entry: Mapped["AnimeEntry"] = relationship(back_populates="qualities")


_engine = None
_SessionLocal = None


def _as_psycopg_url(url: str) -> str:
    """Pin the psycopg3 driver; SQLAlchemy defaults a bare URL to psycopg2."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def init_db(database_url: str | None = None):
    global _engine, _SessionLocal
    url = database_url or get_settings().DATABASE_URL
    if url.startswith("sqlite"):
        if url.startswith("sqlite:///"):
            path = url.replace("sqlite:///", "", 1)
            if path and path != ":memory:":
                from pathlib import Path

                Path(path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, future=True)
        from sqlalchemy import event

        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            # WAL lets the scheduler read while an enrichment pass writes.
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()
    else:
        _engine = create_engine(
            _as_psycopg_url(url),
            future=True,
            # Serverless Postgres (Neon, Supabase) drops idle connections when it
            # suspends, so verify each checkout and recycle inside its window.
            pool_pre_ping=True,
            pool_recycle=300,
        )

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    _migrate(_engine)
    return _engine


def _migrate(engine) -> None:
    """Add columns introduced after a database was first created.

    ``create_all`` only creates missing tables, so pre-existing SQLite files
    need the new columns added explicitly. Safe to run on every startup.
    """
    if engine.dialect.name != "sqlite":
        return
    wanted = {
        "channels": {"kind": "VARCHAR(16) DEFAULT 'feed'"},
        "posts": {
            "url": "VARCHAR(1024)",
            "entry_index": "INTEGER DEFAULT 0",
        },
        "anime_entries": {
            "match_key": "VARCHAR(256)",
            "audio": "VARCHAR(256)",
            "subtitles": "VARCHAR(256)",
            "quality_tags": "VARCHAR(256)",
            "description": "TEXT",
        },
        "anime": {"studio": "VARCHAR(256)"},
    }
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

    _rebuild_legacy_posts_unique(engine)


def _rebuild_legacy_posts_unique(engine) -> None:
    """Replace the old (channel_id, message_id) unique index.

    SQLite cannot drop a constraint declared inside ``CREATE TABLE``, so an
    older database must be rebuilt to allow several index entries per post.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        if not engine.dialect.has_table(conn, "posts"):
            return
        rows = conn.exec_driver_sql("PRAGMA index_list('posts')").fetchall()
        legacy = False
        for row in rows:
            if not row[2]:  # not unique
                continue
            cols = [
                r[2]
                for r in conn.exec_driver_sql(f"PRAGMA index_info('{row[1]}')").fetchall()
            ]
            if cols == ["channel_id", "message_id"]:
                legacy = True
                break
    if not legacy:
        return

    columns = (
        "id, channel_id, anime_id, message_id, url, entry_index, "
        "caption, episode_hint, posted_at, created_at"
    )
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE posts RENAME TO posts_legacy"))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(f"INSERT INTO posts ({columns}) SELECT {columns} FROM posts_legacy"))
        conn.execute(text("DROP TABLE posts_legacy"))


def get_session():
    if _SessionLocal is None:
        init_db()
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def session_scope():
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()
