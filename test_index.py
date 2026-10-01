"""Integration tests for Index.

These exercise the real ingestion -> DB -> API path. Only the external network
boundary (Jikan) is stubbed, using a real captured Jikan response fixture.
Feeds are served by a real local HTTP server so the RSS fetch path is genuine.
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models  # noqa: E402
from app.models import Anime, Channel, Post, session_scope  # noqa: E402
from app.services import anilist, index_parser, ingest, jikan, metadata, rss  # noqa: E402
from app.services.jikan import clean_title, extract_hints  # noqa: E402

FIXTURE = json.loads(Path(__file__).with_name("anilist_frieren.json").read_text())["data"]["Media"]
FRIEREN_META = anilist.normalize(FIXTURE)


class _FeedServer:
    """Serves in-memory RSS documents over HTTP on an ephemeral port."""

    def __init__(self):
        self.feeds: dict[str, bytes] = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = server.feeds.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def add(self, path: str, xml: str) -> str:
        self.feeds[path] = xml.encode()
        return self.base + path

    def stop(self):
        self.httpd.shutdown()


@pytest.fixture(scope="module")
def feeds():
    server = _FeedServer()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _no_app_db_rebind(monkeypatch):
    """App startup must not rebind the DB to the developer's real DATABASE_URL."""
    monkeypatch.setattr("app.main.init_db", lambda *a, **k: None)


@pytest.fixture()
def db(tmp_path):
    models.init_db(f"sqlite:///{tmp_path / 'test.db'}")
    session = session_scope()
    yield session
    session.close()


# ----------------------------- title parsing -----------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("[SubsPlease] Frieren: Beyond Journeys End - 05 (1080p) [ABC].mkv", "Frieren: Beyond Journeys End"),
        ("One Piece Episode 1085 Sub English", "One Piece"),
        ("【Anime】Jujutsu Kaisen Season 2 Episode 12", "Jujutsu Kaisen"),
        ("Attack on Titan S04E28 [Dual Audio] 1080p", "Attack on Titan"),
    ],
)
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


def test_extract_hints_sxxexx():
    assert extract_hints("Attack on Titan S04E28") == ("Episode 28", "Season 04")


def test_extract_hints_episode():
    assert extract_hints("One Piece Episode 1085") == ("Episode 1085", None)


def test_normalize_real_payload():
    assert FRIEREN_META["mal_id"] == 52991
    assert FRIEREN_META["source"] == "anilist"
    assert FRIEREN_META["title"] == "Sousou no Frieren"
    assert FRIEREN_META["title_english"].startswith("Frieren: Beyond Journey")
    assert FRIEREN_META["poster_url"].startswith("http")
    assert FRIEREN_META["banner_url"].startswith("http")
    assert FRIEREN_META["score"] == 9.1
    assert "Adventure" in FRIEREN_META["genres"]


def test_metadata_falls_back_to_jikan(monkeypatch):
    async def anilist_miss(_title):
        return None

    async def jikan_hit(_title):
        return {"source": "jikan", "mal_id": 1, "title": "Fallback"}

    monkeypatch.setattr(anilist, "search_anime", anilist_miss)
    monkeypatch.setattr(jikan, "search_anime", jikan_hit)
    result = asyncio.run(metadata.search_anime("Some Show - 01"))
    assert result["source"] == "jikan"


def test_metadata_prefers_anilist(monkeypatch):
    async def anilist_hit(_title):
        return {"source": "anilist", "title": "Primary"}

    async def jikan_should_not_run(_title):  # pragma: no cover - guard
        raise AssertionError("Jikan should not be called when AniList succeeds")

    monkeypatch.setattr(anilist, "search_anime", anilist_hit)
    monkeypatch.setattr(jikan, "search_anime", jikan_should_not_run)
    assert asyncio.run(metadata.search_anime("Show"))["source"] == "anilist"


