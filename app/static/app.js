(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    try { tg.setHeaderColor("#090a0f"); tg.setBackgroundColor("#090a0f"); } catch (_) {}
  }

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const state = {
    catalog: [],
    home: [],
    genres: [],
    channels: [],
    view: "home",
    query: "",
    genre: null
  };

  const api = async (url) => {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  };

  const esc = (v = "") => String(v).replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));

  const img = (item, cls = "") => {
    const title = item?.title || item?.raw_name || "?";
    if (!item?.poster_url) return `<div class="poster-fallback ${cls}">${esc(title[0]?.toUpperCase() || "?")}</div>`;
    return `<img class="${cls}" loading="lazy" src="${esc(item.poster_url)}" alt="" onerror="this.outerHTML='<div class=&quot;poster-fallback ${cls}&quot;>${esc(title[0]?.toUpperCase() || "?")}</div>'">`;
  };

  const openLink = (url) => {
    if (!url) return;
    try {
      if (tg?.openTelegramLink && /^https?:\/\/(t\.me|telegram\.me)\//.test(url)) tg.openTelegramLink(url);
      else if (tg?.openLink) tg.openLink(url);
      else window.open(url, "_blank");
    } catch (_) { window.open(url, "_blank"); }
  };

  const toast = (message) => {
    const el = $("#toast");
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("show"), 2200);
  };

  const score = (x) => Number(x?.score || 0);
  const title = (x) => x?.title || x?.raw_name || "Unknown";
  const isOngoing = (x) => {
    const s = String(x?.status || "").toLowerCase();
    return s.includes("airing") || s.includes("ongoing") || s.includes("currently");
  };

  function card(item, compact = false) {
    const t = title(item);
    const meta = [
      item.year,
      item.episodes ? `${item.episodes} EP` : null,
      score(item) ? `★ ${score(item).toFixed(1)}` : null
    ].filter(Boolean).join(" · ");
    const channels = Number(item.channel_count || 0);
    return `
      <article class="anime-card ${compact ? "compact" : ""}" data-id="${item.id}">
        <div class="poster-wrap">
          ${img(item)}
          ${item.episodes ? `<span class="ep-badge">${esc(item.episodes)} EP</span>` : ""}
          ${score(item) ? `<span class="score-badge">★ ${score(item).toFixed(1)}</span>` : ""}
        </div>
        <div class="card-copy">
          <h3>${esc(t)}</h3>
          <p>${esc(meta || (channels ? `${channels} channel${channels > 1 ? "s" : ""}` : "Anime"))}</p>
        </div>
      </article>`;
  }

  function renderHero(item) {
    const el = $("#hero");
    if (!item) {
      el.innerHTML = `<div class="hero-empty"><span>INDEX</span><h1>Your anime library starts here.</h1><p>Add or refresh your Telegram index channel to populate the library.</p></div>`;
      return;
    }
    const t = title(item);
    const bg = item.banner_url || item.poster_url || "";
    el.innerHTML = `
      <div class="hero-bg" style="background-image:url('${esc(bg)}')"></div>
      <div class="hero-overlay"></div>
      <div class="hero-content">
        <span class="hero-label">FEATURED ANIME</span>
        <h1>${esc(t)}</h1>
        <div class="hero-meta">
          ${score(item) ? `<b>★ ${score(item).toFixed(1)}</b>` : ""}
          ${item.year ? `<span>${item.year}</span>` : ""}
          ${item.episodes ? `<span>${item.episodes} episodes</span>` : ""}
          ${item.status ? `<span>${esc(item.status)}</span>` : ""}
        </div>
        <p>${esc(item.synopsis || "Explore this title in your indexed Telegram library.")}</p>
        <button class="primary-btn" data-id="${item.id}">View Anime <b>→</b></button>
      </div>`;
    el.querySelector(".primary-btn")?.addEventListener("click", () => openDetail(item.id));
  }

  function renderRail(id, items) {
    const el = $(`#${id}`);
    el.innerHTML = items.length ? items.map(x => card(x, true)).join("") :
      `<div class="rail-empty">No titles available yet.</div>`;
    el.querySelectorAll(".anime-card").forEach(c => c.addEventListener("click", () => openDetail(c.dataset.id)));
  }

  function renderGenres() {
    const el = $("#genreGrid");
    const list = state.genres.slice(0, 18);
    el.innerHTML = list.length ? list.map(g => `
      <button class="genre-card" data-genre="${esc(g.name)}">
        <span>${esc(g.name)}</span><small>${g.count || 0} titles</small>
      </button>`).join("") : `<div class="rail-empty">Genres appear after metadata is indexed.</div>`;
    el.querySelectorAll("[data-genre]").forEach(b => b.addEventListener("click", () => {
      state.genre = b.dataset.genre;
      showView("catalog");
      loadCatalog();
    }));
  }

  function renderHome() {
    const popular = [...state.catalog].sort((a,b) => score(b)-score(a) || title(a).localeCompare(title(b)));
    const ongoing = state.catalog.filter(isOngoing).sort((a,b) => score(b)-score(a));
    const latest = [...state.home].sort((a,b) =>
      new Date(b.latest_post?.posted_at || 0) - new Date(a.latest_post?.posted_at || 0));

    renderHero(popular[0] || state.catalog[0]);
    renderRail("popularRail", popular.slice(0, 12));
    renderRail("ongoingRail", ongoing.slice(0, 12));
    renderRail("latestRail", latest.slice(0, 12));
    renderGenres();

    $("#ongoingSection").style.display = ongoing.length ? "" : "none";
  }

  function showView(view) {
    state.view = view;
    $$(".view").forEach(v => v.classList.remove("active"));
    const target = view === "home" ? "#homeView" :
      view === "catalog" ? "#catalogView" :
      view === "channels" ? "#channelView" : "#listView";
    $(target).classList.add("active");

    $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.view === view));
    window.scrollTo({ top: 0, behavior: "smooth" });

    if (view === "popular") renderList(
      "Most Popular", "DISCOVER", "Top-rated anime in your indexed library.",
      [...state.catalog].sort((a,b) => score(b)-score(a))
    );
    if (view === "ongoing") renderList(
      "Ongoing Anime", "UPDATING", "Titles currently marked as airing/ongoing.",
      state.catalog.filter(isOngoing).sort((a,b) => score(b)-score(a))
    );
    if (view === "channels") renderChannels();
  }

  function renderList(head, eyebrow, desc, items) {
    $("#listTitle").textContent = head;
    $("#listEyebrow").textContent = eyebrow;
    $("#listDescription").textContent = desc;
    $("#listGrid").innerHTML = items.length ? items.map(x => card(x)).join("") :
      `<div class="empty-state"><h2>No anime here yet</h2><p>Once your bot indexes more data, this section will populate automatically.</p></div>`;
    $("#listGrid").querySelectorAll(".anime-card").forEach(c => c.addEventListener("click", () => openDetail(c.dataset.id)));
  }

  function renderFilters() {
    const el = $("#catalogFilters");
    el.innerHTML = `
      <button class="filter ${!state.genre ? "active" : ""}" data-clear>All</button>
      ${state.genres.slice(0, 12).map(g => `<button class="filter ${state.genre === g.name ? "active":""}" data-filter="${esc(g.name)}">${esc(g.name)}</button>`).join("")}
    `;
    el.querySelector("[data-clear]")?.addEventListener("click", () => { state.genre = null; renderFilters(); loadCatalog(); });
    el.querySelectorAll("[data-filter]").forEach(b => b.addEventListener("click", () => {
      state.genre = b.dataset.filter; renderFilters(); loadCatalog();
    }));
  }

  async function loadCatalog() {
    $("#catalogLoader").classList.add("show");
    const params = new URLSearchParams();
    if (state.query) params.set("q", state.query);
    if (state.genre) params.set("genre", state.genre);
    try {
      const data = await api("/api/catalog?" + params);
      const items = data.items || [];
      $("#catalogGrid").innerHTML = items.length ? items.map(x => card(x)).join("") :
        `<div class="empty-state"><h2>No matches</h2><p>Try another title or genre.</p></div>`;
      $("#catalogGrid").querySelectorAll(".anime-card").forEach(c => c.addEventListener("click", () => openDetail(c.dataset.id)));
    } catch (_) {
      toast("Could not load the catalog");
    } finally {
      $("#catalogLoader").classList.remove("show");
    }
  }

  function renderChannels() {
    const el = $("#channelGrid");
    el.innerHTML = state.channels.length ? state.channels.map(c => `
      <article class="channel-card">
        <div class="channel-icon">◫</div>
        <div><h3>${esc(c.title)}</h3><p>${c.post_count || 0} posts${c.kind ? ` · ${esc(c.kind)}` : ""}</p></div>
        <button data-link="${esc(c.link)}">Open →</button>
      </article>`).join("") :
      `<div class="empty-state"><h2>No channels indexed</h2><p>Add the bot to a Telegram channel and refresh the index.</p></div>`;
    el.querySelectorAll("[data-link]").forEach(b => b.addEventListener("click", () => openLink(b.dataset.link)));
  }

  async function openDetail(id) {
    const modal = $("#detail");
    modal.hidden = false;
    modal.innerHTML = `<div class="detail-loading"><span></span></div>`;
    document.body.classList.add("locked");

    let item;
    try { item = await api("/api/entry/" + encodeURIComponent(id)); }
    catch (_) {
      item = state.catalog.find(x => String(x.id) === String(id));
    }
    if (!item) { modal.hidden = true; document.body.classList.remove("locked"); return; }

    const genres = String(item.genres || "").split(",").map(x => x.trim()).filter(Boolean);
    const channels = item.channels?.length ? item.channels :
      (item.channel_link ? [{source:"", url:item.channel_link, kind:item.channel_kind}] : []);
    const qualities = item.qualities || [];

    modal.innerHTML = `
      <div class="detail-backdrop" style="background-image:url('${esc(item.banner_url || item.poster_url || "")}')"></div>
      <div class="detail-panel">
        <button class="detail-close" id="detailClose">×</button>
        <div class="detail-top">
          <div class="detail-poster">${img(item)}</div>
          <div class="detail-heading">
            <span class="eyebrow">ANIME DETAILS</span>
            <h1>${esc(title(item))}</h1>
            <div class="detail-meta">
              ${score(item) ? `<span>★ ${score(item).toFixed(1)}</span>` : ""}
              ${item.year ? `<span>${item.year}</span>` : ""}
              ${item.episodes ? `<span>${item.episodes} EP</span>` : ""}
              ${item.status ? `<span>${esc(item.status)}</span>` : ""}
            </div>
          </div>
        </div>
        ${genres.length ? `<div class="detail-tags">${genres.map(g => `<span>${esc(g)}</span>`).join("")}</div>` : ""}
        ${item.synopsis ? `<p class="detail-synopsis">${esc(item.synopsis)}</p>` : ""}
        ${item.note ? `<div class="detail-note">${esc(item.note)}</div>` : ""}

        <h3 class="detail-section-title">Available Sources</h3>
        <div class="source-list">
          ${channels.length ? channels.map(ch => `
            <button class="source-btn" data-link="${esc(ch.url)}">
              <span>◫</span><div><b>${esc(ch.source || "Telegram Source")}</b><small>${esc(ch.kind || "Open source")}</small></div><i>→</i>
            </button>`).join("") : `<p class="muted">No source link has been attached yet.</p>`}
        </div>

        ${qualities.length ? `
          <h3 class="detail-section-title">Quality / Downloads</h3>
          <div class="quality-list">
            ${qualities.map(q => `<button class="quality-btn" data-link="${esc(q.url)}"><b>${esc((q.quality || "LINK").toUpperCase())}</b><small>${q.batch ? "Batch" : q.via_bot ? "File Bot" : "Download"}</small></button>`).join("")}
          </div>` : ""}
      </div>`;

    $("#detailClose").addEventListener("click", closeDetail);
    modal.querySelectorAll("[data-link]").forEach(b => b.addEventListener("click", () => openLink(b.dataset.link)));
    modal.addEventListener("click", e => { if (e.target === modal) closeDetail(); }, { once: true });
  }

  function closeDetail() {
    $("#detail").hidden = true;
    $("#detail").innerHTML = "";
    document.body.classList.remove("locked");
  }

  async function boot() {
    try {
      const [catalog, home, genres, channels] = await Promise.all([
        api("/api/catalog?limit=500"),
        api("/api/home?limit=500"),
        api("/api/genres"),
        api("/api/channels")
      ]);
      state.catalog = catalog.items || [];
      state.home = home.items || [];
      state.genres = genres.items || [];
      state.channels = channels.items || [];
      renderHome();
      renderFilters();
    } catch (_) {
      toast("Index could not load the library");
    }
  }

  $$(".nav-item,[data-view]").forEach(el => el.addEventListener("click", e => {
    const v = el.dataset.view;
    if (!v) return;
    e.preventDefault();
    showView(v);
  }));

  $("#channelsBtn").addEventListener("click", () => showView("channels"));

  let searchTimer;
  $("#searchInput").addEventListener("input", e => {
    state.query = e.target.value.trim();
    $("#clearSearch").hidden = !state.query;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      if (!state.query) { $("#searchResults").hidden = true; return; }
      try {
        const data = await api("/api/catalog?q=" + encodeURIComponent(state.query) + "&limit=12");
        const items = data.items || [];
        const box = $("#searchResults");
        box.hidden = false;
        box.innerHTML = items.length ? items.map(x => `<button data-id="${x.id}">${img(x,"search-poster")}<span><b>${esc(title(x))}</b><small>${esc(x.year || "")}${x.score ? ` · ★ ${score(x).toFixed(1)}` : ""}</small></span></button>`).join("") : `<p>No anime found.</p>`;
        box.querySelectorAll("[data-id]").forEach(b => b.addEventListener("click", () => { box.hidden = true; openDetail(b.dataset.id); }));
      } catch (_) {}
    }, 250);
  });

  $("#clearSearch").addEventListener("click", () => {
    $("#searchInput").value = ""; state.query = ""; $("#clearSearch").hidden = true; $("#searchResults").hidden = true;
  });

  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#searchInput").focus(); }
    if (e.key === "Escape") { $("#searchResults").hidden = true; if (!$("#detail").hidden) closeDetail(); }
  });

  boot();
})();