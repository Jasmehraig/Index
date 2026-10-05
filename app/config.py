import re
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str = ""
    public_base_url: str = "http://localhost:8000"
    webhook_secret: str = ""
    # Set DATABASE_URL in the environment (Render/Neon). Falls back to a local
    # SQLite file for development only.
    DATABASE_URL: str = "sqlite:///./data/index.db"
    poll_interval_minutes: int = 15
    jikan_base_url: str = "https://api.jikan.moe/v4"
    anilist_url: str = "https://graphql.anilist.co"
    admin_token: str = ""
    # Comma-separated Telegram user ids allowed to run /refresh. Channel owners
    # (whoever added the bot) are always allowed.
    admin_user_ids: str = ""
    # RSS bridge templates tried when discovering a channel feed. {username} and
    # {chat_id} are substituted. Public RSSHub instances are rate limited.
    rss_providers: str = (
        "https://rsshub.app/telegram/channel/{username},"
        "https://rsshub.rssforever.com/telegram/channel/{username}"
    )
    # Optional channel where the bot announces newly indexed anime.
    announce_chat_id: int | None = None
    # Channels whose posts are curated "name + link" lists rather than release
    # feeds. Comma-separated chat ids or @usernames. The catalog is seeded from
    # these channels' public web previews.
    index_channels: str = "https://t.me/Anime_Index_swordsmith"
    # File-share bot username used to resolve file links, if any.
    file_share_bot: str = ""

    @staticmethod
    def _clean_ref(part: str) -> str:
        """'https://t.me/Foo', '@Foo' and 'Foo' all become 'Foo'."""
        part = re.sub(r"^https?://(?:t|telegram)\.me/(?:s/)?", "", part.strip(), flags=re.I)
        return part.strip("/@ ")

    @property
    def index_channel_refs(self) -> set[str]:
        return {
            ref.lower()
            for ref in (self._clean_ref(p) for p in self.index_channels.split(","))
            if ref
        }

    @property
    def index_channel_usernames(self) -> list[str]:
        """Public channel usernames we can read via t.me/s (original casing kept).

        The preview page tags posts as ``data-post="Name/123"`` with the channel's
        real casing, so a lower-cased name would stop paging after the first page.
        """
        names: dict[str, str] = {}
        for part in self.index_channels.split(","):
            ref = self._clean_ref(part)
            if ref and not ref.lstrip("-").isdigit():
                names.setdefault(ref.lower(), ref)
        return sorted(names.values(), key=str.lower)

    @field_validator("announce_chat_id", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        """Treat an empty env var as unset rather than a parse error."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @property
    def bot_api(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    @property
    def admin_ids(self) -> set[int]:
        ids: set[int] = set()
        for part in self.admin_user_ids.split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                ids.add(int(part))
        return ids

    @property
    def webhook_url(self) -> str:
        base = self.public_base_url.rstrip("/")
        return f"{base}/telegram/webhook"


@lru_cache
def get_settings() -> Settings:
    return Settings()