# ----------------------------- RSS parsing -----------------------------
def test_rss_parses_entries(feeds):
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Show - 01</title><link>https://t.me/chan/42</link></item>
    </channel></rss>"""
    url = feeds.add("/basic.xml", xml)
    entries = asyncio.run(rss.fetch_feed(url))
    assert len(entries) == 1
    assert entries[0]["message_id"] == 42
    assert entries[0]["title"] == "Show - 01"


def test_rss_handles_bad_url():
    assert asyncio.run(rss.fetch_feed("http://127.0.0.1:1/nope.xml")) == []


def test_message_id_from_link():
    assert rss.message_id_from_link("https://t.me/chan/123?single") == 123
    assert rss.message_id_from_link("https://t.me/chan") is None


# ----------------------------- ingestion -----------------------------
def test_ingest_channel_indexes_and_enriches(db, monkeypatch, feeds):
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item>
      <title>[SubsPlease] Frieren: Beyond Journeys End - 05 (1080p).mkv</title>
      <link>https://t.me/animetest/101</link>
      <pubDate>Mon, 30 Sep 2024 10:00:00 GMT</pubDate>
    </item>
    <item><title>Frieren - 06</title><link>https://t.me/animetest/102</link></item>
    </channel></rss>"""
    url = feeds.add("/frieren.xml", xml)

    async def fake_search(title, *a, **k):
        return FRIEREN_META if "frieren" in title.lower() else None

    monkeypatch.setattr(metadata, "search_anime", fake_search)

    channel = Channel(
        chat_id=-100123, username="animetest", title="Anime Test", rss_url=url
    )
    db.add(channel)
    db.commit()

    posts = asyncio.run(ingest.ingest_channel(db, channel))
    assert len(posts) == 2
    assert all(p.anime is not None for p in posts)
    assert posts[0].anime.mal_id == 52991
    assert posts[0].anime.poster_url.startswith("http")
    assert posts[0].episode_hint == "Episode 05"

    # Re-ingesting the same feed is idempotent.
    again = asyncio.run(ingest.ingest_channel(db, channel))
    assert again == []
    assert db.query(Post).count() == 2
    assert db.query(Anime).count() == 1


def test_ingest_without_metadata_still_indexes(db, monkeypatch, feeds):
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Obscure OVA 01</title><link>https://t.me/chan/7</link></item>
    </channel></rss>"""
    url = feeds.add("/obscure.xml", xml)

    async def no_match(title, *a, **k):
        return None

    monkeypatch.setattr(metadata, "search_anime", no_match)

    channel = Channel(chat_id=-1, username="chan", title="C", rss_url=url)
    db.add(channel)
    db.commit()

    posts = asyncio.run(ingest.ingest_channel(db, channel))
    assert len(posts) == 1
    assert posts[0].anime is not None  # fallback row created with the cleaned title
    assert posts[0].anime.mal_id is None
    assert posts[0].anime.title == "Obscure OVA 01"
    assert posts[0].telegram_link == "https://t.me/chan/7"


# ----------------------------- telegram links -----------------------------
def test_telegram_link_private_channel(db):
    channel = Channel(chat_id=-100999, title="Private", invite_link="https://t.me/+abc")
    db.add(channel)
    db.commit()
    post = Post(channel_id=channel.id, message_id=55)
    db.add(post)
    db.commit()
    assert post.telegram_link == "https://t.me/c/-100999/55"
    assert channel.link == "https://t.me/+abc"


# ----------------------------- admin gating -----------------------------
def test_is_admin_recognizes_channel_owner(db, monkeypatch):
    from app.routers import webhook

    db.add(Channel(chat_id=-1001, title="Owned", owner_user_id=777))
    db.commit()
    assert webhook._is_admin(db, 777) is True
    assert webhook._is_admin(db, 888) is False
    assert webhook._is_admin(db, None) is False


def test_is_admin_honors_configured_ids(db, monkeypatch):
    from app.config import get_settings
    from app.routers import webhook

    monkeypatch.setattr(get_settings(), "admin_user_ids", "12345, 67890")
    assert webhook._is_admin(db, 12345) is True
    assert webhook._is_admin(db, 67890) is True
    assert webhook._is_admin(db, 1) is False


def test_webhook_rejects_bad_secret():
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    settings = get_settings()
    original = settings.webhook_secret
    settings.webhook_secret = "s3cret"
    try:
        client = TestClient(app)
        bad = client.post("/telegram/webhook", json={"message": {}})
        good = client.post(
            "/telegram/webhook",
            json={"message": {}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        )
        assert bad.status_code == 403
        assert good.status_code == 200 and good.json() == {"ok": True}
    finally:
        settings.webhook_secret = original


def test_blank_env_int_is_unset():
    """An empty ANNOUNCE_CHAT_ID env var must not raise a validation error."""
    from app.config import Settings

    settings = Settings(_env_file=None, announce_chat_id="")
    assert settings.announce_chat_id is None
    settings = Settings(_env_file=None, announce_chat_id="-1001234567890")
    assert settings.announce_chat_id == -1001234567890


def test_webhook_registers_channel_and_indexes_post(db, monkeypatch):
    """Simulate Telegram delivering a bot-add and a channel post."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import webhook

    async def no_feed(_url):
        return []

    async def fake_search(_title, *a, **k):
        return FRIEREN_META

    monkeypatch.setattr(webhook.rss, "fetch_feed", no_feed)
    monkeypatch.setattr(metadata, "search_anime", fake_search)

    client = TestClient(app)

    membership = {
        "my_chat_member": {
            "chat": {"id": -100555, "type": "channel", "title": "New Chan", "username": "newchan"},
            "from": {"id": 4242},
            "new_chat_member": {"status": "administrator"},
        }
    }
    assert client.post("/telegram/webhook", json=membership).json() == {"ok": True}

    channel = db.scalar(select(Channel).where(Channel.chat_id == -100555))
    assert channel is not None and channel.owner_user_id == 4242

    post_update = {
        "channel_post": {
            "message_id": 900,
            "chat": {"id": -100555, "type": "channel", "title": "New Chan"},
            "caption": "[SubsPlease] Frieren - 09 (1080p).mkv",
        }
    }
    assert client.post("/telegram/webhook", json=post_update).json() == {"ok": True}

    db.expire_all()
    post = db.scalar(select(Post).where(Post.message_id == 900))
    assert post is not None
    assert post.anime.title == "Sousou no Frieren"
    assert post.episode_hint == "Episode 09"
    assert post.telegram_link == "https://t.me/newchan/900"


