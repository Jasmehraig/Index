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
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'index.db'}"
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
    # feeds. Comma-separated chat ids or @usernames.
    index_channels: str = ""

    @property
    def index_channel_refs(self) -> set[str]:
        return {part.strip().lower() for part in self.index_channels.split(",") if part.strip()}

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
