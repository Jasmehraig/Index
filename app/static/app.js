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
    const letter = (title.trim()[0] || "?").toUpperCase();
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
      if (tg && tg.openTelegramLink && /^https:\/\/t\.me\//.test(url)) tg.openTelegramLink(url);
      else if (tg && tg.openLink) tg.openLink(url);
      else window.open(url, "_blank");
    } catch (e) { window.open(url, "_blank"); }
  };

  /* ------------------------------ Rendering ------------------------------ */
  function cardHtml(item) {
    const poster = item.poster_url
      ? `<img loading="lazy" src="${escapeHtml(item.poster_url)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'fallback',textContent:'${escapeHtml((item.title[0]||'?').toUpperCase())}'}))" />`
      : placeholder(item.title);
    const badge = item.episodes ? `<span class="badge">${item.episodes} EP</span>` : "";
    const score = item.score ? `<span class="score">★ ${Number(item.score).toFixed(1)}</span>` : "";
    const sub = [item.year, item.channel_count ? item.channel_count + " ch" : null].filter(Boolean).join(" · ");
    return `
      <article class="card" data-id="${item.id}">
        <div class="poster">${poster}${badge}${score}</div>
        <div class="card-info">
          <h3 class="card-title">${escapeHtml(item.title)}</h3>
          <div class="card-sub"><span>${escapeHtml(item.status || "")}</span><span>${escapeHtml(sub)}</span></div>
        </div>
      </article>`;
  }

  function renderHero(item) {
    if (!item) { els.hero.hidden = true; return; }
    const bg = item.poster_url ? `style="background-image:url('${escapeHtml(item.poster_url)}')"` : "";
    els.hero.hidden = false;
    els.hero.innerHTML = `
      <div class="hero-bg" ${bg}></div>
      <div class="hero-shade"></div>
      <div class="hero-body">
        <span class="hero-tag">Featured · Now indexed</span>
        <h2 class="hero-title">${escapeHtml(item.title)}</h2>
        <div class="hero-meta">
          ${item.score ? `<b>★ ${Number(item.score).toFixed(1)}</b>` : ""}
          ${item.year ? `<span>${item.year}</span>` : ""}
          ${item.episodes ? `<span>${item.episodes} episodes</span>` : ""}
          ${item.channel_count ? `<span>${item.channel_count} channel(s)</span>` : ""}
        </div>
        <button class="btn btn-primary" data-open="${item.id}">▶ Watch now</button>
      </div>`;
  }

  function renderGrid() {
    els.loader.hidden = true;
    const items = state.items;
    els.empty.hidden = items.length > 0;
    if (!items.length) {
      els.grid.innerHTML = "";
      els.hero.hidden = true;
      els.empty.querySelector("h2").textContent = state.q || state.genre
        ? "No matches"
        : "Nothing indexed yet";
      els.empty.querySelector("p").textContent = state.q || state.genre
        ? "Try a different title or clear the filters."
        : "Add the bot to an anime channel as an admin and its posts will show up here automatically.";
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
  function openDetail(item) {
    if (!item) return;
    const posts = item.posts || (item.latest_post ? [item.latest_post] : []);
    const bg = item.poster_url ? `style="background-image:url('${escapeHtml(item.poster_url)}')"` : "";
    const poster = item.poster_url
      ? `<img src="${escapeHtml(item.poster_url)}" alt="" />`
      : placeholder(item.title);
    const genres = (item.genres || "").split(",").map((g) => g.trim()).filter(Boolean);

    els.detail.hidden = false;
    els.detail.innerHTML = `
      <div class="detail-banner">
        <div class="hero-bg" ${bg}></div>
        <div class="hero-shade"></div>
        <button class="detail-close" id="detailClose">✕</button>
      </div>
      <div class="detail-head">
        <div class="detail-poster">${poster}</div>
        <div class="detail-titlebox">
          <h2 class="detail-title">${escapeHtml(item.title)}</h2>
          <div class="detail-meta">
            ${item.score ? `<span class="pill gold">★ ${Number(item.score).toFixed(1)}</span>` : ""}
            ${item.year ? `<span class="pill">${item.year}</span>` : ""}
            ${item.episodes ? `<span class="pill">${item.episodes} EP</span>` : ""}
            ${item.status ? `<span class="pill">${escapeHtml(item.status)}</span>` : ""}
          </div>
        </div>
      </div>
      <div class="detail-body">
        ${genres.length ? `<div class="detail-genres">${genres.map((g) => `<span class="genre-tag">${escapeHtml(g)}</span>`).join("")}</div>` : ""}
        ${item.synopsis ? `<p class="synopsis">${escapeHtml(item.synopsis)}</p>` : ""}
        <p class="section-label">${posts.length} post(s) in channels</p>
        <div id="postList">
          ${posts.map((p) => `
            <div class="post-row" data-link="${escapeHtml(p.link)}">
              <div class="post-icon">${p.is_direct_link ? "🔗" : "▶"}</div>
              <div class="post-main">
                <p class="post-channel">${escapeHtml(p.channel.title)}</p>
                <span class="post-ep">${escapeHtml(p.is_direct_link ? (p.caption || "Open link") : (p.episode_hint || "Open post"))}</span>
              </div>
              <span class="post-go">›</span>
            </div>`).join("") || `<p class="synopsis">No channel posts matched yet.</p>`}
        </div>
      </div>`;

    document.getElementById("detailClose").addEventListener("click", closeDetail);
    els.detail.querySelectorAll(".post-row").forEach((row) =>
      row.addEventListener("click", () => openLink(row.dataset.link))
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
      const data = await api("/api/home?" + params.toString());
      state.items = data.items || [];
      renderGrid();
    } catch (e) {
      els.loader.hidden = true;
      toast("Failed to load index");
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
