"""Index — a Telegram Mini App that indexes anime channels."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .models import init_db
from app.routers import api, webhook
from .services.scheduler import start_scheduler, stop_scheduler
from .services.telegram import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("index")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()

    if settings.bot_token:
        try:
            tg = TelegramClient()
            me = await tg.get_me()
            log.info("Bot online: @%s", me.get("username"))
            if settings.public_base_url.startswith("https://"):
                await tg.set_webhook(settings.webhook_url, settings.webhook_secret)
                await tg.set_chat_menu_button(url=settings.public_base_url.rstrip("/") + "/")
                log.info("Webhook set to %s", settings.webhook_url)
            else:
                log.warning("PUBLIC_BASE_URL is not HTTPS; skipping webhook registration")
            await tg.set_my_commands(
                [
                    {"command": "start", "description": "Open the anime index"},
                    {"command": "catalog", "description": "Build catalog from an index channel"},
                    {"command": "episodes", "description": "Add seasons and episodes to a title"},
                    {"command": "channels", "description": "List indexed channels"},
                    {"command": "quality", "description": "Add a download link to a title"},
                    {"command": "refresh", "description": "Re-scan channel feeds"},
                    {"command": "help", "description": "How Index works"},
                ]
            )
        except Exception as exc:  # noqa: BLE001 - app should still serve the mini app
            log.warning("Telegram setup failed: %s", exc)
    else:
        log.warning("BOT_TOKEN not set; running in API-only mode")

    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title="Index Mini App", version="1.0.0", lifespan=lifespan)
app.include_router(api.router)
app.include_router(webhook.router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index_page():
    return FileResponse(str(STATIC_DIR / "index.html"))
