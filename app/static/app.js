(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    try { tg.setHeaderColor("#0a0b10"); tg.setBackgroundColor("#0a0b10"); } catch (e) {}
  }

  const els = {
    grid: document.getElementById("grid"),
    hero: document.getElementById("hero"),
    empty: document.getElementById("empty"),
    loader: document.getElementById("loader"),
    search: document.getElementById("searchInput"),
    clear: document.getElementById("clearBtn"),
    genres: document.getElementById("genreBar"),
    letters: document.getElementById("letterBar"),
    sort: document.getElementById("sortSelect"),
    popularBlock: document.getElementById("popularBlock"),
    popularRail: document.getElementById("popularRail"),
    ongoingBlock: document.getElementById("ongoingBlock"),
    ongoingRail: document.getElementById("ongoingRail"),
    detail: document.getElementById("detail"),
    sheet: document.getElementById("sheet"),
    channelsBtn: document.getElementById("channelsBtn"),
    topBtn: document.getElementById("topBtn"),
  };

  const state = {
    q: "",
    genre: null,
    letter: null,
    sort: "title",
    items: [],
    genres: [],
    channels: [],
    popular: [],
    ongoing: [],
    loading: false,
  };

  const api = async (path) => {
    const res = await fetch(path, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error("Request failed: " + res.status);
    return res.json();
  };

  const escapeHtml = (str = "") =>
    String(str).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const initial = (title = "") => escapeHtml((String(title).trim()[0] || "?").toUpperCase());

  const fallbackDiv = (title = "") => `<div class="fallback">${initial(title)}</div>`;

  const posterImg = (url, title) =>
    url
      ? `<img loading="lazy" src="${escapeHtml(url)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'fallback',textContent:'${initial(title)}'}))" />`
      : fallbackDiv(title);

  const toast = (msg) => {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    requestAnimationFrame(() => el.classList.add("show"));
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 2200);
  };

  const openLink = (url) => {
    if (!url) return;
    try {
      if (tg && tg.openTelegramLink && /^https?:\/\/(t\.me|telegram\.me)\//.test(url)) tg.openTelegramLink(url);
      else if (tg && tg.openLink) tg.openLink(url);
      else window.open(url, "_blank");
    } catch (e) { window.open(url, "_blank"); }
  };

  const KIND = {
    channel:        { label: "Public Channel",  icon: "📢" },
    private_invite: { label: "Private Channel", icon: "🔒" },
    channel_post:   { label: "Channel Post",    icon: "📄" },
    file_bot:       { label: "File Bot",        icon: "🤖" },
    bot:            { label: "Bot",             icon: "🤖" },
    external:       { label: "External Link",   icon: "🔗" },
  };
  const kindInfo = (k) => KIND[k] || KIND.external;

  const QUALITY_ORDER = ["480p", "720p", "1080p", "2160p", "4k", "hd-rip", "web-rip", "bluray", "bdrip"];
  const sortQualities = (list) => [...(list || [])].sort((a, b) => {
    const ia = QUALITY_ORDER.indexOf(a.quality), ib = QUALITY_ORDER.indexOf(b.quality);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });

  const statusClass = (status) => {
    const s = (status || "").toLowerCase();
    if (s.includes("air") || s.includes("releas") || s.includes("ongoing")) return "airing";
    if (s.includes("finish")) return "finished";
    if (s.includes("upcoming") || s.includes("not yet")) return "upcoming";
    return "";
  };

  /* ------------------------------ Cards ------------------------------ */
  function cardHtml(item) {
    const title = item.title || item.raw_name;
    const badge = item.episode_count
      ? `<span class="badge">${item.episode_count} EP</span>`
      : item.episodes ? `<span class="badge">${item.episodes} EP</span>` : "";
    const score = item.score ? `<span class="score">★ ${Number(item.score).toFixed(1)}</span>` : "";
    const st = statusClass(item.status);
    const stTag = st ? `<span class="status-dot ${st}" title="${escapeHtml(item.status || "")}"></span>` : "";
    return `
      <article class="card" data-id="${item.id}">
        <div class="poster">
          ${posterImg(item.poster_url, title)}
          <div class="poster-fade"></div>
          ${badge}${score}${stTag}
        </div>
        <div class="card-info">
          <h3 class="card-title">${escapeHtml(title)}</h3>
          <div class="card-sub">
            <span>${escapeHtml(item.year || "")}</span>
            ${item.season_count > 1 ? `<span>${item.season_count} seasons</span>` : ""}
          </div>
        </div>
      </article>`;
  }

  function railCardHtml(item) {
    const title = item.title || item.raw_name;
    const score = item.score ? `<span class="rail-score">★ ${Number(item.score).toFixed(1)}</span>` : "";
    return `
      <article class="rail-card" data-id="${item.id}">
        <div class="rail-poster">
          ${posterImg(item.poster_url, title)}
          <div class="poster-fade"></div>
          ${score}
          <span class="rail-play">▶</span>
        </div>
        <h4 class="rail-title">${escapeHtml(title)}</h4>
      </article>`;
  }

  /* ------------------------------ Hero ------------------------------ */
  function renderHero(item) {
    if (!item) { els.hero.hidden = true; return; }
    const title = item.title || item.raw_name;
    const bg = (item.banner_url || item.poster_url)
      ? `style="background-image:url('${escapeHtml(item.banner_url || item.poster_url)}')"`
      : "";
    const genres = (item.genres || "").split(",").map((g) => g.trim()).filter(Boolean).slice(0, 3);
    const kind = item.channel_kind ? kindInfo(item.channel_kind) : null;
    els.hero.hidden = false;
    els.hero.innerHTML = `
      <div class="hero-bg" ${bg}></div>
      <div class="hero-shade"></div>
      <div class="hero-body">
        <span class="hero-tag">${kind ? kind.icon + " " + kind.label : "Featured"}</span>
        <h2 class="hero-title">${escapeHtml(title)}</h2>
        <div class="hero-meta">
          ${item.score ? `<b>★ ${Number(item.score).toFixed(1)}</b>` : ""}
          ${item.year ? `<span>${item.year}</span>` : ""}
          ${item.episodes ? `<span>${item.episodes} episodes</span>` : ""}
          ${item.status ? `<span>${escapeHtml(item.status)}</span>` : ""}
        </div>
        ${genres.length ? `<div class="hero-genres">${genres.map((g) => `<span>${escapeHtml(g)}</span>`).join("")}</div>` : ""}
        <button class="btn btn-primary" data-open="${item.id}">▶ Watch Now</button>
      </div>`;
  }

  /* ------------------------------ Rails ------------------------------ */
  function renderRail(blockEl, railEl, items) {
    if (!items || !items.length) { blockEl.hidden = true; return; }
    blockEl.hidden = false;
    railEl.innerHTML = items.map(railCardHtml).join("");
    railEl.querySelectorAll(".rail-card").forEach((el) => {
      el.addEventListener("click", () => {
        const item = items.find((i) => String(i.id) === el.dataset.id);
        if (item) openDetail(item);
      });
    });
  }

  function renderRails() {
    renderRail(els.popularBlock, els.popularRail, state.popular);
    renderRail(els.ongoingBlock, els.ongoingRail, state.ongoing);
  }

  /* ------------------------------ Grid ------------------------------ */
  function renderGrid() {
    els.loader.hidden = true;
    const items = state.items;
    els.empty.hidden = items.length > 0;
    if (!items.length) {
      els.grid.innerHTML = "";
      els.empty.querySelector("h2").textContent =
        state.q || state.genre || state.letter ? "No matches" : "Nothing indexed yet";
      els.empty.querySelector("p").textContent =
        state.q || state.genre || state.letter
          ? "Try a different title or clear the filters."
          : "Add an index channel with /catalog so its anime list shows up here.";
      return;
    }
    els.grid.innerHTML = items.map(cardHtml).join("");
    els.grid.querySelectorAll(".card").forEach((el) => {
      const item = items.find((i) => String(i.id) === el.dataset.id);
      el.addEventListener("click", () => openDetail(item));
    });
  }

  function renderGenres() {
    els.genres.innerHTML =
      `<button class="chip ${state.genre ? "" : "active"}" data-genre="">All</button>` +
      state.genres.map((g) =>
        `<button class="chip ${state.genre === g.name ? "active" : ""}" data-genre="${escapeHtml(g.name)}">${escapeHtml(g.name)}</button>`
      ).join("");
    els.genres.querySelectorAll(".chip").forEach((chip) =>
      chip.addEventListener("click", () => {
        state.genre = chip.dataset.genre || null;
        state.letter = null;
        renderGenres();
        renderLetters();
        load();
      })
    );
  }

  function renderLetters() {
    const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
    els.letters.innerHTML =
      `<button class="chip small ${state.letter ? "" : "active"}" data-letter="">All</button>` +
      letters.map((l) =>
        `<button class="chip small ${state.letter === l ? "active" : ""}" data-letter="${l}">${l}</button>`
      ).join("") +
      `<button class="chip small ${state.letter === "#" ? "active" : ""}" data-letter="#">#</button>`;
    els.letters.querySelectorAll(".chip").forEach((chip) =>
      chip.addEventListener("click", () => {
        state.letter = chip.dataset.letter || null;
        state.genre = null;
        renderGenres();
        renderLetters();
        load();
      })
    );
  }

  /* ------------------------------ Detail view ------------------------------ */
  function qualityChips(item) {
    const list = sortQualities(item.qualities);
    if (list.length) {
      return list.map((q) => `
        <button class="quality-btn" data-link="${escapeHtml(q.url)}">
          <span class="q-name">${escapeHtml(q.quality.toUpperCase())}</span>
          <span class="q-sub">${q.batch ? "Batch" : q.via_bot ? "via bot" : "Download"}</span>
        </button>`).join("");
    }
    // No per-quality links yet: show the fixed set every title carries.
    const tags = (item.quality_tags && item.quality_tags.length)
      ? item.quality_tags : ["480p", "720p", "1080p", "HD-RIP"];
    return tags.map((q) => `
      <div class="quality-btn static">
        <span class="q-name">${escapeHtml(String(q).toUpperCase())}</span>
        <span class="q-sub">Available</span>
      </div>`).join("");
  }

  function channelButtons(full) {
    const list = (full.channels && full.channels.length)
      ? full.channels
      : (full.channel_link ? [{ source: null, url: full.channel_link, kind: full.channel_kind }] : []);
    if (!list.length) return `<p class="hint">No channel link recorded for this title yet.</p>`;
    const multi = list.length > 1;
    return list.map((ch) => {
      const kind = ch.kind ? kindInfo(ch.kind) : null;
      const label = kind ? kind.label : "Open link";
      const title = multi && ch.source ? ch.source : label;
      return `
        <button class="channel-btn kind-${escapeHtml(ch.kind || "external")}" data-link="${escapeHtml(ch.url)}">
          <span class="ch-icon">${kind ? kind.icon : "🔗"}</span>
          <span class="ch-main">
            <span class="ch-title">${escapeHtml(title)}</span>
            <span class="ch-url">${escapeHtml(label)}</span>
          </span>
          <span class="ch-go">›</span>
        </button>`;
    }).join("");
  }

  function detailMetaRow(full) {
    const pills = [];
    if (full.score) pills.push(`<span class="pill gold">★ ${Number(full.score).toFixed(1)}</span>`);
    if (full.year) pills.push(`<span class="pill">${full.year}</span>`);
    if (full.status) pills.push(`<span class="pill ${statusClass(full.status)}">${escapeHtml(full.status)}</span>`);
    if (full.episodes) pills.push(`<span class="pill">${full.episodes} EP</span>`);
    return pills.join("");
  }

  function seasonPosterStrip(seasons) {
    if (!seasons || seasons.length < 2) return "";
    return `
      <p class="section-label">Seasons</p>
      <div class="season-strip">
        ${seasons.map((s, i) => `
          <button class="season-poster ${i === 0 ? "active" : ""}" data-season="${s.number}">
            <div class="season-img">${posterImg(s.poster_url, "S" + s.number)}</div>
            <span class="season-tag">Season ${String(s.number).padStart(2, "0")}</span>
          </button>`).join("")}
      </div>`;
  }

  function episodeList(season) {
    const eps = (season && season.episodes) || [];
    if (!eps.length) {
      return `<p class="hint">No episodes added yet for this season. The owner adds them from the bot with the episode message.</p>`;
    }
    return `
      <div class="episode-list">
        ${eps.map((ep) => `
          <button class="episode-row" data-link="${escapeHtml(ep.url)}">
            <span class="ep-index">${String(ep.number).padStart(2, "0")}</span>
            <span class="ep-main">
              <span class="ep-title">${escapeHtml(ep.title || "Episode " + String(ep.number).padStart(2, "0"))}</span>
              <span class="ep-sub">Tap to watch</span>
            </span>
            <span class="ep-play">▶</span>
          </button>`).join("")}
      </div>`;
  }

  function seasonDetails(season) {
    const quality = (season.quality_tags && season.quality_tags.length)
      ? season.quality_tags : ["480p", "720p", "1080p", "HD-RIP"];
    return `
      <div class="release-details">
        <span><b>Audio</b> ${escapeHtml(season.audio || "Japanese")}</span>
        <span><b>Subtitles</b> ${escapeHtml(season.subtitles || "English Sub")}</span>
        <span><b>Quality</b> ${quality.map((q) => escapeHtml(String(q))).join(" · ")}</span>
      </div>`;
  }

  function recommendationsHtml(items) {
    if (!items || !items.length) return "";
    return `
      <p class="section-label">Recommended</p>
      <div class="rail rec-rail">
        ${items.map(railCardHtml).join("")}
      </div>`;
  }

  async function openDetail(item) {
    if (!item) return;
    els.detail.hidden = false;
    els.detail.innerHTML = `<div class="detail-loading"><span class="spinner"></span></div>`;

    let full = item;
    try { full = await api("/api/entry/" + item.id); } catch (e) { /* fall back to card data */ }

    const title = full.title || full.raw_name;
    const bg = (full.banner_url || full.poster_url)
      ? `style="background-image:url('${escapeHtml(full.banner_url || full.poster_url)}')"`
      : "";
    const genres = (full.genres || "").split(",").map((g) => g.trim()).filter(Boolean);
    const seasons = (full.seasons && full.seasons.length)
      ? full.seasons
      : [{
          number: 1, episodes: [], audio: full.audio, subtitles: full.subtitles,
          quality_tags: full.quality_tags, poster_url: full.poster_url,
        }];
    const watchUrl = (full.channels && full.channels[0] && full.channels[0].url) || full.channel_link || "";

    els.detail.innerHTML = `
      <div class="detail-banner">
        <div class="hero-bg" ${bg}></div>
        <div class="hero-shade"></div>
        <button class="detail-close" id="detailClose">✕</button>
        <div class="detail-banner-body">
          <h2 class="detail-title">${escapeHtml(title)}</h2>
          <div class="detail-meta">${detailMetaRow(full)}</div>
          <button class="btn btn-primary watch-now" data-link="${escapeHtml(watchUrl)}">▶ Watch Now</button>
        </div>
      </div>
      <div class="detail-body">
        ${genres.length ? `<div class="detail-genres">${genres.map((g) => `<span class="genre-tag">${escapeHtml(g)}</span>`).join("")}</div>` : ""}
        <div class="info-line">
          ${full.studio ? `<span><b>Studio</b> ${escapeHtml(full.studio)}</span>` : ""}
          ${full.year ? `<span><b>Year</b> ${full.year}</span>` : ""}
          ${full.episode_count ? `<span><b>Episodes</b> ${full.episode_count}</span>` : ""}
        </div>
        ${full.synopsis ? `<p class="synopsis">${escapeHtml(full.synopsis)}</p>` : ""}

        <p class="section-label">Channel</p>
        ${channelButtons(full)}

        ${seasonPosterStrip(seasons)}
        <div id="seasonView"></div>

        <p class="section-label">Quality &amp; downloads</p>
        <div class="quality-grid">${qualityChips(full)}</div>

        ${recommendationsHtml(full.recommendations)}
      </div>`;

    const seasonView = document.getElementById("seasonView");
    const renderSeason = (number) => {
      const season = seasons.find((s) => s.number === number) || seasons[0];
      seasonView.innerHTML = `
        <p class="section-label">Season ${String(season.number).padStart(2, "0")} · Episodes</p>
        ${seasonDetails(season)}
        ${episodeList(season)}`;
      seasonView.querySelectorAll("[data-link]").forEach((el) =>
        el.addEventListener("click", () => openLink(el.dataset.link))
      );
      els.detail.querySelectorAll(".season-poster").forEach((el) =>
        el.classList.toggle("active", Number(el.dataset.season) === season.number)
      );
    };
    renderSeason(seasons[0].number);

    document.getElementById("detailClose").addEventListener("click", closeDetail);
    els.detail.querySelectorAll(".channel-btn, .quality-btn[data-link], .watch-now").forEach((el) =>
      el.addEventListener("click", () => openLink(el.dataset.link))
    );
    els.detail.querySelectorAll(".season-poster").forEach((el) =>
      el.addEventListener("click", () => renderSeason(Number(el.dataset.season)))
    );
    els.detail.querySelectorAll(".rec-rail .rail-card").forEach((el) => {
      el.addEventListener("click", () => {
        const rec = (full.recommendations || []).find((r) => String(r.id) === el.dataset.id);
        if (rec) { closeDetail(); setTimeout(() => openDetail(rec), 60); }
      });
    });
    document.body.style.overflow = "hidden";
  }

  function closeDetail() {
    els.detail.hidden = true;
    els.detail.innerHTML = "";
    document.body.style.overflow = "";
  }

  /* ------------------------------ Channels sheet ------------------------------ */
  async function openChannels() {
    try {
      const data = await api("/api/channels");
      state.channels = data.items || [];
    } catch (e) { toast("Could not load channels"); return; }

    els.sheet.hidden = false;
    els.sheet.innerHTML = `
      <div class="sheet-card">
        <div class="sheet-handle"></div>
        <h3>Indexed channels</h3>
        ${state.channels.length ? state.channels.map((c) => `
          <div class="sheet-row">
            <div>
              <div class="name">${escapeHtml(c.title)}</div>
              <div class="count">${c.post_count} post(s)${c.kind === "index" ? " · index" : c.rss_url ? " · RSS" : ""}</div>
            </div>
            <a href="${escapeHtml(c.link)}" data-link="${escapeHtml(c.link)}">Open ›</a>
          </div>`).join("") : `<p class="synopsis">No channels indexed yet. Add the bot to a channel as admin.</p>`}
      </div>`;
    els.sheet.addEventListener("click", (e) => {
      if (e.target === els.sheet) els.sheet.hidden = true;
    });
    els.sheet.querySelectorAll("a[data-link]").forEach((a) =>
      a.addEventListener("click", (e) => { e.preventDefault(); openLink(a.dataset.link); })
    );
  }

  /* ------------------------------ Data loading ------------------------------ */
  async function loadSections() {
    try {
      const data = await api("/api/sections?limit=14");
      state.popular = data.popular || [];
      state.ongoing = data.ongoing || [];
      renderHero(data.hero);
      renderRails();
    } catch (e) { /* rails are optional */ }
  }

  async function load() {
    state.loading = true;
    els.loader.hidden = false;
    const params = new URLSearchParams();
    if (state.q) params.set("q", state.q);
    if (state.genre) params.set("genre", state.genre);
    params.set("sort", state.sort);
    try {
      const data = await api("/api/catalog?" + params.toString());
      state.items = data.items || [];
      renderGrid();
    } catch (e) {
      els.loader.hidden = true;
      toast("Failed to load catalog");
    } finally {
      state.loading = false;
    }
  }

  /* ------------------------------ Events ------------------------------ */
  let debounce;
  els.search.addEventListener("input", (e) => {
    state.q = e.target.value.trim();
    els.clear.hidden = !state.q;
    clearTimeout(debounce);
    debounce = setTimeout(load, 300);
  });
  els.clear.addEventListener("click", () => {
    els.search.value = "";
    state.q = "";
    els.clear.hidden = true;
    load();
  });
  els.sort.addEventListener("change", (e) => {
    state.sort = e.target.value;
    load();
  });
  els.channelsBtn.addEventListener("click", openChannels);
  els.topBtn.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  els.hero.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-open]");
    if (!btn) return;
    const item = state.items.find((i) => String(i.id) === btn.dataset.open) ||
      state.popular.find((i) => String(i.id) === btn.dataset.open);
    if (item) openDetail(item);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDetail(); });

  /* ------------------------------ Boot ------------------------------ */
  (async () => {
    try {
      const g = await api("/api/genres");
      state.genres = (g.items || []).slice(0, 14);
      renderGenres();
    } catch (e) {}
    renderLetters();
    await Promise.all([loadSections(), load()]);
  })();
})();
