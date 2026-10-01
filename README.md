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

## Features

- **Mini App UI** — dark, poster-grid interface with a featured hero, search,
  genre chips, a detail sheet, and a channel browser.
- **Live indexing** — the webhook indexes new channel posts the moment they are
  published; a background scheduler also polls RSS feeds on an interval.
- **Curated index channels** — reads "name + link" lists from Telegram
  `text_link` entities, HTML anchors, Markdown links, or plain lines, and links
  each entry straight to its target. Index channels are detected automatically
  (or forced with `/indexchannel` or `INDEX_CHANNELS`).
- **Manual import** — `/import` indexes a pasted list of name+link lines.
- **Rich metadata** — AniList first, Jikan fallback, with progressively looser
  title matching (`Frieren: Beyond Journeys End` → `Sousou no Frieren`).
- **Smart title parsing** — strips release tags, quality markers, file
  extensions and CJK brackets, and extracts episode/season hints.
- **Self-serve feeds** — `/rss <url>` lets a channel owner set a custom feed
  when no RSS bridge is available.
- **Graceful degradation** — works in API-only mode without a bot token, and
  survives flaky upstream metadata APIs.

## Architecture

```
app/
  config.py             settings (env driven)
  models.py             SQLAlchemy models: Anime, Channel, Post
  main.py               FastAPI app, lifespan, static mount
  routers/
    api.py              JSON API for the Mini App + admin refresh
    webhook.py          Telegram webhook: commands, channel posts, membership
  services/
    anilist.py          AniList GraphQL provider (primary)
    jikan.py            Jikan/MyAnimeList provider + title parsing
    metadata.py         unified lookup with fallbacks
    index_parser.py     parses "name + link" lists from index channels
    rss.py              feed fetching/parsing
    ingest.py           RSS/entry -> metadata -> DB pipeline
    scheduler.py        periodic feed polling
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
| `/help` | Show help |

## API

| Endpoint | Description |
| --- | --- |
| `GET /api/home?q=&genre=&limit=&offset=` | Search/browse anime |
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
