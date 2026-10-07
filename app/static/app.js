document.addEventListener('DOMContentLoaded', () => {
  loadSections();
  loadCatalog('title', '');
  
  // Search & Sort Listeners
  const searchInput = document.getElementById('search-input');
  const sortSelect = document.getElementById('sort-select');
  
  let searchTimeout;
  searchInput.addEventListener('input', (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      loadCatalog(sortSelect.value, e.target.value);
    }, 500);
  });

  sortSelect.addEventListener('change', (e) => {
    loadCatalog(e.target.value, searchInput.value);
  });
});

// 1. Fetch /api/sections for Hero, Popular, and Ongoing Rails
async function loadSections() {
  try {
    const res = await fetch('/api/sections');
    const data = await res.json();
    
    renderHero(data.hero);
    renderRail(data.popular, 'popular-rail');
    renderRail(data.ongoing, 'ongoing-rail');
  } catch (err) {
    console.error('Error loading sections:', err);
  }
}

// 2. Fetch /api/catalog for the main Grid
async function loadCatalog(sort, query) {
  const grid = document.getElementById('catalog-grid');
  grid.innerHTML = '<div class="loading-spinner"></div>';
  
  try {
    const res = await fetch(`/api/catalog?sort=${sort}&q=${encodeURIComponent(query)}&limit=100`);
    const data = await res.json();
    
    document.getElementById('catalog-count').textContent = `Showing ${data.count} titles`;
    renderGrid(data.items, 'catalog-grid');
  } catch (err) {
    console.error('Error loading catalog:', err);
    grid.innerHTML = '<p style="text-align:center; width:100%;">Failed to load catalog.</p>';
  }
}

// UI Render Functions
function renderHero(hero) {
  if (!hero) return;
  const container = document.getElementById('hero-section');
  const bgImage = hero.banner_url || hero.poster_url || 'https://images.unsplash.com/photo-1578632767115-351597cf2477?w=1600';
  
  container.style.backgroundImage = `url('${bgImage}')`;
  container.innerHTML = `
    <div class="hero-overlay"></div>
    <div class="hero-content">
      <div class="hero-tags">
        <span class="tag">${hero.status || 'Finished'}</span>
        ${hero.audio ? `<span class="tag">${hero.audio}</span>` : ''}
      </div>
      <h1>${hero.title}</h1>
      <div class="hero-meta">
        <span>${hero.season_count} Seasons</span>
        <span>•</span>
        <span>${hero.episode_count} Episodes</span>
        <span>•</span>
        <span class="score">★ ${hero.score || 'N/A'}</span>
        <span>•</span>
        <span>${hero.year || ''}</span>
      </div>
      <p class="hero-synopsis">${hero.synopsis || 'No synopsis available for this title.'}</p>
      <a href="${hero.channel_link || '#'}" target="_blank" class="btn-primary" style="padding: 14px 32px; font-size: 1.1rem; display: inline-block;">
        Watch on Telegram
      </a>
    </div>
  `;
}

function renderRail(items, elementId) {
  const container = document.getElementById(elementId);
  if (!items || items.length === 0) {
    container.innerHTML = '<p style="color: var(--text-muted); padding: 20px;">No titles found in this section.</p>';
    return;
  }

  container.innerHTML = '';
  items.forEach(item => {
    // We use Banner URL for horizontal rails if available, otherwise poster
    const imgUrl = item.banner_url || item.poster_url || 'https://via.placeholder.com/600x300';
    
    const card = document.createElement('a');
    card.className = 'rail-card';
    card.href = item.channel_link || '#';
    card.target = '_blank';
    
    card.innerHTML = `
      <img src="${imgUrl}" alt="${item.title}" class="rail-img" loading="lazy">
      <div class="rail-info">
        <h3 class="rail-title">${item.title}</h3>
        <div class="rail-stats">
          <span>★ ${item.score || '-'}</span>
          <span>${item.episode_count} Eps</span>
        </div>
        <div class="rail-genres">${item.genres || 'Anime'}</div>
      </div>
    `;
    container.appendChild(card);
  });
}

function renderGrid(items, elementId) {
  const container = document.getElementById(elementId);
  if (!items || items.length === 0) {
    container.innerHTML = '<p style="text-align:center; width:100%; grid-column: 1 / -1;">No matching titles found.</p>';
    return;
  }

  container.innerHTML = '';
  items.forEach(item => {
    const card = document.createElement('a');
    card.className = 'grid-card';
    card.href = item.channel_link || '#';
    card.target = '_blank';
    
    card.innerHTML = `
      <div class="grid-poster">
        <img src="${item.poster_url || 'https://via.placeholder.com/300x450'}" alt="${item.title}" loading="lazy">
        ${item.status === 'Releasing' ? `<span class="badge-status">Airing</span>` : ''}
        <span class="badge-episodes">${item.episode_count} Ep</span>
      </div>
      <div class="grid-info">
        <h3 class="grid-title">${item.title}</h3>
        <div class="grid-meta">
          <span>${item.year || ''}</span>
          <span style="color: #F59E0B; font-weight: 600;">★ ${item.score || '-'}</span>
        </div>
      </div>
    `;
    
    // Register a view when clicked, mapping to your /api/entry/{id}/view logic
    card.addEventListener('click', () => {
      fetch(`/api/entry/${item.id}/view`, { method: 'POST' }).catch(e => console.log(e));
    });
    
    container.appendChild(card);
  });
}
