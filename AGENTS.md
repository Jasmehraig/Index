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
  `get_session().__next__()` â it leaks the generator.
- All SQLAlchemy models live in `app/models.py`; schema is created with
  `Base.metadata.create_all`, so new columns need a manual `ALTER TABLE` on
  existing databases (there is no migration tool). `init_db` calls `_migrate`,
  which adds the post-`kind`/`url`/`entry_index` columns,
  `anime_entries.match_key`, the `anime_entries` release-detail columns
  (`audio`, `subtitles`, `quality_tags`, `description`) and `anime.studio` to
  older SQLite files. New *tables* (`entry_channels`, `seasons`, `episodes`)
  are created by `create_all` and need no migration entry.
- Storage is SQLite by default but Postgres is a supported target. Keep new SQL
  portable: `ilike`/`coalesce`/`func.count` are fine, but nothing SQLite-only
  (`strftime`, `PRAGMA`, `INSERT OR REPLACE`, `AUTOINCREMENT`) outside the
  guarded `_migrate` block. `_as_psycopg_url` rewrites a bare
  `postgresql://`/`postgres://` URL to `postgresql+psycopg://`; the non-SQLite
  engine sets `pool_pre_ping` + `pool_recycle=300` for serverless databases.
- Channels have a `kind`: `"feed"` (one anime per post) or `"index"` (posts are
  lists of name+link pairs). Ingestion branches on it in `ingest.ingest_entries`.
  Index entries carry their own `Post.url`; always link via `post.target_link`,
  never `post.telegram_link`, so curated links win over the channel post.
- External HTTP calls go through `app/services/*` and must degrade gracefully
  when an upstream API is down (return `None`/`[]`, never raise into a webhook).
- The Telegram webhook must always return `{"ok": true}`; wrap handlers in
  try/except and log instead of propagating.
- Telegram retries an update when the webhook does not answer within ~30s, and
  Heroku kills the request there (`H12` â 503). Anything a command does over the
  network (`/catalog`, `/refresh`) must therefore run as a detached background
  task (`catalog.spawn`) and report its own result; the request handler returns
  immediately. Never block the event loop with `time.sleep` in a request path â
  use `asyncio.sleep` (see `telegram_web.fetch_channel`).
- Do not `await` a Telegram send in a webhook handler either: `sendMessage` can
  take seconds, which eats the same 30s budget. Use `webhook.notify_later`, which
  fires the send as a background task.
- Telegram re-delivers an update whose response was slow, so the same `/catalog`
  can arrive several times. `_catalog_running` collapses them: only the first
  starts a scrape, the rest get "Already readingÉ". Never start a second scrape
  for a channel that already has one in flight.
- A single `uvicorn` worker serves both the Telegram webhook and the Mini App
  API, so anything that stalls the event loop stalls the app.

## Gotchas

- AniList is the primary metadata provider, Jikan the fallback. AniList's
  single-`Media` query 404s on unmatched titles â use `Page.media` so it returns
  an empty list instead.
- The metadata clients throttle with a module-level `asyncio.Lock` plus a min
  interval. A `Lock` is **not reentrant**: a retry must run *outside* the
  `async with _lock:` block (release, back off, loop). Retrying recursively while
  still holding the lock deadlocks, and with `enrich_pending`'s
  `asyncio.wait_for(..., 20)` the whole enrichment pass stalls ~20s on the first
  AniList 429. Keep retries as a bounded `for attempt in range(...)` loop.
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
  small-caps (`á´ á´ Éª ê± á´ á´ á´ ê±`), so field regexes must include those codepoints.
- `services/catalog.py` writes entries first (`import_entries`), merges detail
  cards (`apply_details`), and enriches metadata as a separate pass
  (`enrich_pending`) because AniList rate-limits at 0.7s/request.
- Database-channel files are matched to entries by filename via
  `catalog.match_entry` / `catalog.attach_file` (longest catalog name contained
  in the cleaned filename). Called from `webhook._attach_channel_file`.
- API: `/api/catalog`, `/api/sections`, `/api/entry/{id}`,
  `/api/entry/{id}/quality`, `/api/catalog/refresh`. The Mini App
  (`static/app.js`) renders the hero + rails from `/api/sections` and the grid
  from `/api/catalog`; the detail page comes from `/api/entry/{id}`.

## Seasons and episodes

Owners add episodes by sending the bot one plain-text message per title:

```
Anime name - Demon Slayer
Season - Season 01
Language - English, Japanese
Subtitle - English Sub
Quality - 480p, 720p, 1080p, HD-RIP
Episode 1 - https://t.me/FileBot?start=a1
```

- `services/episode_parser.py` parses it. Fields are **season-scoped**: a
  `Language`/`Subtitle`/`Quality` line after a `Season -` line belongs to that
  season; lines before any season become defaults. Do not re-introduce
  message-wide fields that leak one season's subtitles onto another.
- `webhook._handle_message` treats any non-command message that
  `looks_like_episode_message` as episode input; `/episodes` forces it.
- `catalog.find_entry` matches the title loosely (match_key â raw name â
  longest catalog name contained in the query), so "Demon Slayer" finds
  "Demon Slayer: Kimetsu no Yaiba".
- `catalog.upsert_episodes` is keyed by (entry, season number) and
  (season, episode number), so re-sending a corrected message updates links in
  place. `catalog.entry_seasons` shapes the payload the Mini App reads.
- Defaults the UI always shows even with no episode data: `480p Â· 720p Â· 1080p Â·
  HD-RIP` and `English Sub` (see `api.DEFAULT_QUALITIES` / `DEFAULT_SUBTITLES`).

