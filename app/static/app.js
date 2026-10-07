// State Management
const state = {
  animes: [],
  filtered: [],
  topSix: [],
  currentSlide: 0,
  sliderInterval: null,
  selectedCategory: 'all',
  selectedGenre: 'all',
  searchQuery: ''
};

// DOM Nodes
const nodes = {
  heroTrack: document.getElementById('hero-slides-track'),
  indicators: document.getElementById('slider-indicators'),
  animeGrid: document.getElementById('anime-grid'),
  genreContainer: document.getElementById('genre-container'),
  navButtons: document.querySelectorAll('.nav-item'),
  searchInput: document.getElementById('search-input'),
  resultsCount: document.getElementById('results-count'),
  loadingState: document.getElementById('loading-state'),
  emptyState: document.getElementById('empty-state'),
  modal: document.getElementById('details-modal'),
  modalContent: document.getElementById('modal-content'),
  modalClose: document.getElementById('modal-close'),
  menuToggle: document.getElementById('menu-toggle'),
  sidebar: document.getElementById('sidebar'),
  sidebarClose: document.getElementById('sidebar-close'),
  backdrop: document.getElementById('drawer-backdrop'),
  catalogHeading: document.getElementById('catalog-heading')
};

// Initialize App
document.addEventListener('DOMContentLoaded', async () => {
  setupSidebar();
  setupSearch();
  setupFilters();
  await fetchAnimeData();
});

// Fetch Anime Data from existing backend endpoints
async function fetchAnimeData() {
  nodes.loadingState.classList.remove('hidden');
  nodes.animeGrid.innerHTML = '';

  try {
    // Attempt catalog endpoint; fallback to items or search API if catalog is empty
    let res = await fetch('/api/catalog');
    if (!res.ok) res = await fetch('/api/search?q=');
    const data = await res.json();

    // Standardize catalog array structure
    state.animes = Array.isArray(data) ? data : (data.items || data.results || []);
    
    // Select Top 6 based on score / popularity / index
    state.topSix = [...state.animes]
      .sort((a, b) => (b.score || 0) - (a.score || 0))
      .slice(0, 6);

    buildHeroSlider();
    applyFilters();
  } catch (err) {
    console.error('Failed to load anime catalog:', err);
  } finally {
    nodes.loadingState.classList.add('hidden');
  }
}

// Build Left-to-Right Hero Slider
function buildHeroSlider() {
  if (!state.topSix.length) return;

  nodes.heroTrack.innerHTML = '';
  nodes.indicators.innerHTML = '';

  state.topSix.forEach((item, index) => {
    const poster = item.banner_url || item.image_url || item.thumbnail || 'https://images.unsplash.com/photo-1578632767115-351597cf2477?w=1200';
    const title = item.title || item.name || 'Featured Anime';
    const score = item.score ? `★ ${item.score}` : '★ 8.5';
    const type = item.type || item.format || 'TV Series';
    const synopsis = item.synopsis || item.description || 'Explore the complete series with original subtitles and high quality.';

    const slide = document.createElement('div');
    slide.className = 'hero-slide';
    slide.style.backgroundImage = `url('${poster}')`;
    slide.innerHTML = `
      <div class="hero-overlay"></div>
      <div class="hero-details">
        <span class="hero-rank-tag">TOP #${index + 1} ON SWORDSMITH</span>
        <h1 class="hero-title">${title}</h1>
        <div class="hero-meta">
          <span class="hero-score">${score}</span>
          <span>•</span>
          <span>${type}</span>
        </div>
        <p class="hero-synopsis">${synopsis}</p>
        <button class="btn-watch" onclick="openDetails('${item.id || item.anime_id || index}')">
          <svg width="20" height="20" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
          Watch Now
        </button>
      </div>
    `;
    nodes.heroTrack.appendChild(slide);

    // Indicator Dot
    const dot = document.createElement('div');
    dot.className = `indicator ${index === 0 ? 'active' : ''}`;
    dot.addEventListener('click', () => moveToSlide(index));
    nodes.indicators.appendChild(dot);
  });

  // Slider Button Controls
  document.getElementById('slider-prev').onclick = () => moveToSlide(state.currentSlide - 1);
  document.getElementById('slider-next').onclick = () => moveToSlide(state.currentSlide + 1);

  startSliderInterval();
}

function moveToSlide(index) {
  const total = state.topSix.length;
  if (total === 0) return;

  // Left-to-right cyclic wrap
  state.currentSlide = (index + total) % total;
  nodes.heroTrack.style.transform = `translateX(-${state.currentSlide * 100}%)`;

  const dots = nodes.indicators.querySelectorAll('.indicator');
  dots.forEach((dot, idx) => {
    dot.classList.toggle('active', idx === state.currentSlide);
  });

  resetSliderInterval();
}

function startSliderInterval() {
  state.sliderInterval = setInterval(() => {
    moveToSlide(state.currentSlide + 1);
  }, 5000);
}

