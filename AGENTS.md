# AGENTS.md

Persistent notes for agents working in this repository.

## What this is

Telegram bot + Telegram Mini App that indexes anime posted in Telegram channels.
FastAPI backend, vanilla-JS Mini App, SQLite storage. Licensed GPL-3.0.

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -q          # 59 tests, ~9s
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
  which adds the post-`kind`/`url`/`entry_index` columns and
  `anime_entries.match_key` to older SQLite files. New *tables* (e.g.
  `entry_channels`) are created by `create_all` and need no migration entry.
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
- SQLite takes a write lock as soon as a session has pending changes, so never
  hold one across an `await`. `catalog.enrich_pending` fetches metadata first,
  then writes in a second phase; `init_db` enables WAL so reads and writes can
  overlap. Do not reintroduce awaits between `db.add()` and `db.commit()`.
- Jikan's search can retry several times; a title with no match must not stall a
  batch. `enrich_pending` wraps each lookup in `asyncio.wait_for(..., 20)`.

## Catalog pipeline

The Mini App is driven by `AnimeEntry` rows, not `Post` rows:

- `services/telegram_web.py` scrapes a public index channel's `t.me/s/<user>`
  preview. It returns `(entries, details)`: numbered series-list pairs and the
  Season/Episodes/Audio/Genres detail cards. Channel posts use decorative
  small-caps (`ᴇ ᴘ ɪ ꜱ ᴏ ᴅ ᴇ ꜱ`), so field regexes must include those codepoints.
- `services/catalog.py` writes entries first (`import_entries`), merges detail
  cards (`apply_details`), and enriches metadata as a separate pass
  (`enrich_pending`) because AniList rate-limits at 0.7s/request.
- Database-channel files are matched to entries by filename via
  `catalog.match_entry` / `catalog.attach_file` (longest catalog name contained
  in the cleaned filename). Called from `webhook._attach_channel_file`.
- API: `/api/catalog`, `/api/entry/{id}`, `/api/entry/{id}/quality`,
  `/api/catalog/refresh`. The Mini App (`static/app.js`) renders cards from
  `/api/catalog` and the detail page from `/api/entry/{id}`.

## Multi-channel dedup and English titles

Several index channels can be indexed at once (`INDEX_CHANNELS` is a list, and
`/catalog @x` can be run repeatedly). The same anime in two channels must become
one card, so:

- `AnimeEntry.match_key` is a normalized name (`_key`: lowercase, alphanumerics
  only) and is the merge identity. `_resolve_row` looks up by
  (source_chat, entry_index) first, then by match_key, and only creates a row
  when both miss. `import_entries` reports `merged` for cross-channel hits.
- Per-channel links live in `EntryChannel` (unique on entry_id+source_chat), not
  on the entry. `_record_channel` upserts them; `_channel_links` reads them.
  `/api/entry/{id}` returns `channels[]` plus `channel_count`; `static/app.js`
  renders one button per channel and labels the card "N channels".
- `collapse_duplicates` merges rows that share a match_key (oldest wins),
  moving channels/qualities and re-pointing `anime_id`. `backfill_match_keys`
  seeds match_key + an EntryChannel for rows created before this existed. Both
  run in `refresh_all` and in the `/catalog` command, so old databases converge.
- When merging, do NOT delete a duplicate row without first moving its
  `EntryChannel` and `QualityLink` rows — a cascade delete would drop them.

Titles are English-only: `catalog.display_title` returns
`title_english or title or raw_name`, never `title_japanese`. `/api/catalog`
sorts by `coalesce(title_english, title, raw_name)`. Do not set
`title_english` from a scraped channel name — only AniList/Jikan may supply it,
or a Japanese romaji name would leak into the grid.

`enrich_pending` treats a placeholder row (`anime.source == "index"`) as still
pending, so a transient metadata miss (Jikan 504, AniList rate limit) is retried
on the next pass instead of being stuck poster-less forever.

## Testing

`tests/test_index.py` is an integration suite. Feeds are served by a real local
HTTP server; only the external metadata provider is stubbed using the captured
AniList fixture `tests/anilist_frieren.json`. Keep tests on real code paths.
