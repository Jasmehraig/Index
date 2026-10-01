# AGENTS.md

Persistent notes for agents working in this repository.

## What this is

Telegram bot + Telegram Mini App that indexes anime posted in Telegram channels.
FastAPI backend, vanilla-JS Mini App, SQLite storage. Licensed GPL-3.0.

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -q          # 29 tests, ~6s
uvicorn app.main:app --port 8000    # serve API + Mini App
```

Run the dev server on an allowed host/port (e.g. `--port 12000`) when working
inside the Agent Canvas environment.

## Conventions

- `from __future__ import annotations` at the top of every module.
- DB access: use the `session_scope()` helper for ad-hoc sessions and the
  `get_session` FastAPI dependency for request handlers. Never call
  `get_session().__next__()` — it leaks the generator.
- All SQLAlchemy models live in `app/models.py`; schema is created with
  `Base.metadata.create_all`, so new columns need a manual `ALTER TABLE` on
  existing databases (there is no migration tool). `init_db` calls `_migrate`,
  which adds the post-`kind`/`url`/`entry_index` columns to older SQLite files.
- Channels have a `kind`: `"feed"` (one anime per post) or `"index"` (posts are
  lists of name+link pairs). Ingestion branches on it in `ingest.ingest_entries`.
  Index entries carry their own `Post.url`; always link via `post.target_link`,
  never `post.telegram_link`, so curated links win over the channel post.
- External HTTP calls go through `app/services/*` and must degrade gracefully
  when an upstream API is down (return `None`/`[]`, never raise into a webhook).
- The Telegram webhook must always return `{"ok": true}`; wrap handlers in
  try/except and log instead of propagating.

## Gotchas

- AniList is the primary metadata provider, Jikan the fallback. AniList's
  single-`Media` query 404s on unmatched titles — use `Page.media` so it returns
  an empty list instead.
- Title matching uses progressively looser candidate strings; keep the
  `_TRAILING_NUM` regex requiring a real separator so `Mob Psycho 100` is not
  truncated.
- Public RSSHub instances are heavily rate limited. `RSS_PROVIDERS` is
  configurable; recommend self-hosting for real use.
- The Mini App is only opened by Telegram over HTTPS; local dev uses the
  API-only mode when `BOT_TOKEN` is unset.

## Testing

`tests/test_index.py` is an integration suite. Feeds are served by a real local
HTTP server; only the external metadata provider is stubbed using the captured
AniList fixture `tests/anilist_frieren.json`. Keep tests on real code paths.
