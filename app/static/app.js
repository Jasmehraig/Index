(() => {
"use strict";

const tg = window.Telegram?.WebApp;
if (tg) {
  try { tg.ready(); tg.expand(); tg.setHeaderColor("#08090d"); tg.setBackgroundColor("#08090d"); } catch(e){}
}

const $ = (s, p=document) => p.querySelector(s);
const $$ = (s, p=document) => [...p.querySelectorAll(s)];
// Storage is user- and version-controlled, so it can hold anything (a value
// written by an older build, a half-written string, a WebView quirk). A raw
// JSON.parse would throw and abort the whole script, leaving a blank app.
const loadFavorites = () => {
  try {
    const raw = localStorage.getItem("anime_index_favorites");
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch(e){ return [] }
};
const saveFavorites = ids => {
  try { localStorage.setItem("anime_index_favorites", JSON.stringify(ids)); } catch(e){}
};
const state = {
  q:"", genre:null, letter:null, sort:"title",
  items:[], genres:[], popular:[], ongoing:[], channels:[],
  favorites: loadFavorites(),
  heroIndex:0, heroTimer:null
};

const api = async path => {
  const r = await fetch(path, {headers:{Accept:"application/json"}});
  if(!r.ok) throw new Error("HTTP "+r.status);
  return r.json();
};
const esc = (v="") => String(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const titleOf = x => x?.title || x?.raw_name || "Unknown Anime";
const initial = t => esc(String(t).trim()[0] || "?").toUpperCase();
const img = (url,t) => url
  ? `<img loading="lazy" referrerpolicy="no-referrer" src="${esc(url)}" alt="" onerror="this.outerHTML='<div class=\\'fallback\\'>${initial(t)}</div>'">`
  : `<div class="fallback">${initial(t)}</div>`;
const statusClass = s => {
  s=(s||"").toLowerCase();
  if(s.includes("air")||s.includes("releas")||s.includes("ongoing")) return "airing";
  if(s.includes("finish")) return "finished";
  if(s.includes("upcoming")||s.includes("not yet")) return "upcoming";
  return "";
};
const toast = m => {
  const e=$("#toast"); e.textContent=m; e.classList.add("show");
  clearTimeout(e._t); e._t=setTimeout(()=>e.classList.remove("show"),2200);
};
const openLink = url => {
  if(!url) return toast("No link available");
  try {
    if(tg?.openTelegramLink && /^https?:\/\/(t\.me|telegram\.me)\//.test(url)) tg.openTelegramLink(url);
    else if(tg?.openLink) tg.openLink(url); else window.open(url,"_blank");
  } catch(e){window.open(url,"_blank")}
};
const isFav = id => state.favorites.includes(String(id));
const toggleFav = id => {
  id=String(id);
  state.favorites=isFav(id)?state.favorites.filter(x=>x!==id):[...state.favorites,id];
  saveFavorites(state.favorites);
  updateFavCount(); toast(isFav(id)?"Added to My List":"Removed from My List");
};
const updateFavCount=()=>{$("#favCount").textContent=state.favorites.length};

function railCard(x){
  const t=titleOf(x);
  return `<article class="rail-card" data-id="${esc(x.id)}"><div class="rail-poster">${img(x.poster_url,t)}${x.score?`<span class="rail-score">★ ${Number(x.score).toFixed(1)}</span>`:""}<span class="rail-play">▶</span></div><h4 class="rail-title">${esc(t)}</h4></article>`;
}
function card(x){
  const t=titleOf(x), st=statusClass(x.status);
  return `<article class="card" data-id="${esc(x.id)}"><div class="poster">${img(x.poster_url,t)}<div class="poster-fade"></div>${x.episode_count?`<span class="badge">${x.episode_count} EP</span>`:""}${x.score?`<span class="score">★ ${Number(x.score).toFixed(1)}</span>`:""}${st?`<span class="status-dot ${st}"></span>`:""}</div><div class="card-info"><h3 class="card-title">${esc(t)}</h3><div class="card-sub"><span>${esc(x.year||"")}</span>${x.season_count>1?`<span>${x.season_count} seasons</span>`:""}</div></div></article>`;
}

function renderHero(x){
  const h=$("#hero"); if(!x){h.hidden=true;return}
  const t=titleOf(x), genres=(x.genres||"").split(",").map(s=>s.trim()).filter(Boolean).slice(0,3);
  h.hidden=false;
  h.innerHTML=`<div class="hero-bg" style="background-image:url('${esc(x.banner_url||x.poster_url||"")}')"></div>
  <div class="hero-content"><span class="hero-kicker">FEATURED ANIME • ${esc(x.status||"CATALOG")}</span>
  <h1 class="hero-title">${esc(t)}</h1><p class="hero-desc">${esc(x.synopsis||"Discover episodes, seasons, quality links and source channels in one place.")}</p>
  <div class="hero-meta">${x.score?`<span class="meta rating">★ ${Number(x.score).toFixed(1)}</span>`:""}${x.year?`<span class="meta">${x.year}</span>`:""}${x.episodes?`<span class="meta">${x.episodes} Episodes</span>`:""}${genres.map(g=>`<span class="meta">${esc(g)}</span>`).join("")}</div>
  <div class="hero-actions"><button class="primary-btn" data-open="${esc(x.id)}">▶ Watch now</button><button class="ghost-btn" data-fav="${esc(x.id)}">${isFav(x.id)?"♥":"♡"} My List</button></div></div>`;
  $("[data-open]",h).onclick=()=>openDetail(x);
  $("[data-fav]",h).onclick=()=>{toggleFav(x.id);renderHero(x)};
}

function renderRail(block,rail,items){
  if(!items?.length){block.hidden=true;return}
  block.hidden=false; rail.innerHTML=items.map(railCard).join("");
  $$(".rail-card",rail).forEach(e=>e.onclick=()=>openDetail(items.find(x=>String(x.id)===e.dataset.id)));
}
function renderGenres(){
  $("#genreBar").innerHTML=`<button class="chip ${!state.genre?"active":""}" data-g="">All</button>`+
    state.genres.map(g=>`<button class="chip ${state.genre===g.name?"active":""}" data-g="${esc(g.name)}">${esc(g.name)}</button>`).join("");
  $$(".chip",$("#genreBar")).forEach(b=>b.onclick=()=>{state.genre=b.dataset.g||null;state.letter=null;renderGenres();load()});
}
function renderLetters(){
  const letters="ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
  $("#letterBar").innerHTML=`<button class="chip ${!state.letter?"active":""}" data-l="">All</button>`+
    letters.map(l=>`<button class="chip ${state.letter===l?"active":""}" data-l="${l}">${l}</button>`).join("")+
    `<button class="chip ${state.letter==="#"?"active":""}" data-l="#">#</button>`;
  $$(".chip",$("#letterBar")).forEach(b=>b.onclick=()=>{state.letter=b.dataset.l||null;state.genre=null;renderGenres();load()});
}
function renderGrid(){
  const items=state.items;
  $("#loader").hidden=true; $("#empty").hidden=items.length>0;
  $("#resultCount").textContent=`${items.length} title${items.length===1?"":"s"} indexed`;
  $("#grid").innerHTML=items.map(card).join("");
  $$(".card",$("#grid")).forEach(e=>e.onclick=()=>openDetail(items.find(x=>String(x.id)===e.dataset.id)));
}
async function loadSections(){
  try{
    const d=await api("/api/sections?limit=14");
    state.popular=d.popular||[];state.ongoing=d.ongoing||[];
    renderHero(d.hero);
    renderRail($("#popularBlock"),$("#popularRail"),state.popular);
    renderRail($("#ongoingBlock"),$("#ongoingRail"),state.ongoing);
  }catch(e){}
}
async function load(){
  $("#loader").hidden=false;
  const p=new URLSearchParams(); if(state.q)p.set("q",state.q);if(state.genre)p.set("genre",state.genre);p.set("sort",state.sort);
  p.set("limit","500");
  try{
    const d=await api("/api/catalog?"+p);
    let items=d.items||[];
    if(state.letter) items=items.filter(x=>{const c=titleOf(x).trim()[0]?.toUpperCase()||"#";return state.letter==="#"?!/[A-Z]/.test(c):c===state.letter});
    if(state.q||state.genre||state.letter) $("#catalogSection").scrollIntoView({behavior:"smooth",block:"start"});
    state.items=items;renderGrid();
  }catch(e){$("#loader").hidden=true;toast("Failed to load catalog")}
}

function detailQuality(full){
  const list=[...(full.qualities||[])].sort((a,b)=>String(a.quality).localeCompare(String(b.quality),undefined,{numeric:true}));
  const tags=list.length?list:(full.quality_tags||["480p","720p","1080p","HD-RIP"]).map(q=>({quality:q}));
  return tags.map(q=>`<button class="quality-btn" ${q.url?`data-link="${esc(q.url)}"`:""}><span class="q-name">${esc(String(q.quality).toUpperCase())}</span><span class="q-sub">${q.url?"Download":"Available"}</span></button>`).join("");
}
function channelButtons(full){
  const list=full.channels?.length?full.channels:(full.channel_link?[{url:full.channel_link,kind:full.channel_kind}] :[]);
  if(!list.length)return `<p class="synopsis">No channel link recorded yet.</p>`;
  return list.map(c=>`<button class="channel-btn" data-link="${esc(c.url)}"><span class="ch-icon">◫</span><span class="ch-main"><span class="ch-title">${esc(c.source||"Source Channel")}</span><span class="ch-url">${esc(c.url||"")}</span></span><span class="ch-go">›</span></button>`).join("");
}
function seasonPosterStrip(seasons){
  if(!seasons||seasons.length<2)return "";
  return `<p class="section-label">Seasons</p><div class="season-strip">${seasons.map((s,i)=>`<button class="season-poster ${i===0?"active":""}" data-season="${esc(s.number)}"><div class="season-img">${img(s.poster_url,"S"+s.number)}</div><span class="season-tag">Season ${String(s.number).padStart(2,"0")}</span></button>`).join("")}</div>`;
}
function seasonDetails(s){
  const q=(s.quality_tags&&s.quality_tags.length)?s.quality_tags:["480p","720p","1080p","HD-RIP"];
  return `<div class="release-details"><span><b>Audio</b> ${esc(s.audio||"Japanese")}</span><span><b>Subtitles</b> ${esc(s.subtitles||"English Sub")}</span><span><b>Quality</b> ${q.map(x=>esc(String(x))).join(" · ")}</span></div>`;
}
function episodeList(s){
  const eps=(s&&s.episodes)||[];
  if(!eps.length)return `<p class="synopsis">No episodes added yet for this season. The owner adds them from the bot with the episode message.</p>`;
  return `<div class="episode-list">${eps.map(ep=>`<button class="episode-row" data-link="${esc(ep.url)}"><span class="ep-index">${String(ep.number).padStart(2,"0")}</span><span class="ep-main"><span class="ep-title">${esc(ep.title||"Episode "+String(ep.number).padStart(2,"0"))}</span><span class="ep-sub">Tap to watch</span></span><span>▶</span></button>`).join("")}</div>`;
}
function recommendationsHtml(items){
  if(!items||!items.length)return "";
  return `<p class="section-label">Recommended</p><div class="rail rec-rail">${items.map(railCard).join("")}</div>`;
}
async function openDetail(item){
  if(!item)return;
  const d=$("#detail");d.hidden=false;document.body.style.overflow="hidden";
  d.innerHTML=`<div class="detail-hero"><div class="hero-bg" style="background-image:url('${esc(item.banner_url||item.poster_url||"")}')"></div><div class="hero-shade"></div><button class="circle-btn detail-close" id="detailClose">×</button><div class="detail-body detail-hero-inner">${item.poster_url?`<div class="hero-poster"><img src="${esc(item.poster_url)}" alt="" loading="lazy"></div>`:""}<div class="hero-copy"><h1 class="detail-title">${esc(titleOf(item))}</h1><div class="detail-meta">${item.score?`<span class="meta rating">★ ${Number(item.score).toFixed(1)}</span>`:""}${item.year?`<span class="meta">${item.year}</span>`:""}${item.status?`<span class="meta">${esc(item.status)}</span>`:""}${item.episode_count?`<span class="meta">${esc(item.episode_count)} EP</span>`:""}</div><div class="detail-actions"><button class="primary-btn" data-link="${esc(item.channel_link||"")}">▶ Watch now</button><button class="ghost-btn" id="favBtn">${isFav(item.id)?"♥":"♡"} My List</button></div></div></div></div><div class="detail-body"><div id="detailMain"><div class="loader"><span></span></div></div></div>`;
  $("#detailClose").onclick=closeDetail;
  $("#favBtn").onclick=()=>{toggleFav(item.id);$("#favBtn").innerHTML=`${isFav(item.id)?"♥":"♡"} My List`};
  $$(".detail [data-link]").forEach(e=>e.onclick=()=>openLink(e.dataset.link));

  let full=item;
  try{ full=await api("/api/entry/"+item.id); }catch(e){/* fall back to card data */}
  fetch("/api/entry/"+item.id+"/view",{method:"POST"}).catch(()=>{});

  const genres=(full.genres||"").split(",").map(g=>g.trim()).filter(Boolean);
  const seasons=(full.seasons&&full.seasons.length)?full.seasons:[{number:1,episodes:[],audio:full.audio,subtitles:full.subtitles,quality_tags:full.quality_tags,poster_url:full.poster_url}];
  const watch=(full.channels&&full.channels[0]&&full.channels[0].url)||full.channel_link||"";

  $("#detailMain").innerHTML=`
  <div class="detail-content"><div>
    ${genres.length?`<div class="detail-genres">${genres.map(g=>`<span class="genre-tag">${esc(g)}</span>`).join("")}</div>`:""}
    <p class="section-label">Information</p>
    <div class="info-box"><div class="info-line"><b>Studio</b>${esc(full.studio||"—")}</div><div class="info-line"><b>Year</b>${esc(full.year||"—")}</div><div class="info-line"><b>Episodes</b>${esc(full.episode_count||full.episodes||"—")}</div></div>
    ${full.synopsis?`<p class="section-label">Description</p><p class="synopsis detail-desc" id="descBox">${esc(full.synopsis)}</p><button class="desc-toggle" id="descToggle" hidden>Read more</button>`:""}
    ${seasonPosterStrip(seasons)}
    <div id="seasonView"></div>
    <p class="section-label">Quality & Downloads</p><div class="quality-grid">${detailQuality(full)}</div>
  </div>
  <aside>
    <p class="section-label">Source Channels</p>${channelButtons(full)}
  </aside></div>
  <div id="recArea">${recommendationsHtml(full.recommendations)}</div>`;
  $(".primary-btn[data-link]",$("#detailMain")).onclick=()=>openLink(watch)||0;

  const desc=$("#descBox");
  if(desc){
    const toggle=$("#descToggle");
    // -webkit-line-clamp can report scrollHeight == clientHeight, so also use
    // the text length as a fallback signal for "there is more to show".
    const long=(full.synopsis||"").length>240 || desc.scrollHeight>desc.clientHeight+2;
    toggle.hidden=!long;
    toggle.onclick=()=>{
      const open=desc.classList.toggle("expanded");
      toggle.textContent=open?"Show less":"Read more";
    };
  }

  const seasonView=$("#seasonView");
  const renderSeason=(number)=>{
    const s=seasons.find(x=>String(x.number)===String(number))||seasons[0];
    seasonView.innerHTML=`<p class="section-label">Season ${String(s.number).padStart(2,"0")} · Episodes</p>${seasonDetails(s)}${episodeList(s)}`;
    $$("#seasonView [data-link]").forEach(e=>e.onclick=()=>openLink(e.dataset.link));
    $$(".season-poster").forEach(e=>e.classList.toggle("active",String(e.dataset.season)===String(s.number)));
  };
  renderSeason(seasons[0].number);

  $$(".season-poster").forEach(e=>e.onclick=()=>renderSeason(e.dataset.season));
  $$("#recArea .rec-rail .rail-card").forEach(e=>e.onclick=()=>{
    const rec=(full.recommendations||[]).find(r=>String(r.id)===e.dataset.id);
    if(rec){closeDetail();setTimeout(()=>openDetail(rec),60)}
  });
}
function closeDetail(){$("#detail").hidden=true;$("#detail").innerHTML="";document.body.style.overflow=""}

async function openChannels(){
  try{const d=await api("/api/channels");state.channels=d.items||[]}catch(e){return toast("Could not load channels")}
  const s=$("#sheet");s.hidden=false;
  s.innerHTML=`<div class="sheet-card"><div class="sheet-handle"></div><h3>Indexed Channels</h3>${state.channels.length?state.channels.map(c=>`<div class="sheet-row"><div><div class="name">${esc(c.title)}</div><div class="count">${c.post_count||0} posts${c.kind==="index"?" · index":""}</div></div><a href="#" data-link="${esc(c.link)}">Open ›</a></div>`).join(""):`<p class="synopsis">No channels indexed yet.</p>`}</div>`;
  $$("#sheet [data-link]").forEach(a=>a.onclick=e=>{e.preventDefault();openLink(a.dataset.link)});
  s.onclick=e=>{if(e.target===s)s.hidden=true};
}

function nav(where){
  $$(".nav-item,.mobile-nav button").forEach(x=>x.classList.toggle("active",x.dataset.nav===where));
  if(where==="home")window.scrollTo({top:0,behavior:"smooth"});
  if(where==="popular")$("#popularBlock").scrollIntoView({behavior:"smooth"});
  if(where==="ongoing")$("#ongoingBlock").scrollIntoView({behavior:"smooth"});
  if(where==="catalog")$("#catalogSection").scrollIntoView({behavior:"smooth"});
  if(where==="favorites"){
    const fav=state.items.filter(x=>isFav(x.id));
    state.items=fav;renderGrid();$("#catalogSection").scrollIntoView({behavior:"smooth"});
    if(!fav.length)toast("Your My List is empty");
  }
  $("#sidebar").classList.remove("open");$("#sidebarShade").classList.remove("open");
}

$("#menuBtn")?.addEventListener("click",()=>{$("#sidebar").classList.add("open");$("#sidebarShade").classList.add("open")});
$("#closeSidebar")?.addEventListener("click",()=>nav("home"));
$("#sidebarShade").onclick=()=>nav("home");
$("#channelsBtn").onclick=openChannels;
$("#searchFocus").onclick=()=>{$("#searchInput").focus();window.scrollTo({top:0,behavior:"smooth"})};
$("#clearBtn").onclick=()=>{$("#searchInput").value="";state.q="";$("#clearBtn").hidden=true;load()};
$("#searchInput").oninput=e=>{state.q=e.target.value.trim();$("#clearBtn").hidden=!state.q;clearTimeout(window._search);window._search=setTimeout(load,300)};
$("#sortSelect").onchange=e=>{state.sort=e.target.value;load()};
$("#filterBtn").onclick=()=>{$("#genreBar").scrollIntoView({behavior:"smooth",block:"center"});toast("Choose a genre above")};
$$("[data-nav]").forEach(e=>e.onclick=()=>nav(e.dataset.nav));
document.addEventListener("keydown",e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==="k"){e.preventDefault();$("#searchInput").focus()}if(e.key==="Escape")closeDetail()});
$("#themeBtn").onclick=()=>{document.body.classList.toggle("light");toast("Theme preference saved")};

(async()=>{
  updateFavCount();
  try{const g=await api("/api/genres");state.genres=(g.items||[]).slice(0,20)}catch(e){}
  renderGenres();renderLetters();
  await Promise.all([loadSections(),load()]);
})();
})();