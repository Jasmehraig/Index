"""Thin async wrapper around the Telegram Bot API."""
from __future__ import annotations

import logging

import httpx

from ..config import get_settings

log = logging.getLogger("index.telegram")

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class TelegramClient:
    def __init__(self, token: str | None = None):
        self.token = token or get_settings().bot_token
        if not self.token:
            raise RuntimeError("BOT_TOKEN is not configured")
        self._base = f"https://api.telegram.org/bot{self.token}"
        self._file_base = f"https://api.telegram.org/file/bot{self.token}"

    async def _call(self, method: str, **params):
        payload = {k: v for k, v in params.items() if v is not None}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{self._base}/{method}", json=payload)
        data = resp.json()
        if not data.get("ok"):
            log.warning("Telegram %s failed: %s", method, data.get("description"))
            raise TelegramError(data.get("description", "unknown error"), data.get("error_code"))
        return data["result"]

    async def get_me(self):
        return await self._call("getMe")

    async def set_webhook(self, url: str, secret_token: str = ""):
        return await self._call(
            "setWebhook",
            url=url,
            secret_token=secret_token or None,
            allowed_updates=["message", "channel_post", "my_chat_member"],
            drop_pending_updates=True,
        )

    async def delete_webhook(self):
        return await self._call("deleteWebhook", drop_pending_updates=True)

    async def set_chat_menu_button(self, chat_id: int | None = None, url: str | None = None):
        menu_button = {"type": "web_app", "text": "Open Index", "web_app": {"url": url}} if url else {"type": "default"}
        return await self._call("setChatMenuButton", chat_id=chat_id, menu_button=menu_button)

    async def set_my_commands(self, commands: list[dict]) -> None:
        """Publish the slash-command menu shown in Telegram clients."""
        try:
            await self._call("setMyCommands", commands=commands)
        except TelegramError as exc:
            log.warning("Could not set bot commands: %s", exc)

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML", disable_web_page_preview=False):
        return await self._call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
        return await self._call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    async def answer_callback_query(self, callback_query_id: str, text: str = "", show_alert: bool = False):
        return await self._call(
            "answerCallbackQuery",
            callback_query_id=callback_query_id,
            text=text or None,
            show_alert=show_alert,
        )

    async def get_chat(self, chat_id):
        return await self._call("getChat", chat_id=chat_id)

    async def get_chat_member(self, chat_id, user_id):
        return await self._call("getChatMember", chat_id=chat_id, user_id=user_id)

    async def get_chat_administrators(self, chat_id):
        return await self._call("getChatAdministrators", chat_id=chat_id)

    async def export_chat_invite_link(self, chat_id):
        return await self._call("exportChatInviteLink", chat_id=chat_id)


class TelegramError(Exception):
    def __init__(self, description: str, code: int | None = None):
        super().__init__(description)
        self.description = description
        self.code = code
