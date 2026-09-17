/**
 * OmniSource Features Module
 * Implements P0-P3 features: Favorites, Collections, QR codes, search
 * operators, ratings, and i18n — a dependency-free vanilla JS module that
 * extends the OS namespace from core.js and the renderers in site.js.
 */

(function() {
  'use strict';

  // ============================================================================
  // UTILITIES
  // ============================================================================

  const OS = window.OS || {};
  window.OS = OS;

  // Storage utilities with namespace
  const Storage = {
    get: (key, defaultValue = null) => {
      try {
        const item = localStorage.getItem(`os:${key}`);
        return item ? JSON.parse(item) : defaultValue;
      } catch {
        return defaultValue;
      }
    },
    set: (key, value) => {
      try {
        localStorage.setItem(`os:${key}`, JSON.stringify(value));
      } catch (e) {
        console.warn('LocalStorage not available:', e);
      }
    },
    remove: (key) => {
      try {
        localStorage.removeItem(`os:${key}`);
      } catch {}
    }
  };

  // Generate unique IDs
  const generateId = () => {
    return Date.now().toString(36) + Math.random().toString(36).substring(2);
  };

  // Debounce utility
  const debounce = (fn, delay = 200) => {
    let timeout;
    return (...args) => {
      clearTimeout(timeout);
      timeout = setTimeout(() => fn(...args), delay);
    };
  };

  // ============================================================================
  // EVENT BUS
  // ============================================================================

  const EventBus = {
    _events: {},
    on: (event, callback) => {
      if (!EventBus._events[event]) {
        EventBus._events[event] = [];
      }
      EventBus._events[event].push(callback);
    },
    off: (event, callback) => {
      const listeners = EventBus._events[event];
      if (listeners) {
        EventBus._events[event] = listeners.filter(l => l !== callback);
      }
    },
    emit: (event, ...args) => {
      const listeners = EventBus._events[event];
      if (listeners) {
        listeners.forEach(l => l(...args));
      }
    }
  };

  OS.on = EventBus.on;
  OS.emit = EventBus.emit;

  // ============================================================================
  // FAVORITES / WATCHLIST (P0)
  // ============================================================================

  // Favorites live under 'omnisource-favorites' (canonical, read/written by
  // js/site.js) and are mirrored to the legacy prefixed 'os:favorites' key,
  // so the heart buttons on the home catalog and on /favorites/ +
  // /collections/ never disagree. Keep both keys in sync on every write.
  const FAVORITES_CANONICAL_KEY = 'omnisource-favorites';
  const FAVORITES_EVENT = 'os:favorites-changed';

  const Favorites = {
    STORAGE_KEY: 'favorites', // legacy os:favorites, mirrored below

    init() {
      // Merge any legacy data into the canonical store and mirror it back.
      this.getAll();
      this._injectUI();
      this._loadFromURL();
      window.addEventListener(FAVORITES_EVENT, () => {
        this.updateCount();
        this._syncButtons();
      });
      window.addEventListener('storage', (event) => {
        if (event.key === FAVORITES_CANONICAL_KEY || event.key === `os:${this.STORAGE_KEY}`) {
          this.updateCount();
          this._syncButtons();
        }
      });
    },

    _readList(key) {
      try {
        const raw = localStorage.getItem(key);
        const list = raw ? JSON.parse(raw) : [];
        return Array.isArray(list) ? list : [];
      } catch {
        return [];
      }
    },

    _writeList(list) {
      const value = JSON.stringify(Array.from(new Set(list)));
      try {
        localStorage.setItem(FAVORITES_CANONICAL_KEY, value);
        localStorage.setItem(`os:${this.STORAGE_KEY}`, value);
      } catch (e) { /* storage unavailable */ }
      try { window.dispatchEvent(new CustomEvent(FAVORITES_EVENT)); } catch (e) { /* ignore */ }
    },

    _syncButtons() {
      document.querySelectorAll('.favorite-btn[data-app-id]').forEach(btn => {
        const active = this.has(btn.dataset.appId);
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-label', active ? 'Remove from favorites' : 'Add to favorites');
      });
    },

    _injectUI() {
      // Add favorite button styles (the buttons themselves are rendered by
      // the page's own catalog renderer — site.js already draws a heart on
      // every card, so we only provide the shared stylesheet here).
      const style = document.createElement('style');
      style.textContent = `
        .favorite-btn {
          cursor: pointer;
          border: 0;
          background: none;
          padding: 4px;
          color: var(--faint);
          border-radius: 8px;
          transition: color 0.15s ease, transform 0.15s ease;
        }
        .favorite-btn:hover {
          color: var(--accent);
          transform: scale(1.1);
        }
        .favorite-btn.active {
          color: var(--accent);
        }
        .favorite-btn.active svg {
          fill: currentColor;
        }
        .favorite-btn svg {
          width: 18px;
          height: 18px;
          fill: none;
          stroke: currentColor;
          stroke-width: 1.7;
          stroke-linecap: round;
          stroke-linejoin: round;
        }
        .favorites-count {
          font: 700 10px var(--font-mono);
          background: var(--accent-soft);
          color: var(--accent);
          padding: 2px 8px;
          border-radius: 999px;
          margin-left: 8px;
        }
        .favorites-nav-item {
          position: relative;
        }
        .favorites-nav-item .favorites-count {
          position: absolute;
          top: -4px;
          right: -4px;
        }
      `;
      document.head.appendChild(style);
    },

    _loadFromURL() {
      // Check if URL has favorite param
      const params = new URLSearchParams(window.location.search);
      if (params.has('favorite')) {
        const appId = params.get('favorite');
        this.toggle(appId, true);
      }
    },

    getAll() {
      // Union the canonical key and the legacy prefixed key, then persist the
      // merge so a one-time migration happens transparently.
      const canonical = this._readList(FAVORITES_CANONICAL_KEY);
      const legacy = this._readList(`os:${this.STORAGE_KEY}`);
      const merged = Array.from(new Set(canonical.concat(legacy)));
      if (merged.length !== canonical.length || legacy.length !== merged.length) {
        try {
          localStorage.setItem(FAVORITES_CANONICAL_KEY, JSON.stringify(merged));
          localStorage.setItem(`os:${this.STORAGE_KEY}`, JSON.stringify(merged));
        } catch (e) { /* storage unavailable */ }
      }
      return merged;
    },

    has(appId) {
      return this.getAll().includes(appId);
    },

    add(appId) {
      const favorites = this.getAll();
      if (!favorites.includes(appId)) {
        favorites.push(appId);
        this._writeList(favorites);
        this.updateCount();
        OS.emit('favorites:updated', this.getAll());
        return true;
      }
      return false;
    },

    remove(appId) {
      const favorites = this.getAll();
      const index = favorites.indexOf(appId);
      if (index > -1) {
        favorites.splice(index, 1);
        this._writeList(favorites);
        this.updateCount();
        OS.emit('favorites:updated', this.getAll());
        return true;
      }
      return false;
    },

    toggle(appId, force) {
      const has = this.has(appId);
      if (force === true && !has) {
        return this.add(appId);
      }
      if (force === false && has) {
        return this.remove(appId);
      }
      return has ? this.remove(appId) : this.add(appId);
    },

    updateCount() {
      const count = this.getAll().length;
      document.querySelectorAll('.favorites-count').forEach(countEl => {
        countEl.textContent = count;
        countEl.style.display = count > 0 ? 'inline' : 'none';
      });
    },

    injectButtons(container = document) {
      // Add favorite buttons to app cards
      const appCards = container.querySelectorAll('.app-card, .rail-card, .featured-card, .result-row');
      appCards.forEach(card => {
        if (card.querySelector('.favorite-btn')) return;

        const appId = this._getAppId(card);
        if (!appId) return;

        const btn = document.createElement('button');
        btn.className = 'favorite-btn';
        btn.setAttribute('aria-label', 'Add to favorites');
        btn.setAttribute('data-app-id', appId);
        btn.innerHTML = `
          <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
          </svg>
        `;

        if (this.has(appId)) {
          btn.classList.add('active');
          btn.setAttribute('aria-label', 'Remove from favorites');
        }

        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.toggle(appId);
          btn.classList.toggle('active');
          const isActive = btn.classList.contains('active');
          btn.setAttribute('aria-label', isActive ? 'Remove from favorites' : 'Add to favorites');
        });

        // Find the best place to insert the button
        const actions = card.querySelector('.card-actions, .actions');
        if (actions) {
          actions.prepend(btn);
        } else {
          // Try to insert near the icon or title
          const icon = card.querySelector('.app-icon, .icon-wrap, img');
          if (icon) {
            icon.after(btn);
          }
        }
      });
    },

    _getAppId(card) {
      // Try to get app ID from various attributes
      const href = card.getAttribute('href');
      if (href && href.includes('/apps/')) {
        const match = href.match(/\/apps\/([^\/]+)/);
        if (match) return match[1];
      }

      // Check for data attributes
      const dataId = card.getAttribute('data-app-id') ||
                    card.getAttribute('data-id') ||
                    card.getAttribute('id');
      if (dataId) return dataId;

      // Try to extract from the card structure
      const title = card.querySelector('h3, h2, .card-identity h3');
      if (title) {
        const text = title.textContent.trim().toLowerCase().replace(/\s+/g, '-');
        return text;
      }

      return null;
    },

    getFavoritesPageHTML() {
      const favorites = this.getAll();
      const appData = this._loadAppData();
      const favoriteApps = appData.filter(app => favorites.includes(app.id));

      return `
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <title>My Favorites - OmniSource</title>
          <link rel="stylesheet" href="${OS.asset('design-system/tokens.css')}">
          <link rel="stylesheet" href="${OS.asset('design-system/utilities.css')}">
          <link rel="stylesheet" href="${OS.asset('design-system/animations.css')}">
          <link rel="stylesheet" href="${OS.asset('design-system/components.css')}">
        </head>
        <body>
          <header class="site-header" id="site-header">
            <div class="nav shell">
              <a href="${OS.url('/')}" class="brand">
                <picture><source srcset="${OS.asset('assets/OmniSource.webp')}" type="image/webp"><img src="${OS.asset('assets/OmniSource.png')}" alt="OmniSource" width="40" height="40"></picture>
                <span>OmniSource</span>
              </a>
              <nav class="nav-links">
                <a href="${OS.url('/')}">Home</a>
                <a href="${OS.url('/compare/')}">Compare</a>
                <a href="${OS.url('/status/')}">Status</a>
                <a href="${OS.url('/analytics/')}">Analytics</a>
                <a href="${OS.url('/install/')}">Install</a>
                <a href="${OS.url('/favorites/')}" class="active" aria-current="page">
                  <span>Favorites</span>
                  <span class="favorites-count">${favorites.length}</span>
                </a>
              </nav>
              <div class="nav-controls">
                <button id="themeButton" class="icon-button" aria-label="Toggle theme">
                  <svg class="icon-sun" viewBox="0 0 24 24"><path d="M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zm0 2a7 7 0 1 1 0-14 7 7 0 0 1 0 14z"/></svg>
                  <svg class="icon-moon" viewBox="0 0 24 24"><path d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z"/></svg>
                </button>
              </div>
            </div>
          </header>
          
          <main class="shell">
            <div class="section">
              <div class="section-heading">
                <h1>My Favorites</h1>
                <p class="lead">${favoriteApps.length} app${favoriteApps.length !== 1 ? 's' : ''} saved</p>
              </div>
              
              ${favoriteApps.length === 0 ? `
                <div class="empty-state">
                  <div class="empty-icon">❤️</div>
                  <h3>No favorites yet</h3>
                  <p>Start adding apps to your favorites by clicking the heart icon on any app card.</p>
                  <a href="${OS.url('/')}" class="button primary">Browse Apps</a>
                </div>
              ` : `
                <div class="apps-grid">
                  ${favoriteApps.map(app => this._renderAppCard(app)).join('')}
                </div>
              `}
            </div>
          </main>
          
          <footer class="footer">
            <div class="footer-inner shell">
              <div class="footer-brand">
                <picture><source srcset="${OS.asset('assets/OmniSource.webp')}" type="image/webp"><img src="${OS.asset('assets/OmniSource.png')}" alt="OmniSource" width="40" height="40"></picture>
                <div>
                  <strong>OmniSource</strong>
                  <p>The App Store for sideloaded iOS.</p>
                </div>
              </div>
              <p class="fine-print">
                OmniSource is an independent community project. All apps, code and trademarks belong to their respective owners.
              </p>
            </div>
          </footer>
          
          <script src="${OS.url('js/core.js')}"></script>
          <script>
            // Initialize favorites on this page
            document.addEventListener('DOMContentLoaded', () => {
              OS.Favorites.injectButtons();
              OS.Favorites.updateCount();
            });
          </script>
        </body>
        </html>
      `;
    },

    _loadAppData() {
      // The hosting page loads the discovery catalog through OS.loadCatalog()
      // (api/catalog.json → feeds/discovery.json → discovery.json) and drops it
      // on window.OS_CATALOG before any of this runs.
      try {
        const catalog = window.OS_CATALOG || [];
        return catalog.map(app => ({
          id: app.slug || app.id,
          name: app.name,
          description: app.description,
          icon: app.icon,
          developer: app.developer,
          version: app.version,
          updated: app.updated,
          status: app.status,
          bundleId: app.bundleIdentifier,
          source: app.source
        }));
      } catch {
        return [];
      }
    },

    _renderAppCard(app) {
      // Every value below comes from a fetched catalog document, so it is
      // escaped on the way into innerHTML - the same rule js/site.js follows
      // for the cards this one mirrors.
      const esc = (value) => OS.esc(value == null ? '' : value);
      return `
        <article class="app-card" data-app-id="${esc(app.id)}">
          <div class="card-top">
            <div class="icon-wrap">
              <img src="${esc(OS.asset(app.icon || 'OmniSource.png'))}" alt="${esc(app.name)}" class="app-icon" width="58" height="58">
            </div>
            <div class="card-identity">
              <h3><a href="${esc(OS.url(`apps/${app.id}/`))}">${esc(app.name)}</a></h3>
              <p class="card-dev">${esc(app.developer || 'Unknown')}</p>
            </div>
            <button class="favorite-btn active" aria-label="Remove from favorites" data-app-id="${esc(app.id)}">
              <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
              </svg>
            </button>
          </div>
          <p class="app-description">${esc(app.description || 'No description available.')}</p>
          <div class="card-meta">
            <span class="meta-item"><b>${esc(app.version || 'N/A')}</b> version</span>
            <span class="meta-item"><b>${esc(app.status || 'unknown')}</b> status</span>
            <span class="meta-item"><b>${esc(app.source || 'N/A')}</b> source</span>
          </div>
          <div class="card-bottom">
            <a class="get-button" href="${esc(OS.url(`apps/${app.id}/`))}">View details
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg>
            </a>
          </div>
        </article>
      `;
    }
  };

  // ============================================================================
  // APP COLLECTIONS (P0)
  // ============================================================================

  const Collections = {
    STORAGE_KEY: 'collections',

    init() {
      if (!Storage.get(this.STORAGE_KEY)) {
        // Create default collections
        const defaults = [
          { id: 'youtube-mods', name: 'YouTube Mods', description: 'Popular YouTube modifications', apps: [], color: '#ff0000' },
          { id: 'gaming-tools', name: 'Gaming Tools', description: 'Tools for mobile gaming', apps: [], color: '#00ff00' },
          { id: 'productivity', name: 'Productivity', description: 'Productivity apps and utilities', apps: [], color: '#0000ff' },
          { id: 'my-apps', name: 'My Apps', description: 'Apps I use regularly', apps: [], color: '#ff00ff' }
        ];
        Storage.set(this.STORAGE_KEY, defaults);
      }
      this._injectUI();
    },

    // The collection modals on /favorites/ and /collections/ are plain markup
    // in those pages (no stylesheet ever covered them, so they rendered as
    // unstyled blocks). Inject the shared modal design once here.
    _injectUI() {
      if (document.getElementById('os-collection-modal-styles')) return;
      const style = document.createElement('style');
      style.id = 'os-collection-modal-styles';
      style.textContent = `
        .collections-modal, .create-collection-modal, .add-to-collection-modal {
          position: fixed;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          background: var(--surface-solid);
          border: 1px solid var(--line);
          border-radius: var(--radius-xl);
          padding: 24px;
          max-width: 480px;
          width: 90%;
          max-height: 80vh;
          overflow-y: auto;
          box-shadow: var(--shadow-lg);
          z-index: 1000;
          display: none;
        }
        .collections-modal.show, .create-collection-modal.show, .add-to-collection-modal.show {
          display: block;
        }
        .collections-modal h3, .create-collection-modal h3, .add-to-collection-modal h3 {
          margin: 0 0 8px;
          font-size: 18px;
        }
        .collections-modal p, .create-collection-modal p, .add-to-collection-modal p {
          color: var(--muted);
          font-size: 13px;
          margin: 0 0 16px;
        }
        .collections-backdrop, .create-collection-backdrop, .add-to-collection-backdrop {
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.5);
          backdrop-filter: blur(4px);
          z-index: 999;
          opacity: 0;
          pointer-events: none;
          transition: opacity 0.2s ease;
        }
        .collections-backdrop.show, .create-collection-backdrop.show, .add-to-collection-backdrop.show {
          opacity: 1;
          pointer-events: all;
        }
        .collection-form {
          display: grid;
          gap: 12px;
        }
        .collection-form .field {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .collection-form label {
          font: 700 11px var(--font-mono);
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: var(--muted);
        }
        .collection-form input,
        .collection-form textarea {
          border: 1px solid var(--line);
          background: var(--surface-solid);
          color: var(--text);
          border-radius: var(--radius-md);
          padding: 0 12px;
          font-size: 13px;
          min-height: 40px;
        }
        .collection-form textarea {
          padding: 10px 12px;
          resize: vertical;
        }
        .collection-list {
          display: grid;
          gap: 10px;
          margin-top: 8px;
        }
        .collection-item {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 12px;
          background: var(--surface-2);
          border: 1px solid var(--line);
          border-radius: var(--radius-md);
          cursor: pointer;
          transition: border-color 0.15s ease, background 0.15s ease;
        }
        .collection-item:hover {
          border-color: var(--accent);
          background: var(--accent-soft);
        }
        .collection-item .info {
          flex: 1;
          min-width: 0;
        }
        .collection-item .info h4 {
          margin: 0 0 2px;
          font-size: 13px;
        }
        .collection-item .info p {
          margin: 0;
          font-size: 11px;
          color: var(--muted);
        }
        .collection-item .app-count {
          font: 700 11px var(--font-mono);
          color: var(--muted);
        }
      `;
      document.head.appendChild(style);
    },

    getAll() {
      return Storage.get(this.STORAGE_KEY, []);
    },

    getById(id) {
      return this.getAll().find(c => c.id === id);
    },

    create(name, description = '', color = '#6d5ef5') {
      const collections = this.getAll();
      const id = generateId();
      const newCollection = { id, name, description, apps: [], color };
      collections.push(newCollection);
      Storage.set(this.STORAGE_KEY, collections);
      OS.emit('collections:updated', collections);
      return newCollection;
    },

    update(id, updates) {
      const collections = this.getAll();
      const index = collections.findIndex(c => c.id === id);
      if (index > -1) {
        collections[index] = { ...collections[index], ...updates };
        Storage.set(this.STORAGE_KEY, collections);
        OS.emit('collections:updated', collections);
        return collections[index];
      }
      return null;
    },

    delete(id) {
      const collections = this.getAll();
      const index = collections.findIndex(c => c.id === id);
      if (index > -1) {
        collections.splice(index, 1);
        Storage.set(this.STORAGE_KEY, collections);
        OS.emit('collections:updated', collections);
        return true;
      }
      return false;
    },

    addApp(collectionId, appId) {
      const collections = this.getAll();
      const index = collections.findIndex(c => c.id === collectionId);
      if (index > -1) {
        if (!collections[index].apps.includes(appId)) {
          collections[index].apps.push(appId);
          Storage.set(this.STORAGE_KEY, collections);
          OS.emit('collections:updated', collections);
          return true;
        }
      }
      return false;
    },

    removeApp(collectionId, appId) {
      const collections = this.getAll();
      const index = collections.findIndex(c => c.id === collectionId);
      if (index > -1) {
        const appIndex = collections[index].apps.indexOf(appId);
        if (appIndex > -1) {
          collections[index].apps.splice(appIndex, 1);
          Storage.set(this.STORAGE_KEY, collections);
          OS.emit('collections:updated', collections);
          return true;
        }
      }
      return false;
    },

    toggleApp(collectionId, appId) {
      const has = this.hasApp(collectionId, appId);
      return has ? this.removeApp(collectionId, appId) : this.addApp(collectionId, appId);
    },

    hasApp(collectionId, appId) {
      const collection = this.getById(collectionId);
      return collection && collection.apps.includes(appId);
    },

    getUserCollections() {
      return this.getAll().filter(c => !c.id.startsWith('default-'));
    },

    getCollectionsForApp(appId) {
      return this.getAll().filter(c => c.apps.includes(appId));
    }
  };

  // ============================================================================
  // QR CODE GENERATOR (P1)
  // ============================================================================
  // Dependency-free: the QR is rendered as an <img> from api.qrserver.com (the
  // same generator the home page uses), so there is no third-party script to
  // load and no canvas library to shadow. The favorites/collections pages ship
  // a .qr-modal/.qr-backdrop pair; when it is missing this module creates one.
  const QRCode = {
    init() {
      this._injectUI();
    },

    _injectUI() {
      const style = document.createElement('style');
      style.textContent = `
        .qr-btn {
          cursor: pointer;
          border: 0;
          background: none;
          padding: 6px;
          color: var(--muted);
          border-radius: 8px;
          transition: color 0.15s ease, background 0.15s ease;
        }
        .qr-btn:hover {
          color: var(--accent);
          background: var(--surface-2);
        }
        .qr-btn svg {
          width: 18px;
          height: 18px;
          fill: none;
          stroke: currentColor;
          stroke-width: 1.8;
        }
        .qr-modal {
          position: fixed;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          background: var(--surface-solid);
          border: 1px solid var(--line);
          border-radius: var(--radius-xl);
          padding: 24px;
          max-width: 340px;
          width: 90%;
          box-shadow: var(--shadow-lg);
          z-index: 1000;
          display: none;
        }
        .qr-modal.show {
          display: block;
        }
        .qr-modal h3 {
          margin: 0 0 12px;
          font-size: 18px;
        }
        .qr-modal .qr-code {
          text-align: center;
          margin: 16px 0;
        }
        .qr-modal .qr-code img {
          display: block;
          margin: 0 auto;
          background: #fff;
          border-radius: 10px;
          padding: 8px;
          max-width: 100%;
        }
        .qr-modal .url-display {
          font: 11px var(--font-mono);
          color: var(--muted);
          word-break: break-all;
          background: var(--surface-2);
          padding: 8px 12px;
          border-radius: var(--radius-sm);
          margin-top: 12px;
        }
        .qr-modal .actions {
          display: flex;
          gap: 8px;
          margin-top: 16px;
          justify-content: flex-end;
        }
        .qr-backdrop {
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.5);
          backdrop-filter: blur(4px);
          z-index: 999;
          opacity: 0;
          pointer-events: none;
          transition: opacity 0.2s ease;
        }
        .qr-backdrop.show {
          opacity: 1;
          pointer-events: all;
        }
      `;
      document.head.appendChild(style);
    },

    _els() {
      let backdrop = document.querySelector('.qr-backdrop');
      let modal = document.querySelector('.qr-modal');
      if (!backdrop || !modal) {
        this._createModal();
        backdrop = document.querySelector('.qr-backdrop');
        modal = document.querySelector('.qr-modal');
      }
      return { backdrop, modal };
    },

    _qrSrc(text) {
      return 'https://api.qrserver.com/v1/create-qr-code/?size=460x460&margin=0&data=' + encodeURIComponent(text);
    },

    showQR(text, title = 'QR Code') {
      const els = this._els();
      const container = els.modal.querySelector('.qr-code');
      if (container) {
        container.innerHTML = '<img src="' + this._qrSrc(text) + '" alt="QR code" width="240" height="240" loading="lazy">';
      }
      const urlDisplay = els.modal.querySelector('.url-display');
      if (urlDisplay) urlDisplay.textContent = text;
      const titleEl = els.modal.querySelector('h3');
      if (titleEl) titleEl.textContent = title;
      this._lastText = text;
      els.backdrop.classList.add('show');
      els.modal.classList.add('show');
    },

    hideQR() {
      const backdrop = document.querySelector('.qr-backdrop');
      const modal = document.querySelector('.qr-modal');
      if (backdrop) backdrop.classList.remove('show');
      if (modal) modal.classList.remove('show');
    },

    _createModal() {
      const backdrop = document.createElement('div');
      backdrop.className = 'qr-backdrop';
      backdrop.addEventListener('click', () => this.hideQR());
      document.body.appendChild(backdrop);

      const modal = document.createElement('div');
      modal.className = 'qr-modal';
      modal.innerHTML = `
        <h3>QR Code</h3>
        <div class="qr-code"></div>
        <div class="url-display"></div>
        <div class="actions">
          <button class="button" onclick="OS.QRCode.download()">Download</button>
          <button class="button ghost" onclick="OS.QRCode.hideQR()">Close</button>
        </div>
      `;
      document.body.appendChild(modal);

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') this.hideQR();
      });
    },

    download() {
      const text = this._lastText;
      if (!text) return;
      fetch(this._qrSrc(text))
        .then((res) => res.blob())
        .then((blob) => {
          const link = document.createElement('a');
          link.href = URL.createObjectURL(blob);
          link.download = 'omnisource-qr.png';
          link.click();
          setTimeout(() => URL.revokeObjectURL(link.href), 5000);
        })
        .catch(() => {
          window.open(this._qrSrc(text), '_blank');
        });
    }
  };

  // ============================================================================
  // SEARCH OPERATORS (P1)
  //
  // This module is the operator UI layered on top of the search engine that
  // js/core.js publishes as OS.Search (load / search / highlight / docs). It
  // must NOT be exported under that name: features.js runs after core.js, so
  // assigning OS.Search here replaced the engine object and every call site
  // in site.js and core.js threw "OS.Search.load is not a function". The UI is
  // therefore exported as OS.SearchUI and keeps the engine untouched.
  //
  // It only shows a "?" help panel of operator syntax next to the catalog
  // search box. It does NOT intercept typing — the catalog renderer in
  // js/site.js owns the grid, and a second input listener here used to blow
  // its results away 300 ms after every keystroke.
  // ============================================================================

  const SearchUI = {
    init() {
      this._injectUI();
    },

    _injectUI() {
      const searchBox = document.querySelector('#catalog .search-box') || document.querySelector('.search-box');
      if (!searchBox) return;
      const input = searchBox.querySelector('input');
      if (!input) return;

      const style = document.createElement('style');
      style.textContent = `
        .search-help {
          position: absolute;
          right: 6px;
          top: 50%;
          transform: translateY(-50%);
          display: grid;
          place-items: center;
          /* A real tap target: this used to be a 15x19px "?" that sat under
             the user's thumb — and under the input's own text. */
          min-width: 36px;
          min-height: 36px;
          background: none;
          border: 0;
          color: var(--muted);
          cursor: pointer;
          padding: 0;
          border-radius: 9px;
          font: 700 11px var(--font-mono);
          letter-spacing: 0.08em;
          text-transform: uppercase;
        }
        /* Reserve the button's room so the query never runs underneath it. */
        .search-box.has-search-help input { padding-right: 34px; }
        .search-help:hover,
        .search-help:focus-visible {
          color: var(--text);
          background: var(--surface-2);
        }
        .search-operators-panel {
          position: absolute;
          top: 100%;
          right: 0;
          margin-top: 8px;
          background: var(--surface-solid);
          border: 1px solid var(--line);
          border-radius: var(--radius-md);
          padding: 12px;
          min-width: 240px;
          box-shadow: var(--shadow-md);
          z-index: 100;
          display: none;
        }
        .search-operators-panel.show {
          display: block;
        }
        .search-operators-panel h4 {
          margin: 0 0 8px;
          font-size: 11px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          color: var(--muted);
        }
        .search-operators-panel .operator-list {
          display: grid;
          gap: 6px;
        }
        .search-operators-panel .operator-item {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 12px;
          padding: 6px 8px;
          border-radius: var(--radius-sm);
          cursor: pointer;
          transition: background 0.15s ease;
        }
        .search-operators-panel .operator-item:hover {
          background: var(--surface-2);
        }
        .search-operators-panel .operator-item code {
          font: 600 10px var(--font-mono);
          background: var(--surface-2);
          padding: 2px 6px;
          border-radius: 4px;
        }
        .search-operators-panel .operator-item span {
          color: var(--muted);
        }
      `;
      document.head.appendChild(style);

      const helpBtn = document.createElement('button');
      helpBtn.className = 'search-help';
      helpBtn.textContent = '?';
      helpBtn.type = 'button';
      helpBtn.setAttribute('aria-label', 'Search operators help');

      const operatorsPanel = document.createElement('div');
      operatorsPanel.className = 'search-operators-panel';
      operatorsPanel.innerHTML = `
        <h4>Search Operators</h4>
        <div class="operator-list">
          <div class="operator-item" data-operator="status:">
            <code>status:</code>
            <span>Filter by status (stable, beta, manual, unmaintained)</span>
          </div>
          <div class="operator-item" data-operator="source:">
            <code>source:</code>
            <span>Filter by source</span>
          </div>
          <div class="operator-item" data-operator="category:">
            <code>category:</code>
            <span>Filter by category</span>
          </div>
          <div class="operator-item" data-operator="updated:>">
            <code>updated:&gt;</code>
            <span>Updated after date (e.g., updated:>7d)</span>
          </div>
          <div class="operator-item" data-operator="updated:<">
            <code>updated:&lt;</code>
            <span>Updated before date</span>
          </div>
          <div class="operator-item" data-operator="version:">
            <code>version:</code>
            <span>Filter by version</span>
          </div>
        </div>
      `;

      searchBox.classList.add('has-search-help');
      searchBox.appendChild(helpBtn);
      searchBox.appendChild(operatorsPanel);

      helpBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        operatorsPanel.classList.toggle('show');
      });

      document.addEventListener('click', (e) => {
        if (!searchBox.contains(e.target)) {
          operatorsPanel.classList.remove('show');
        }
      });

      operatorsPanel.querySelectorAll('.operator-item').forEach(item => {
        item.addEventListener('click', () => {
          const op = item.getAttribute('data-operator');
          input.value = input.value + op;
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.focus();
          operatorsPanel.classList.remove('show');
        });
      });
    }
  };

  // ============================================================================
  // USER RATINGS (P2)
  // ============================================================================

  const Ratings = {
    STORAGE_KEY: 'ratings',

    init() {
      this._injectUI();
      this._loadFromStorage();
    },

    _injectUI() {
      const style = document.createElement('style');
      style.textContent = `
        .rating-stars {
          display: inline-flex;
          align-items: center;
          gap: 2px;
          font-size: 14px;
        }
        .rating-stars .star {
          cursor: pointer;
          color: var(--muted);
          transition: color 0.15s ease, transform 0.15s ease;
        }
        .rating-stars .star:hover {
          color: var(--amber);
          transform: scale(1.2);
        }
        .rating-stars .star.active {
          color: var(--amber);
        }
        .rating-stars .star svg {
          width: 16px;
          height: 16px;
          fill: currentColor;
        }
        .rating-display {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          font-size: 12px;
          color: var(--muted);
        }
        .rating-display .stars {
          color: var(--amber);
        }
        .rating-count {
          font-size: 11px;
          color: var(--faint);
        }
        .rate-btn {
          cursor: pointer;
          border: 0;
          background: none;
          padding: 4px 8px;
          color: var(--muted);
          font: 700 10px var(--font-mono);
          letter-spacing: 0.08em;
          text-transform: uppercase;
          border-radius: 6px;
          transition: all 0.15s ease;
        }
        .rate-btn:hover {
          color: var(--amber);
          background: var(--amber-soft);
        }
        .rating-modal {
          position: fixed;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          background: var(--surface-solid);
          border: 1px solid var(--line);
          border-radius: var(--radius-xl);
          padding: 24px;
          max-width: 360px;
          width: 90%;
          box-shadow: var(--shadow-lg);
          z-index: 1000;
          display: none;
        }
        .rating-modal.show {
          display: block;
        }
        .rating-modal h3 {
          margin: 0 0 8px;
          font-size: 18px;
        }
        .rating-modal p {
          color: var(--muted);
          font-size: 13px;
          margin: 0 0 16px;
        }
        .rating-modal .rating-stars {
          justify-content: center;
          font-size: 24px;
          margin: 16px 0;
        }
        .rating-modal .rating-stars .star {
          font-size: 28px;
        }
        .rating-modal .actions {
          display: flex;
          gap: 8px;
          justify-content: flex-end;
        }
        .rating-backdrop {
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.5);
          backdrop-filter: blur(4px);
          z-index: 999;
          opacity: 0;
          pointer-events: none;
          transition: opacity 0.2s ease;
        }
        .rating-backdrop.show {
          opacity: 1;
          pointer-events: all;
        }
      `;
      document.head.appendChild(style);
    },

    _loadFromStorage() {
      if (!Storage.get(this.STORAGE_KEY)) {
        Storage.set(this.STORAGE_KEY, {});
      }
    },

    getRating(appId) {
      const ratings = Storage.get(this.STORAGE_KEY, {});
      return ratings[appId] || 0;
    },

    setRating(appId, rating) {
      const ratings = Storage.get(this.STORAGE_KEY, {});
      ratings[appId] = rating;
      Storage.set(this.STORAGE_KEY, ratings);
      OS.emit('ratings:updated', { appId, rating });
    },

    getAverageRating(appId) {
      // In a real implementation, this would come from a backend
      // For now, just return the user's rating
      return this.getRating(appId);
    },

    getRatingCount(appId) {
      // In a real implementation, this would come from a backend
      return this.getRating(appId) > 0 ? 1 : 0;
    },

    showRatingModal(appId, appName) {
      if (!this._modal) {
        this._createModal();
      }

      const title = this._modal.querySelector('h3');
      title.textContent = `Rate ${appName}`;

      const currentRating = this.getRating(appId);
      const stars = this._modal.querySelectorAll('.star');
      stars.forEach((star, index) => {
        star.classList.toggle('active', index < currentRating);
      });

      // Store current app ID
      this._currentAppId = appId;

      this._backdrop.classList.add('show');
      this._modal.classList.add('show');
    },

    hideRatingModal() {
      if (this._backdrop) this._backdrop.classList.remove('show');
      if (this._modal) this._modal.classList.remove('show');
    },

    _createModal() {
      this._backdrop = document.createElement('div');
      this._backdrop.className = 'rating-backdrop';
      this._backdrop.addEventListener('click', () => this.hideRatingModal());
      document.body.appendChild(this._backdrop);

      this._modal = document.createElement('div');
      this._modal.className = 'rating-modal';
      this._modal.innerHTML = `
        <h3>Rate App</h3>
        <p>Help others discover great apps by sharing your experience.</p>
        <div class="rating-stars">
          ${[1, 2, 3, 4, 5].map(i => `
            <span class="star" data-rating="${i}">
              <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                <path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/>
              </svg>
            </span>
          `).join('')}
        </div>
        <div class="actions">
          <button class="button ghost" onclick="OS.Ratings.hideRatingModal()">Cancel</button>
          <button class="button primary" onclick="OS.Ratings.submitRating()">Submit</button>
        </div>
      `;
      document.body.appendChild(this._modal);

      // Setup star hover effects
      const stars = this._modal.querySelectorAll('.star');
      stars.forEach((star, index) => {
        star.addEventListener('click', () => {
          stars.forEach((s, i) => {
            s.classList.toggle('active', i <= index);
          });
        });
        
        star.addEventListener('mouseenter', () => {
          stars.forEach((s, i) => {
            s.classList.toggle('active', i <= index);
          });
        });
      });

      // Close on escape
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') this.hideRatingModal();
      });
    },

    submitRating() {
      if (!this._currentAppId) return;
      
      const activeStars = this._modal.querySelectorAll('.star.active');
      const rating = activeStars.length;
      
      this.setRating(this._currentAppId, rating);
      this.hideRatingModal();
      
      OS.toast(`Rated with ${rating} star${rating !== 1 ? 's' : ''}!`);
      
      // Update UI
      this.injectRatingDisplays();
    },

    injectRatingDisplays(container = document) {
      const appCards = container.querySelectorAll('.app-card, .rail-card, .featured-card, .result-row');
      appCards.forEach(card => {
        const appId = this._getAppId(card);
        if (!appId) return;

        // Remove existing rating display
        const existing = card.querySelector('.rating-display');
        if (existing) existing.remove();

        const rating = this.getAverageRating(appId);
        const count = this.getRatingCount(appId);

        if (rating === 0 && count === 0) return;

        const display = document.createElement('div');
        display.className = 'rating-display';
        display.innerHTML = `
          <span class="stars">${'★'.repeat(Math.floor(rating))}${'☆'.repeat(5 - Math.floor(rating))}</span>
          <span class="rating-count">(${count})</span>
        `;

        // Try to insert in a good location
        const meta = card.querySelector('.card-meta, .meta');
        if (meta) {
          meta.appendChild(display);
        } else {
          card.appendChild(display);
        }
      });

      // Add rate buttons
      this._injectRateButtons(container);
    },

    _injectRateButtons(container = document) {
      const appCards = container.querySelectorAll('.app-card, .rail-card, .featured-card, .result-row');
      appCards.forEach(card => {
        if (card.querySelector('.rate-btn')) return;

        const appId = this._getAppId(card);
        if (!appId) return;

        const appName = card.querySelector('h3, h2')?.textContent.trim() || 'this app';

        const btn = document.createElement('button');
        btn.className = 'rate-btn';
        btn.textContent = 'Rate';
        btn.setAttribute('data-app-id', appId);
        btn.setAttribute('data-app-name', appName);
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.showRatingModal(appId, appName);
        });

        const actions = card.querySelector('.card-actions, .actions');
        if (actions) {
          actions.appendChild(btn);
        }
      });
    },

    _getAppId(card) {
      // Same logic as Favorites
      const href = card.getAttribute('href');
      if (href && href.includes('/apps/')) {
        const match = href.match(/\/apps\/([^\/]+)/);
        if (match) return match[1];
      }

      const dataId = card.getAttribute('data-app-id') ||
                    card.getAttribute('data-id') ||
                    card.getAttribute('id');
      if (dataId) return dataId;

      const title = card.querySelector('h3, h2, .card-identity h3');
      if (title) {
        const text = title.textContent.trim().toLowerCase().replace(/\s+/g, '-');
        return text;
      }

      return null;
    }
  };

  // ============================================================================
  // MULTI-LANGUAGE SUPPORT (P3)
  // ============================================================================

  const I18n = {
    STORAGE_KEY: 'language',
    
    languages: {
      en: { name: 'English', native: 'English' },
      es: { name: 'Spanish', native: 'Español' },
      zh: { name: 'Chinese', native: '中文' },
      ja: { name: 'Japanese', native: '日本語' },
      bn: { name: 'Bengali', native: 'বাংলা' },
      ar: { name: 'Arabic', native: 'العربية' },
      fr: { name: 'French', native: 'Français' },
      de: { name: 'German', native: 'Deutsch' }
    },

    // NOTE: translations live in locales/*.json and are served by the
    // standalone runtime (src/js/i18n.js, window.OmniI18n). This module used
    // to carry its own hardcoded en/es table with flat keys, which drifted
    // from the canonical dotted keys and left every other language showing
    // English. It now delegates: t()/setLanguage() proxy OmniI18n, and the
    // language switcher below is the only UI this module owns.

    init() {
      this._loadLanguage();
      this._injectUI();
      this._applyTranslations();
      // Sync the chrome once the locale bundles arrive (and on every later
      // language switch): init runs before OmniI18n finishes fetching.
      //
      // Do NOT call _applyTranslations() here. OmniI18n.apply() dispatches
      // i18n:changed as its last step, so re-applying from this listener
      // re-enters apply() forever — it measured ~610 dispatches per boot and
      // ended in an uncaught "Maximum call stack size exceeded" on every
      // page. The [data-i18n*] markup is already translated by the apply()
      // that fired this event; only the widgets this module owns (the
      // language <select>s and the document title) need refreshing.
      window.addEventListener('i18n:changed', () => {
        this.currentLanguage = (window.OmniI18n && window.OmniI18n.language) || this.currentLanguage;
        this.syncLanguageControls();
        this._applyTitle();
      });
    },

    _loadLanguage() {
      if (window.OmniI18n && window.OmniI18n.language) {
        this.currentLanguage = window.OmniI18n.language;
        return;
      }
      const saved = Storage.get(this.STORAGE_KEY);
      if (saved && this.languages[saved]) {
        this.currentLanguage = saved;
      } else {
        // Try to detect browser language
        const browserLang = navigator.language.split('-')[0];
        this.currentLanguage = this.languages[browserLang] ? browserLang : 'en';
      }
    },

    /* Language UI.
     *
     * Two always-visible controls replace the old cramped <select>:
     *
     *   1. a globe button + dropdown in the header row (`.nav-controls`),
     *      rendered at *every* breakpoint — the previous header copy was
     *      hidden below 1100px, so on a phone the only way to change
     *      language was to open the hamburger drawer and dig for it;
     *   2. a language pill row in the home-page hero, so the choice sits
     *      right on the main screen with nothing to open.
     *
     * Both drive the same setLanguage() path and are kept in step by
     * syncLanguageControls(). Their styles live in the design system
     * (`.lang-picker` / `.lang-menu` / `.hero-lang` in components.css)
     * rather than an injected <style> tag, so they are themeable, survive a
     * strict CSP and cannot flash unstyled.
     */
    _injectUI() {
      this._buildHeaderPicker();
      this._buildHeroPicker();
      this._bindDismissal();
      this.syncLanguageControls();
    },

    _globeSvg() {
      return '<svg class="icon-globe" aria-hidden="true" viewBox="0 0 24 24">' +
        '<circle cx="12" cy="12" r="9"/><path d="M3.2 12h17.6"/>' +
        '<path d="M12 3.2c2.4 2.4 3.7 5.5 3.7 8.8s-1.3 6.4-3.7 8.8c-2.4-2.4-3.7-5.5-3.7-8.8S9.6 5.6 12 3.2Z"/>' +
        '</svg>';
    },

    _buildHeaderPicker() {
      const controls = document.querySelector('.nav-controls');
      if (!controls || controls.querySelector('[data-lang-picker]')) return;

      const picker = document.createElement('div');
      picker.className = 'lang-picker';
      picker.setAttribute('data-lang-picker', '');

      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.id = 'langToggle';
      toggle.className = 'icon-button lang-toggle';
      toggle.setAttribute('aria-haspopup', 'listbox');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.innerHTML = this._globeSvg() + '<span class="lang-code">EN</span>';

      const menu = document.createElement('div');
      menu.className = 'lang-menu';
      menu.id = 'langMenu';
      menu.setAttribute('role', 'listbox');
      menu.setAttribute('data-i18n-aria-label', 'nav.language');
      menu.setAttribute('tabindex', '-1');
      menu.hidden = true;
      toggle.setAttribute('aria-controls', menu.id);

      Object.entries(this.languages).forEach(([code, lang]) => {
        const option = document.createElement('button');
        option.type = 'button';
        option.className = 'lang-option';
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', 'false');
        option.setAttribute('data-lang', code);
        // textContent, never innerHTML: native language names are exactly the
        // kind of string that must not be parsed as markup.
        const native = document.createElement('span');
        native.className = 'lang-native';
        native.textContent = lang.native;
        const name = document.createElement('span');
        name.className = 'lang-name';
        name.textContent = lang.name;
        option.appendChild(native);
        option.appendChild(name);
        option.addEventListener('click', () => {
          this.setLanguage(code);
          this._closeMenu(true);
        });
        menu.appendChild(option);
      });

      toggle.addEventListener('click', (event) => {
        // Stop the document-level dismissal handler (bound below) from
        // immediately re-closing what this click just opened.
        event.stopPropagation();
        if (menu.hidden) this._openMenu(); else this._closeMenu(true);
      });
      toggle.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          this._openMenu();
        }
      });
      menu.addEventListener('keydown', (event) => this._menuKeys(event));

      picker.appendChild(toggle);
      picker.appendChild(menu);
      controls.appendChild(picker);
      this._toggle = toggle;
      this._menu = menu;
    },

    /* Home-page hero pill row — the "main screen" copy of the switcher. */
    _buildHeroPicker() {
      if (!document.body || document.body.dataset.page !== 'home') return;
      const hero = document.querySelector('.hero-inner') || document.querySelector('.hero');
      if (!hero || hero.querySelector('[data-hero-lang]')) return;

      const row = document.createElement('div');
      row.className = 'hero-lang';
      row.setAttribute('data-hero-lang', '');
      row.setAttribute('role', 'group');
      row.setAttribute('data-i18n-aria-label', 'nav.language');

      const label = document.createElement('span');
      label.className = 'hero-lang-label';
      label.innerHTML = this._globeSvg();
      const labelText = document.createElement('span');
      labelText.setAttribute('data-i18n', 'nav.language');
      labelText.textContent = 'Language';
      label.appendChild(labelText);

      const options = document.createElement('div');
      options.className = 'hero-lang-options';
      Object.entries(this.languages).forEach(([code, lang]) => {
        const pill = document.createElement('button');
        pill.type = 'button';
        pill.className = 'lang-pill';
        pill.setAttribute('data-lang', code);
        pill.setAttribute('aria-pressed', 'false');
        pill.setAttribute('title', lang.name);
        pill.textContent = lang.native;
        pill.addEventListener('click', () => this.setLanguage(code));
        options.appendChild(pill);
      });

      row.appendChild(label);
      row.appendChild(options);
      // Between the calls to action and the search bar: on the first screen,
      // without competing with the headline.
      const anchor = hero.querySelector('.hero-search') || hero.querySelector('.source-box');
      if (anchor) hero.insertBefore(row, anchor); else hero.appendChild(row);
    },

    _openMenu() {
      const menu = this._menu;
      const toggle = this._toggle;
      if (!menu || !toggle) return;
      menu.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      const picker = toggle.closest('.lang-picker');
      if (picker) picker.classList.add('is-open');
      const active = menu.querySelector('[aria-selected="true"]') || menu.firstElementChild;
      if (active && active.focus) active.focus();
    },

    _closeMenu(refocus) {
      const menu = this._menu;
      const toggle = this._toggle;
      if (!menu || menu.hidden) return;
      menu.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      const picker = toggle.closest('.lang-picker');
      if (picker) picker.classList.remove('is-open');
      if (refocus && toggle.focus) toggle.focus();
    },

    _menuKeys(event) {
      const menu = this._menu;
      if (!menu) return;
      const options = Array.prototype.slice.call(menu.querySelectorAll('.lang-option'));
      if (!options.length) return;
      const index = options.indexOf(document.activeElement);
      const key = event.key;
      if (key === 'Escape') { event.preventDefault(); this._closeMenu(true); return; }
      if (key === 'Tab') { this._closeMenu(false); return; }
      if (key === 'ArrowDown') { event.preventDefault(); options[(index + 1) % options.length].focus(); return; }
      if (key === 'ArrowUp') { event.preventDefault(); options[(index - 1 + options.length) % options.length].focus(); return; }
      if (key === 'Home') { event.preventDefault(); options[0].focus(); return; }
      if (key === 'End') { event.preventDefault(); options[options.length - 1].focus(); }
    },

    _bindDismissal() {
      document.addEventListener('click', (event) => {
        const picker = this._toggle && this._toggle.closest('.lang-picker');
        if (picker && !picker.contains(event.target)) this._closeMenu(false);
      });
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') this._closeMenu(false);
      });
      window.addEventListener('resize', () => this._closeMenu(false), { passive: true });
    },

    /* Keep every language control in step with the active locale. */
    syncLanguageControls() {
      const current = this.currentLanguage;
      const meta = this.languages[current] || this.languages.en;
      document.querySelectorAll('.lang-option').forEach((option) => {
        const on = option.getAttribute('data-lang') === current;
        option.setAttribute('aria-selected', String(on));
        option.classList.toggle('is-active', on);
      });
      document.querySelectorAll('.lang-pill').forEach((pill) => {
        const on = pill.getAttribute('data-lang') === current;
        pill.setAttribute('aria-pressed', String(on));
        pill.classList.toggle('is-active', on);
      });
      document.querySelectorAll('.lang-code').forEach((node) => {
        node.textContent = String(current).toUpperCase();
      });
      if (this._toggle) {
        // Set by hand rather than data-i18n-aria-label so the label can carry
        // the active language: OmniI18n.apply() would flatten it to just
        // "Language" on every pass.
        const base = this.t('nav.language');
        const text = (base === 'nav.language' ? 'Language' : base) + ': ' + meta.native;
        this._toggle.setAttribute('aria-label', text);
        this._toggle.title = text;
      }
      document.documentElement.setAttribute('data-language', current);
    },

    /* Back-compat alias: earlier revisions exposed syncLanguageSelects(). */
    syncLanguageSelects() {
      this.syncLanguageControls();
    },

    setLanguage(code) {
      // The standalone runtime owns JSON locales (including RTL languages).
      // Keep this legacy facade compatible for existing feature consumers.
      if (!this.languages[code]) return;
      this.currentLanguage = code;
      Storage.set(this.STORAGE_KEY, code);
      this.syncLanguageControls();
      if (window.OmniI18n && window.OmniI18n.setLanguage) {
        // setLanguage re-applies the page itself and emits i18n:changed,
        // which this module listens for (see init) to finish the update.
        window.OmniI18n.setLanguage(code);
        OS.emit('language:changed', code);
        return;
      }
      this._applyTranslations();
      OS.emit('language:changed', code);
    },

    getLanguage() {
      return this.currentLanguage;
    },

    // Delegate to the runtime so dotted canonical keys resolve. When the
    // locale bundles have not loaded yet this echoes the key back to the
    // caller — the same contract OS.t has, minus the English fallback.
    t(key, params = {}) {
      if (window.OmniI18n && window.OmniI18n.loaded && window.OmniI18n.loaded('en')) {
        return window.OmniI18n.t(key, params);
      }
      return key;
    },

    _applyTranslations() {
      // OmniI18n.apply owns every [data-i18n*] attribute variant, so defer
      // to it entirely — including the no-locale state, where the static
      // English markup is the correct content and must not be replaced by
      // raw keys.
      if (window.OmniI18n && window.OmniI18n.loaded && window.OmniI18n.loaded('en')) {
        window.OmniI18n.apply();
      }
      this._applyTitle();
    },

    // Title-only refresh. Safe to call from the i18n:changed listener
    // because it never calls back into OmniI18n.apply().
    _applyTitle() {
      // Static pages (analytics, status, compare, install, search and the
      // per-app pages) ship their own titles, so only the home page gets the
      // localized site title — guarded on a loaded EN bundle so the key never
      // leaks into the tab.
      const page = document.body && document.body.dataset.page;
      if (page === 'home' && window.OmniI18n && window.OmniI18n.loaded && window.OmniI18n.loaded('en')) {
        document.title = window.OmniI18n.t('site.title', { site: 'OmniSource' });
      }
    }
  };

  // ============================================================================
  // TOAST NOTIFICATIONS (Utility)
  // ============================================================================

  /* Toasts.
   *
   * js/core.js already owns a working implementation: one shared #toast node
   * revealed by adding `.show` (components.css ships `.toast` at opacity 0
   * and `.toast.show` at opacity 1). This module used to overwrite OS.toast
   * with its own renderer, which appended `<div class="toast">` nodes to a
   * #toast-container and relied on an inline `slideIn` animation to make
   * them visible. Because the shared `.toast` rule sets `opacity: 0` and the
   * inline style never overrode it — and a CSS animation without
   * `animation-fill-mode` snaps back to the base value the instant it ends —
   * every toast on the site was invisible: copy confirmations, "Saved for
   * later", ratings, the PWA install notice and the service-worker update
   * notice all fired and showed nothing.
   *
   * So: delegate to core.js whenever it is present, and keep a standalone
   * fallback (for embeds that ship features.js without core.js) that drives
   * the exact same `.toast` / `.toast.show` contract instead of inventing a
   * second, invisible one.
   */
  const coreToast = typeof OS.toast === 'function' ? OS.toast.bind(OS) : null;

  const Toast = {
    init() {
      // Nothing to inject: the shared #toast node is created on demand by
      // whichever implementation runs (core.js, or _fallback below).
    },

    show(message, options = {}) {
      if (message == null || message === '') return null;
      if (coreToast) return coreToast(String(message));
      return Toast._fallback(String(message), options);
    },

    /** Standalone renderer, used only when js/core.js is not on the page. */
    _fallback(message, options = {}) {
      const duration = options.duration || 2600;
      let node = document.getElementById('toast');
      if (!node) {
        node = document.createElement('div');
        node.className = 'toast';
        node.id = 'toast';
        node.setAttribute('role', 'status');
        node.setAttribute('aria-live', 'polite');
        node.innerHTML =
          '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg><span></span>';
        document.body.appendChild(node);
      }
      const label = node.querySelector('span');
      if (label) label.textContent = message;
      node.classList.add('show');
      clearTimeout(Toast._timer);
      Toast._timer = setTimeout(() => node.classList.remove('show'), duration);
      return node;
    }
  };

  // The slideIn/slideOut keyframes this module used to inject are gone: the
  // shared `.toast` / `.toast.show` rules in components.css (with the
  // `os-toast-in` keyframe) are now the only toast animation, so there is
  // exactly one source of truth for it.

  // ============================================================================
  // INITIALIZE ALL FEATURES
  // ============================================================================

  const init = () => {
    // Initialize utilities
    Toast.init();
    // Only claim OS.toast when core.js did not already provide one; the
    // delegate above keeps `OS.toast(msg)` working either way and never
    // shadows the shared #toast renderer the stylesheet is built for.
    if (!coreToast) OS.toast = Toast.show;

    // Initialize features
    Favorites.init();
    Collections.init();
    QRCode.init();
    SearchUI.init();
    Ratings.init();
    I18n.init();

    // Make features accessible
    OS.Favorites = Favorites;
    OS.Collections = Collections;
    OS.QRCode = QRCode;
    // Deliberately OS.SearchUI, not OS.Search: the search engine lives in
    // js/core.js and site.js/core.js call OS.Search.load()/search()/highlight().
    OS.SearchUI = SearchUI;
    OS.Ratings = Ratings;
    OS.I18n = I18n;

    // Auto-initialize features on page load. The home catalog renders
    // asynchronously (js/site.js), so these injections only touch cards that
    // already exist at DOMContentLoaded — the renderer adds its own heart
    // button to every card it draws.
    document.addEventListener('DOMContentLoaded', () => {
      Favorites.injectButtons();
      Favorites.updateCount();
      Ratings.injectRatingDisplays();
    });
  };

  // Run on DOM ready or immediately if already loaded
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Export for module usage
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { Favorites, Collections, QRCode, SearchUI, Ratings, I18n };
  }
})();
