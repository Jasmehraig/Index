# Index

A Telegram bot + Telegram Mini App that indexes anime shared across Telegram
channels and presents them in a cinematic, movie-hosting-style browsing UI.

Add the bot to a channel as an admin. It reads the channel's posts (live via the
Telegram webhook and/or from the channel's RSS feed), matches each title against
[AniList](https://anilist.co) (falling back to
[Jikan/MyAnimeList](https://jikan.moe)) to fetch posters, banners, genres,
scores and synopses, and stores everything in a searchable index. Users open the
Mini App, search or filter by genre, tap a poster, and jump straight to the
original channel post.

It understands two kinds of channels:

- **Feed channels** — release channels where each post is one anime file
  (`[SubsPlease] Frieren - 05 (1080p).mkv`).
- **Index channels** — curated channels that post lists of *name + link* pairs
  (`Haikyuu` next to a `t.me` link). Every pair becomes its own searchable
  entry, and tapping it opens that exact link.

You can index **several** index channels at once (yours and a friend's). The same
anime listed by more than one channel is merged into a single card that offers a
button per channel, so there is no duplication. Titles are always shown in
English — AniList's English name when it has one, otherwise the romaji name, never
the Japanese one.

## Features

- **Mini App UI** — dark, poster-grid interface with a featured hero, search,
  genre chips, a detail sheet, and a channel browser.
- **Live indexing** — the webhook indexes new channel posts the moment they are
  published; a background scheduler also polls RSS feeds on an interval.
- **Curated index channels** — reads "name + link" lists from Telegram
  `text_link` entities, HTML anchors, Markdown links, or plain lines, and links
  each entry straight to its target. Index channels are detected automatically
  (or forced with `/indexchannel` or `INDEX_CHANNELS`).
- **Multiple index channels, merged** — index your own channel and a friend's;
  an anime listed by both becomes one card with a button per channel, so there
  is no duplication.
- **English titles** — cards and detail pages show AniList's English title when
  available, otherwise the romaji title; the Japanese title is never shown.
- **Manual import** — `/import` indexes a pasted list of name+link lines.
- **Rich metadata** — AniList first, Jikan fallback, with progressively looser
  title matching (`Frieren: Beyond Journeys End` → `Sousou no Frieren`).
- **Smart title parsing** — strips release tags, quality markers, file
  extensions and CJK brackets, and extracts episode/season hints.
- **Self-serve feeds** — `/rss <url>` lets a channel owner set a custom feed
  when no RSS bridge is available.
- **Graceful degradation** — works in API-only mode without a bot token, and
  survives flaky upstream metadata APIs.

## How the bot works

The whole thing is one FastAPI process that talks to Telegram on one side and
the Mini App on the other. Nothing runs on Telegram's servers — the bot is a
normal web service that receives updates by webhook.

**1. Updates arrive by webhook.** When you set `PUBLIC_BASE_URL`, the app calls
`setWebhook` and points Telegram at `POST {PUBLIC_BASE_URL}/telegram/webhook`.
Every message, channel post, and Mini App button press is delivered there. The
webhook is verified with a secret token (`TELEGRAM_WEBHOOK_SECRET`) so only
Telegram can post to it.

**2. Channel posts are indexed as they happen.** A `channel_post` update is
stored, the title is parsed (release tags, quality markers, episode hints), the
channel is auto-classified as `feed` or `index`, and — for feed channels — the
anime is matched against AniList and written to the index. This is the live path:
a new file in your channel shows up in the Mini App within seconds.

**3. RSS and scheduled polling backfill the rest.** Some channels are easier to
read from a feed. A background scheduler (APScheduler, in-process) polls each
channel's RSS on `POLL_INTERVAL_MINUTES` and runs the same ingest pipeline, so
posts published while the bot was offline are still picked up. Index channels are
re-read on the same schedule via `refresh_catalog`.

**4. Index channels are scraped from their public preview.** A curated channel
that posts `[01] 91 Days` next to a `t.me` link is read from
`https://t.me/s/<username>` — no bot membership required. Each name+link pair
becomes a catalog row; detail posts (`⧉ Title`, Season / Episodes / Genres /
Synopsis) are merged onto the matching row. Links are classified as a public
channel, a private invite (`t.me/+…`), a channel post, or a file-share bot deep
link.

**5. Metadata enrichment runs as a separate pass.** Import is fast and writes rows
immediately so the app is usable at once; a slower pass then fills in AniList
metadata (poster, banner, genres, episodes, score, year) with Jikan as fallback.
Lookups are rate-limited and retried — a title that misses once is left pending
and picked up on a later pass rather than being stuck without a poster.

**6. The Mini App reads a JSON API.** `app/static/` is a small vanilla-JS client
that calls `/api/catalog`, `/api/entry/{id}`, `/api/genres`, and `/api/channels`.
It runs inside Telegram's WebView, opens links with `openTelegramLink`, and is
validated with Telegram `initData` when auth is enabled.

### Command reference

| Command | What it does |
| --- | --- |
| `/start` | Opens the Mini App (menu button) |
| `/catalog @Channel` | Import/refresh the catalog from a public index channel |
| `/indexchannel [@Channel]` | Mark the current channel (or one named) as an index channel |
| `/import` | Paste a list of `Name - link` lines to index them directly |
| `/rss <url>` | Set a custom RSS feed for the channel |
| `/refresh` | Re-scan feeds and index channels now |
| `/quality <id> <quality> <url>` | Attach a manual download link to a catalog entry |
| `/status` | Show indexing stats |

## Where the data is stored

Everything lives in a single **SQLite database** at `data/index.db` (the path is
set by `DATABASE_URL`, default `sqlite:///./data/index.db`). The `data/` directory
is created on first run and is git-ignored. SQLite runs in **WAL mode** so the
scheduler, webhook, and Mini App reads do not block each other.

Tables:

| Table | Holds |
| --- | --- |
| `anime` | Enriched metadata — titles (English/romaji/Japanese), poster, banner, genres, episodes, status, score, year, AniList/MAL ids |
| `channels` | Channels the bot has been added to, their kind (`feed`/`index`), RSS URL, invite link |
| `posts` | Indexed channel posts, the parsed title, and episode hints |
| `anime_entries` | **The catalog** — one row per anime listed by an index channel, with a `match_key` used to merge duplicates |
| `entry_channels` | One row per index channel that lists an anime, and the link it points at — this is what gives a card multiple channel buttons |
| `quality_links` | Download links per entry, tagged with quality (`1080p`, `Batch`, …) and whether they go through a file-share bot |

Because the database is a file, **it needs a persistent disk in production**.
On a host with an ephemeral filesystem the index is wiped on every deploy and
rebuilt from RSS on the next poll — fine for a demo, but mount a volume for
anything durable.

SQLite is the supported default. `DATABASE_URL` accepts any SQLAlchemy URL, so
Postgres is possible, but it needs the driver added to `requirements.txt`
(`psycopg[binary]`) and the lightweight column migrations in `models.py` are
SQLite-only — they are skipped on other dialects.

The Mini App's static files (`index.html`, `styles.css`, `app.js`) are served
from `app/static/` and bundled into the image; they carry no state.

## Architecture

```
app/
  config.py             settings (env driven)
  models.py             SQLAlchemy models: Anime, Channel, Post, AnimeEntry,
                        EntryChannel, QualityLink
  main.py               FastAPI app, lifespan, static mount
  routers/
    api.py              JSON API for the Mini App + admin refresh
    webhook.py          Telegram webhook: commands, channel posts, membership
  services/
    anilist.py          AniList GraphQL provider (primary)
    jikan.py            Jikan/MyAnimeList provider + title parsing
    metadata.py         unified lookup with fallbacks
    telegram_web.py     scrapes a public index channel's t.me/s preview
    catalog.py          builds the Mini App catalog, merges duplicates,
                        enriches metadata, matches channel files
    index_parser.py     parses "name + link" lists from index channels
    rss.py              feed fetching/parsing
    ingest.py           RSS/entry -> metadata -> DB pipeline
    scheduler.py        periodic feed polling + catalog refresh
    telegram.py         Telegram Bot API client
    webapp_auth.py      Mini App initData validation
  static/               index.html, styles.css, app.js (the Mini App)
tests/                  pytest integration suite
```

## Index channels (name + link lists)

If you run a curated channel that lists anime names next to links (the "create
link" feature), the bot indexes every pair as its own entry:

1. Add the bot to the channel as an admin. If a post contains two or more
   name+link pairs it is recognised as an index channel automatically.
2. Or force it: send `/indexchannel` (or `/indexchannel @yourchannel`) in the
   bot's private chat, or list it in `INDEX_CHANNELS`.
3. Already published posts can be pulled in with `/refresh` (or `/rss <url>` for
   a feed), and one-off lists can be pasted after `/import`.

Each entry stores its own URL, so tapping a poster in the Mini App opens the
target link directly rather than the channel post. Recognised formats:

```
Haikyuu                     ← Telegram text_link entity (the "create link" feature)
https://t.me/animefiles/12

<a href="https://t.me/animefiles/12">Haikyuu</a>   ← HTML anchor (RSS bridges)
[Haikyuu](https://t.me/animefiles/12)             ← Markdown
Haikyuu - https://t.me/animefiles/12              ← plain line
Haikyuu
https://t.me/animefiles/12                        ← name, then bare link
```

## The catalog Mini App

The Mini App is a cinematic, movie-hosting-style index. It is built from one or
more **public index channels** — channels that post numbered lists of anime names
and links — and does not require the bot to be a member of those channels.

Set the authoritative source(s) and rebuild them any time:

```bash
# One channel, or several separated by commas (yours + a friend's)
INDEX_CHANNELS=Anime_Index_swordsmith,Friends_Anime_Index
```

```
/catalog @Anime_Index_swordsmith
```

### Several index channels, no duplicates

Listing the same anime in two index channels does **not** create two cards. Every
row is keyed by a normalized name, so when a second channel lists an anime the
first channel already has, the row is reused and the new link is attached to it.
The detail page then shows one button per channel:

```
Available in 2 channels
  📢 Anime_Index_swordsmith   Public Channel · https://t.me/…
  🔒 Friends_Anime_Index      Private Channel · https://t.me/+…
```

On the grid, such a card is labelled `2 channels` instead of a single kind.
Duplicates that already exist are collapsed the next time you run `/catalog` or
`/refresh`, which also backfills match keys for older rows.

The scraper reads the channel's public web preview (`t.me/s/<username>`) and
handles both post types those channels use:

* **Series-list posts** — `[01] 91 Days` with an anchor to the target. Each pair
  becomes a catalog row, and the link is classified as a public channel, a
  private invite (`t.me/+…`), a channel post, or a file-share bot deep link.
* **Detail posts** — `⧉ Handa-kun + Barakamon` with Season / Episodes / Audio /
  Genres / Synopsis. These are merged onto the matching row so the detail page
  carries the extra fields.

UI chrome (navigation buttons, "Information" posts, self-links back to the index
channel) is filtered out so only real anime names are indexed.

### Metadata

Every catalog row is enriched with **AniList** metadata (poster, synopsis,
genres, episodes, score, year) with **Jikan** as a fallback. Lookups are
rate-limited and run as a separate pass, so the catalog is browsable the moment
it is imported:

```
/catalog @Anime_Index_swordsmith     # import fast, enrich in the background
```

### Database channels

When the bot is an admin in the channel that actually hosts the files, incoming
documents are matched against the catalog by filename. A file such as
`[SubsPlease] Demon Slayer - 03 (1080p).mkv` is attached to the *Demon Slayer*
entry as a `1080p` download link, so the detail page can offer quality buttons
that jump straight to the file. Owners can also add links by hand:

```
/quality 12 1080p https://t.me/YourFileBot?start=abc
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env        # then fill in BOT_TOKEN and PUBLIC_BASE_URL
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000> for the Mini App and `/health` for a status check.
Without `BOT_TOKEN` the app runs in API-only mode so you can develop the UI.

### Wiring up Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token
   into `BOT_TOKEN`.
2. Expose the app over HTTPS (ngrok, cloudflared, or a real host) and set
   `PUBLIC_BASE_URL` to that URL.
3. Start the app. On boot it registers the webhook and sets the Mini App menu
   button automatically.
4. Add the bot to a channel as an **admin**. It registers the channel, tries to
   discover an RSS feed, and starts indexing.

### Bot commands

| Command | Description |
| --- | --- |
| `/start` | Open the Mini App |
| `/channels` | List indexed channels |
| `/refresh` | Re-scan feeds (channel owners/admins) |
| `/rss <url>` | Set a custom feed for your channel |
| `/indexchannel [@chan]` | Treat your channel as a name+link index |
| `/feed [@chan]` | Treat your channel as a release feed |
| `/import <lines>` | Index a pasted list of name+link lines |
| `/catalog [@chan]` | Build the catalog from a public index channel |
| `/quality <id> <quality> <link>` | Add a download link to a catalog entry |
| `/help` | Show help |

## API

| Endpoint | Description |
| --- | --- |
| `GET /api/home?q=&genre=&limit=&offset=` | Search/browse anime |
| `GET /api/catalog?q=&genre=&limit=&offset=` | Browse the index-channel catalog |
| `GET /api/entry/{id}` | Catalog entry: metadata, `channels[]` (one per index channel), quality links |
| `GET /api/genres` | Genre facets with counts |
| `GET /api/anime/{id}` | Anime detail with all channel posts |
| `GET /api/channels` | Indexed channels |
| `POST /api/auth/validate` | Verify Mini App `initData` |
| `POST /api/admin/refresh` | Re-index all channels (`X-Admin-Token`) |
| `POST /telegram/webhook` | Telegram update receiver |

## Tests

```bash
python -m pytest tests/ -q
```

The suite exercises the real ingestion → database → API path. Feeds are served
by a real local HTTP server, and only the external metadata provider is stubbed
using a captured AniList response fixture.

## Configuration

See [`.env.example`](.env.example). The most important values are `BOT_TOKEN`,
`PUBLIC_BASE_URL`, and `DATABASE_URL`. `RSS_PROVIDERS` points at RSS bridge
templates — the public RSSHub instances are rate limited, so a self-hosted
instance is recommended for anything beyond light use.

`INDEX_CHANNELS` takes a comma-separated list, so you can index your channel and
a friend's together; duplicates across them are merged automatically.

## Getting a free public HTTPS URL

Telegram only opens Mini Apps over HTTPS, so even for local development you need
a public HTTPS URL. All of these are free and give you a URL instantly:

| Tool | Command | Notes |
| --- | --- | --- |
| **Cloudflare Tunnel** | `cloudflared tunnel --url http://localhost:8000` | No account needed; prints a `https://*.trycloudflare.com` URL. Most reliable. |
| **ngrok** | `ngrok http 8000` | Free account required; URL changes each restart. |
| **localtunnel** | `npx localtunnel --port 8000` | `npx` only, no install. Occasionally shows an interstitial. |
| **serveo** | `ssh -R 80:localhost:8000 serveo.net` | No install (uses your SSH client). |

Copy the printed URL into `PUBLIC_BASE_URL` and restart the app. It registers the
webhook and Mini App menu button automatically.

```bash
# Example with Cloudflare Tunnel
cloudflared tunnel --url http://localhost:8000
# => https://random-words-1234.trycloudflare.com
echo 'PUBLIC_BASE_URL=https://random-words-1234.trycloudflare.com' >> .env
uvicorn app.main:app --port 8000
```

### How long does a quick tunnel last?

These are all **quick tunnels** — fine for development, not for a bot you leave
running:

| | Lifetime |
| --- | --- |
| `trycloudflare` quick tunnel | No fixed limit, but the URL is random and dies with the process. If the machine sleeps, reboots, or `cloudflared` exits, the hostname is gone and you must re-set the webhook. |
| `ngrok` free | The process must stay up; the URL changes on every restart (one static domain is included on the free plan). |
| `localtunnel` / `serveo` | Similar — dies with the process, and both are flaky under load. |

So a quick tunnel is online exactly as long as the machine running `cloudflared`
stays awake. When the URL changes, Telegram keeps delivering updates to the old
one and the bot goes silent — re-run the tunnel and update `PUBLIC_BASE_URL` (the
app re-registers the webhook on startup).

**For always-on hosting**, deploy the service itself instead of tunnelling: a
small VM, Fly.io, Railway, or Render (see below) keeps the process and the
database alive 24/7 and gives you a stable HTTPS URL with no tunnel to babysit.
For a named, stable Cloudflare URL on your own domain, use a *named* tunnel
(`cloudflared tunnel create`) with a DNS record instead of `--url`.

## Free deployment options

| Platform | Cost | Persistent disk | Notes |
| --- | --- | --- | --- |
| **Fly.io** | Free allowance | ✅ volumes | Best fit — the app needs a persistent volume for SQLite. |
| **Railway** | Trial credit, then paid | ✅ volumes | `railway.toml` included. Add a volume at `/app/data`. |
| **Render** | Free web service | ❌ (disk is paid) | `render.yaml` included. DB resets on redeploy unless you add a disk. |
| **Koyeb** | Free tier | ❌ | Docker-based; `Dockerfile` included. |
| **Hugging Face Spaces** | Free | ❌ | Docker SDK works; ephemeral storage. |

The app is a long-running web service (it holds a webhook and an in-process
scheduler), so serverless/edge platforms that only run request handlers — Vercel,
Netlify Functions, Cloudflare Workers — are **not** suitable.

### Docker / Compose

```bash
cp .env.example .env      # set BOT_TOKEN and PUBLIC_BASE_URL
docker compose up --build
```

SQLite is stored on the `index-data` volume so it survives restarts.

### Fly.io example

```bash
fly launch --no-deploy
fly volumes create index_data --size 1
# add to fly.toml:  [mounts]  source="index_data"  destination="/app/data"
fly secrets set BOT_TOKEN=... PUBLIC_BASE_URL=https://<app>.fly.dev
fly deploy
```

> **Ephemeral storage warning:** on platforms without a persistent volume the
> SQLite file is wiped on every deploy/restart and the index rebuilds from RSS
> on the next poll. Fine for a demo; use a volume (or a Postgres `DATABASE_URL`)
> for anything durable.

## License

GPL-3.0. See [LICENSE](LICENSE).