# ----------------------------- index-channel parsing -----------------------------
def test_parse_text_link_entities():
    """The 'create link' feature: a name label carrying a text_link entity."""
    text = "Haikyuu\nAttack on Titan"
    entities = [
        {"type": "text_link", "offset": 0, "length": 7, "url": "https://t.me/animefiles/1"},
        {"type": "text_link", "offset": 8, "length": 15, "url": "https://t.me/animefiles/2"},
    ]
    entries = index_parser.parse_entries(text, entities)
    assert [e["title"] for e in entries] == ["Haikyuu", "Attack on Titan"]
    assert entries[0]["url"] == "https://t.me/animefiles/1"
    assert [e["index"] for e in entries] == [0, 1]


def test_parse_html_and_markdown_anchors():
    html = (
        "<b>Index</b><br>"
        '<a href="https://t.me/chan/11">Haikyuu</a><br>'
        "[Chainsaw Man](https://t.me/chan/12)"
    )
    entries = index_parser.parse_entries(html)
    assert {e["title"] for e in entries} == {"Haikyuu", "Chainsaw Man"}
    assert all(e["is_telegram"] for e in entries)


def test_parse_plain_name_then_link_lines():
    text = "One Piece\nhttps://t.me/chan/21\n\nJujutsu Kaisen - https://t.me/chan/22"
    entries = index_parser.parse_entries(text)
    assert entries[0]["title"] == "One Piece"
    assert entries[0]["url"] == "https://t.me/chan/21"
    assert entries[1]["title"] == "Jujutsu Kaisen"
    assert entries[1]["url"] == "https://t.me/chan/22"


def test_parse_deduplicates_and_ignores_non_links():
    text = "Haikyuu | https://t.me/chan/1\nHaikyuu | https://t.me/chan/1\nNo link here"
    entries = index_parser.parse_entries(text)
    assert len(entries) == 1
    assert entries[0]["url"] == "https://t.me/chan/1"


def test_looks_like_index_requires_two_pairs():
    one = "Haikyuu https://t.me/chan/1"
    many = "Haikyuu https://t.me/chan/1\nBleach https://t.me/chan/2"
    assert index_parser.looks_like_index(one) is False
    assert index_parser.looks_like_index(many) is True


