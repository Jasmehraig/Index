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
    detail: document.getElementById("detail"),
    sheet: document.getElementById("sheet"),
    channelsBtn: document.getElementById("channelsBtn"),
  };

  const state = { q: "", genre: null, items: [], genres: [], channels: [], loading: false };

  const api = async (path) => {
    const res = await fetch(path, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error("Request failed: " + res.status);
    return res.json();
  };

  const escapeHtml = (str = "") =>
    String(str).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const placeholder = (title = "") => {
    const letter = (String(title).trim()[0] || "?").toUpperCase();
    return `<div class="fallback">${escapeHtml(letter)}</div>`;
  };

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

  /* ------------------------------ Rendering ------------------------------ */
  function cardHtml(item) {
    const title = item.title || item.raw_name;
    const poster = item.poster_url
      ? `<img loading="lazy" src="${escapeHtml(item.poster_url)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'fallback',textContent:'${escapeHtml((title[0] || '?').toUpperCase())}'}))" />`
      : placeholder(title);
    const badge = item.episodes ? `<span class="badge">${item.episodes} EP</span>` : "";
    const score = item.score ? `<span class="score">★ ${Number(item.score).toFixed(1)}</span>` : "";
    const kind = item.channel_kind ? kindInfo(item.channel_kind) : null;
    const n = item.channel_count || (item.channel_link ? 1 : 0);
    const sub = [item.year, n > 1 ? `${n} channels` : (kind ? kind.label : null)].filter(Boolean).join(" · ");
    return `
      <article class="card" data-id="${item.id}">
        <div class="poster">${poster}${badge}${score}</div>
        <div class="card-info">
          <h3 class="card-title">${escapeHtml(title)}</h3>
          <div class="card-sub"><span>${escapeHtml(item.status || "")}</span><span>${escapeHtml(sub)}</span></div>
        </div>
      </article>`;
  }

  function renderHero(item) {
    if (!item) { els.hero.hidden = true; return; }
    const title = item.title || item.raw_name;
    const bg = item.poster_url ? `style="background-image:url('${escapeHtml(item.poster_url)}')"` : "";
    const kind = item.channel_kind ? kindInfo(item.channel_kind) : null;
    els.hero.hidden = false;
    els.hero.innerHTML = `
      <div class="hero-bg" ${bg}></div>
      <div class="hero-shade"></div>
      <div class="hero-body">
        <span class="hero-tag">Featured · Indexed</span>
        <h2 class="hero-title">${escapeHtml(title)}</h2>
        <div class="hero-meta">
          ${item.score ? `<b>★ ${Number(item.score).toFixed(1)}</b>` : ""}
          ${item.year ? `<span>${item.year}</span>` : ""}
          ${item.episodes ? `<span>${item.episodes} episodes</span>` : ""}
          ${kind ? `<span>${kind.icon} ${kind.label}</span>` : ""}
        </div>
        <button class="btn btn-primary" data-open="${item.id}">▶ Open</button>
      </div>`;
  }

  function renderGrid() {
    els.loader.hidden = true;
    const items = state.items;
    els.empty.hidden = items.length > 0;
    if (!items.length) {
      els.grid.innerHTML = "";
      els.hero.hidden = true;
      els.empty.querySelector("h2").textContent = state.q || state.genre ? "No matches" : "Nothing indexed yet";
      els.empty.querySelector("p").textContent = state.q || state.genre
        ? "Try a different title or clear the filters."
        : "Add an index channel with /import so its anime list shows up here.";
      return;
    }
    const [featured, ...rest] = items;
    renderHero(featured);
    els.grid.innerHTML = rest.map(cardHtml).join("");
    els.grid.querySelectorAll(".card").forEach((el, i) => {
      el.style.animationDelay = Math.min(i * 22, 300) + "ms";
      el.addEventListener("click", () => openDetail(items[i + 1]));
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
        renderGenres();
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
    return `<p class="hint">No quality links yet. Add one from the bot with
      <code>/quality ${item.id} 1080p &lt;link&gt;</code>, or send the files to your file-share bot
      and paste the links here.</p>`;
  }

  function channelButtons(full) {
    const list = (full.channels && full.channels.length)
      ? full.channels
      : (full.channel_link ? [{ source: null, url: full.channel_link, kind: full.channel_kind }] : []);
    if (!list.length) return `<p class="hint">No channel link recorded for this title yet.</p>`;
    const multi = list.length > 1;
    return list.map((ch, i) => {
      const kind = ch.kind ? kindInfo(ch.kind) : null;
      const label = kind ? kind.label : "Open link";
      // With several sources, name the index channel so the choice is obvious.
      const title = multi && ch.source ? ch.source : label;
      return `
        <button class="channel-btn kind-${escapeHtml(ch.kind || "external")}" data-link="${escapeHtml(ch.url)}">
          <span class="ch-icon">${kind ? kind.icon : "🔗"}</span>
          <span class="ch-main">
            <span class="ch-title">${escapeHtml(title)}</span>
            <span class="ch-url">${escapeHtml(multi ? label + " · " + ch.url : ch.url)}</span>
          </span>
          <span class="ch-go">›</span>
        </button>`;
    }).join("");
  }

  async function openDetail(item) {
    if (!item) return;
    els.detail.hidden = false;
    els.detail.innerHTML = `<div class="detail-loading"><span class="spinner"></span></div>`;

    let full = item;
    try { full = await api("/api/entry/" + item.id); } catch (e) { /* fall back to card data */ }

    const title = full.title || full.raw_name;
    const bg = full.poster_url ? `style="background-image:url('${escapeHtml(full.poster_url)}')"` : "";
    const poster = full.poster_url
      ? `<img src="${escapeHtml(full.poster_url)}" alt="" />`
      : placeholder(title);
    const genres = (full.genres || "").split(",").map((g) => g.trim()).filter(Boolean);
    const count = (full.channels && full.channels.length) || (full.channel_link ? 1 : 0);
    const kind = full.channel_kind ? kindInfo(full.channel_kind) : null;
    const chanLabel = count > 1 ? `${count} channels` : (kind ? kind.label : null);

    els.detail.innerHTML = `
      <div class="detail-banner">
        <div class="hero-bg" ${bg}></div>
        <div class="hero-shade"></div>
        <button class="detail-close" id="detailClose">✕</button>
      </div>
      <div class="detail-head">
        <div class="detail-poster">${poster}</div>
        <div class="detail-titlebox">
          <h2 class="detail-title">${escapeHtml(title)}</h2>
          <div class="detail-meta">
            ${full.score ? `<span class="pill gold">★ ${Number(full.score).toFixed(1)}</span>` : ""}
            ${full.year ? `<span class="pill">${full.year}</span>` : ""}
            ${full.episodes ? `<span class="pill">${full.episodes} EP</span>` : ""}
            ${full.status ? `<span class="pill">${escapeHtml(full.status)}</span>` : ""}
            ${chanLabel ? `<span class="pill">${escapeHtml(chanLabel)}</span>` : ""}
          </div>
        </div>
      </div>
      <div class="detail-body">
        ${genres.length ? `<div class="detail-genres">${genres.map((g) => `<span class="genre-tag">${escapeHtml(g)}</span>`).join("")}</div>` : ""}
        ${full.synopsis ? `<p class="synopsis">${escapeHtml(full.synopsis)}</p>` : ""}
        ${full.note ? `<p class="detail-note">${escapeHtml(full.note)}</p>` : ""}

        <p class="section-label">${(full.channels && full.channels.length > 1) ? `Available in ${full.channels.length} channels` : "Channel"}</p>
        ${channelButtons(full)}

        <p class="section-label">Quality &amp; downloads</p>
        <div class="quality-grid">${qualityChips(full)}</div>
      </div>`;

    document.getElementById("detailClose").addEventListener("click", closeDetail);
    els.detail.querySelectorAll("[data-link]").forEach((el) =>
      el.addEventListener("click", () => openLink(el.dataset.link))
    );
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
  async function load() {
    state.loading = true;
    els.loader.hidden = false;
    const params = new URLSearchParams();
    if (state.q) params.set("q", state.q);
    if (state.genre) params.set("genre", state.genre);
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
  els.channelsBtn.addEventListener("click", openChannels);
  els.hero.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-open]");
    if (btn) openDetail(state.items[0]);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDetail(); });

  /* ------------------------------ Boot ------------------------------ */
  (async () => {
    try {
      const g = await api("/api/genres");
      state.genres = (g.items || []).slice(0, 14);
      renderGenres();
    } catch (e) {}
    await load();
  })();
})();