## Release channels (`/syncepisodes`)

A "single-anime" release channel posts one block per episode with a `START`
deep link, e.g. `➥ Episode : 01` / `➥ Season : 01` / `➥ Audio : Japanese
[Eng Sub]`. `/syncepisodes <@channel> [anime]` builds that anime's episode list
from those posts.

- `services/release_parser.py` parses one post. `unicodedata.normalize("NFKC")`
  folds the decorative bold/fullwidth fonts to ASCII first (`𝟰𝟴𝟬𝗽` → `480p`).
  One post can hold several episode blocks (split on the `────`/`┃███` rules)
  sharing a single link at the bottom, or the link may only exist as Telegram's
  link-preview card — pass the post's anchors as `fallback_urls`. `Audio : X
  [Eng Sub]` yields audio `X` and subtitles `English`; `Jap + Eng [Dual audio]`
  yields `Japanese, English`. Output matches `episode_parser` so it feeds
  `catalog.upsert_episodes` unchanged (`quality_tags` is a comma string).
- `catalog.collect_release_episodes` is network-only; `apply_release_episodes`
  is DB-only. Keep them split — SQLite takes a write lock as soon as a session
  has pending changes, so the channel fetch must happen before the session opens.
  `sync_release_channel` is the two together for callers that don't care.
- The anime is resolved as: the name passed on the command → `RELEASE_CHANNEL_MAP`
  → the channel's own `og:title` (`telegram_web.channel_title`, exposed on each
  post as `channel_title`) → the username. Single-anime channels are named after
  the show, so no argument is usually needed; when the title comes from the
  channel and no card exists, one is created and enrichment is scheduled.
- Private channels have no `t.me/s` preview, so their posts are read in the
  webhook instead: `webhook._sync_release_from_post` runs on every channel post
  and attaches episodes to the card named after the channel. It only touches
  entries that already exist, so it never drifts onto unrelated feed channels.

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
  `EntryChannel` and `QualityLink` rows â a cascade delete would drop them.

Titles are English-only: `catalog.display_title` returns
`title_english or title or raw_name`, never `title_japanese`. `/api/catalog`
sorts by `coalesce(title_english, title, raw_name)`. Do not set
`title_english` from a scraped channel name â only AniList/Jikan may supply it,
or a Japanese romaji name would leak into the grid.

`enrich_pending` treats a placeholder row (`anime.source == "index"`) as still
pending, so a transient metadata miss (Jikan 504, AniList rate limit) is retried
on the next pass instead of being stuck poster-less forever.

## Testing

`tests/test_index.py` is an integration suite. Feeds are served by a real local
HTTP server; only the external metadata provider is stubbed using the captured
AniList fixture `tests/anilist_frieren.json`. Keep tests on real code paths.

## Posters, ongoing and popularity

- Posters come only from `catalog.enrich_pending`. Nothing else fetches them, so
  it must actually run: it is started on boot (`main.lifespan`), after `/catalog`,
  `/ongoing` and `refresh_all`, and every 10 min by the scheduler
  (`enrich_catalog`). It is resumable and skips titles that missed in the last 6 h
  (`_missed`). `/enrich` starts it by hand. Never await it inside a webhook.
- Never hardcode `BOT_TOKEN` / `DATABASE_URL` defaults in `config.py`.
- `/api/sections`: `popular` = views desc, then score, then year; `ongoing` = the
  `ongoing_entries` list (position order), falling back to AniList-airing titles,
  never padded with unrelated titles.
- `OngoingEntry` / `EntryView` are new tables on purpose: Postgres has no
  `_migrate`, so new *columns* on existing tables would need a manual ALTER.
- Plain-message "ongoing anime" lists and `/ongoing` go through
  `services/ongoing_parser.py`; only `text_link` entities are used because
  Telegram tags every typed URL as a `url` entity.
- `Settings.index_channel_usernames` keeps the channel's original casing: the
  `t.me/s` preview tags posts `data-post="Name/123"` and paging stops otherwise.

## Access control (owner-only commands)

The bot is private. Only the owner, listed admins, and channel owners may run
commands; everyone else can only open the Mini App and read `/help`.

- `Settings.owner_user_ids` (`OWNER_USER_IDS`) lists the bot owner's Telegram
  ids. The code cannot tell a bot's creator, so set this env var by hand; it is
  the "Owner add it" step.
- `_is_admin` returns true for `ADMIN_USER_IDS`, `OWNER_USER_IDS`, or any
  `Channel.owner_user_id` (whoever added the bot).
- `_handle_message` gates every command except `/start` and `/help` behind
  `_is_admin`, including in private chats - Telegram lets any user DM a bot, so
  the private chat is not a trust boundary. Plain-text ongoing/episode input is
  only handled in private chats; group text is ignored.
- Tests: `test_start_and_help_are_public`, `test_public_user_cannot_run_commands`,
  `test_owner_and_admin_can_run_commands`, `test_group_plain_text_is_ignored`.

## Mini App detail page

- `/api/catalog` defaults to `limit=120`; the Mini App asks for the max (500) so
  the grid and letter filter show the whole catalog.
- The Mini App keeps the Enji-03 UI. The detail page (`static/app.js`
  `openDetail` + `seasonBlock`) is Enji-03's: hero banner, two-column body
  (left: genres, synopsis, seasons/episodes, quality; right: channels +
  Information). Do not swap it for Enji-01's detail markup.