def test_ingest_index_channel_creates_one_post_per_pair(db, monkeypatch):
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item>
      <title>Anime Index</title>
      <link>https://t.me/indexchan/50</link>
      <description><![CDATA[
        <a href="https://t.me/files/1">Haikyuu</a><br>
        <a href="https://t.me/files/2">Bleach</a>
      ]]></description>
    </item>
    </channel></rss>"""
    server = _FeedServer()
    try:
        feed_url = server.add("/index.xml", xml)

        async def no_match(title, *a, **k):
            return None

        monkeypatch.setattr(metadata, "search_anime", no_match)

        channel = Channel(
            chat_id=-100777, username="indexchan", title="Anime Index",
            kind="index", rss_url=feed_url,
        )
        db.add(channel)
        db.commit()

        posts = asyncio.run(ingest.ingest_channel(db, channel))
        assert len(posts) == 2
        assert {p.caption for p in posts} == {"Haikyuu", "Bleach"}
        assert {p.url for p in posts} == {"https://t.me/files/1", "https://t.me/files/2"}
        assert {p.entry_index for p in posts} == {0, 1}
        # Tapping an entry goes to its own link, not the channel post.
        assert posts[0].target_link == posts[0].url

        again = asyncio.run(ingest.ingest_channel(db, channel))
        assert again == []
        assert db.query(Post).count() == 2
    finally:
        server.stop()


def test_webhook_index_channel_post_from_entities(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    async def no_match(title, *a, **k):
        return None

    monkeypatch.setattr(metadata, "search_anime", no_match)

    channel = Channel(chat_id=-100888, username="curated", title="Curated", kind="index")
    db.add(channel)
    db.commit()

    update = {
        "channel_post": {
            "message_id": 700,
            "chat": {"id": -100888, "type": "channel", "title": "Curated"},
            "caption": "Haikyuu\nChainsaw Man",
            "caption_entities": [
                {"type": "text_link", "offset": 0, "length": 7, "url": "https://t.me/files/aa"},
                {"type": "text_link", "offset": 8, "length": 12, "url": "https://t.me/files/bb"},
            ],
        }
    }
    with TestClient(app) as client:
        assert client.post("/telegram/webhook", json=update).json() == {"ok": True}

    db.expire_all()
    posts = db.scalars(select(Post).where(Post.channel_id == channel.id)).all()
    assert {p.caption for p in posts} == {"Haikyuu", "Chainsaw Man"}
    assert {p.url for p in posts} == {"https://t.me/files/aa", "https://t.me/files/bb"}


def test_webhook_auto_detects_index_channel(db, monkeypatch):
    """A feed channel that posts a name+link list flips to index mode."""
    from fastapi.testclient import TestClient

    from app.main import app

    async def no_match(title, *a, **k):
        return None

    monkeypatch.setattr(metadata, "search_anime", no_match)

    channel = Channel(chat_id=-100999, username="autodet", title="Auto", kind="feed")
    db.add(channel)
    db.commit()

    update = {
        "channel_post": {
            "message_id": 800,
            "chat": {"id": -100999, "type": "channel", "title": "Auto"},
            "caption": "Haikyuu https://t.me/files/x\nBleach https://t.me/files/y",
        }
    }
    with TestClient(app) as client:
        assert client.post("/telegram/webhook", json=update).json() == {"ok": True}

    db.expire_all()
    assert db.get(Channel, channel.id).kind == "index"
    posts = db.scalars(select(Post).where(Post.channel_id == channel.id)).all()
    assert {p.caption for p in posts} == {"Haikyuu", "Bleach"}


def test_api_exposes_direct_links_and_channel_kind(db):
    from fastapi.testclient import TestClient

    from app.main import app

    channel = Channel(chat_id=-101000, username="apidir", title="API Dir", kind="index")
    db.add(channel)
    db.commit()
    anime = Anime(title="Haikyuu")
    db.add(anime)
    db.commit()
    post = Post(
        channel_id=channel.id, anime_id=anime.id, message_id=60,
        entry_index=0, url="https://t.me/files/direct", caption="Haikyuu",
    )
    db.add(post)
    db.commit()

    with TestClient(app) as client:
        detail = client.get(f"/api/anime/{anime.id}").json()
        assert detail["posts"][0]["link"] == "https://t.me/files/direct"
        assert detail["posts"][0]["is_direct_link"] is True
        channels = client.get("/api/channels").json()["items"]
        assert channels[0]["kind"] == "index"