function resetSliderInterval() {
  clearInterval(state.sliderInterval);
  startSliderInterval();
}

// Filtering & Rendering Catalog
function setupFilters() {
  // Category navigation
  nodes.navButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      nodes.navButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.selectedCategory = btn.dataset.category;
      nodes.catalogHeading.textContent = btn.innerText.replace(/[^a-zA-Z ]/g, '').trim();
      applyFilters();
    });
  });

  // Genre chips
  nodes.genreContainer.addEventListener('click', (e) => {
    if (!e.target.classList.contains('genre-chip')) return;
    nodes.genreContainer.querySelectorAll('.genre-chip').forEach(c => c.classList.remove('active'));
    e.target.classList.add('active');
    state.selectedGenre = e.target.dataset.genre;
    applyFilters();
  });
}

function setupSearch() {
  nodes.searchInput.addEventListener('input', (e) => {
    state.searchQuery = e.target.value.toLowerCase().trim();
    applyFilters();
  });
}

function applyFilters() {
  let list = [...state.animes];

  // Category filter
  if (state.selectedCategory === 'ongoing') {
    list = list.filter(a => a.status === 'RELEASING' || a.is_ongoing);
  } else if (state.selectedCategory === 'completed') {
    list = list.filter(a => a.status === 'FINISHED' || (!a.is_ongoing && a.status));
  } else if (state.selectedCategory === 'movies') {
    list = list.filter(a => (a.format || a.type || '').toUpperCase() === 'MOVIE');
  }

  // Genre filter
  if (state.selectedGenre !== 'all') {
    list = list.filter(a => {
      const genres = a.genres || [];
      return genres.some(g => g.toLowerCase() === state.selectedGenre.toLowerCase());
    });
  }

  // Search input
  if (state.searchQuery) {
    list = list.filter(a => {
      const title = (a.title || a.name || '').toLowerCase();
      return title.includes(state.searchQuery);
    });
  }

  state.filtered = list;
  renderGrid(list);
}

function renderGrid(items) {
  nodes.resultsCount.textContent = `${items.length} Titles`;
  nodes.animeGrid.innerHTML = '';

  if (!items.length) {
    nodes.emptyState.classList.remove('hidden');
    return;
  }
  nodes.emptyState.classList.add('hidden');

  items.forEach(item => {
    const card = document.createElement('div');
    card.className = 'anime-card';
    const poster = item.image_url || item.thumbnail || 'https://via.placeholder.com/300x450';
    const title = item.title || item.name || 'Untitled';
    const epCount = item.total_episodes || item.episodes ? `${item.total_episodes || item.episodes} Ep` : 'HD';

    card.innerHTML = `
      <div class="card-poster">
        <img src="${poster}" alt="${title}" loading="lazy" />
        <span class="card-badge">${epCount}</span>
      </div>
      <div class="card-body">
        <h3 class="card-title">${title}</h3>
        <div class="card-meta">
          <span>${item.type || 'Anime'}</span>
          <span>${item.score ? `★ ${item.score}` : 'Official'}</span>
        </div>
      </div>
    `;
    card.addEventListener('click', () => openDetails(item.id || item.anime_id));
    nodes.animeGrid.appendChild(card);
  });
}

// Modal View & Player Integration
window.openDetails = async function (id) {
  const item = state.animes.find(a => (a.id || a.anime_id) == id) || state.topSix[0];
  if (!item) return;

  nodes.modalContent.innerHTML = `
    <div style="display:flex; gap: 20px; flex-wrap: wrap;">
      <img src="${item.image_url || item.thumbnail}" style="width: 140px; border-radius: 12px; object-fit: cover;" />
      <div style="flex: 1; min-width: 250px;">
        <h2 style="margin-bottom: 8px;">${item.title || item.name}</h2>
        <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 12px;">${item.genres ? item.genres.join(', ') : 'Anime'}</p>
        <p style="font-size: 0.9rem; line-height: 1.5; margin-bottom: 20px;">${item.synopsis || 'No description provided.'}</p>
        <a href="https://t.me/official_swordsmith" target="_blank" class="btn-watch" style="text-decoration:none;">
          Play on @official_swordsmith
        </a>
      </div>
    </div>
  `;
  nodes.modal.classList.remove('hidden');
};

nodes.modalClose.onclick = () => nodes.modal.classList.add('hidden');
window.onclick = (e) => {
  if (e.target === nodes.modal) nodes.modal.classList.add('hidden');
};

// Sidebar Toggle For Mobile
function setupSidebar() {
  const open = () => {
    nodes.sidebar.classList.add('open');
    nodes.backdrop.classList.add('active');
  };
  const close = () => {
    nodes.sidebar.classList.remove('open');
    nodes.backdrop.classList.remove('active');
  };

  nodes.menuToggle?.addEventListener('click', open);
  nodes.sidebarClose?.addEventListener('click', close);
  nodes.backdrop?.addEventListener('click', close);
}
