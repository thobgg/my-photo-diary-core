/* MPD – Album Init
 * Entry point: loadAlbum, resize handler, DOMContentLoaded → initEditMode
 * All logic lives in album-state.js + album-render.js + album-edit.js +
 * album-drag.js + album-save.js + album-insert.js + album-ui.js + album-map.js
 */

// ============================================
// Mobile kebab (album actions on phone)
// ============================================
function toggleAlbumKebab(event) {
    if (event) event.stopPropagation();
    const menu = document.getElementById('album-kebab-menu');
    if (!menu) return;
    if (menu.hasAttribute('hidden')) menu.removeAttribute('hidden');
    else menu.setAttribute('hidden', '');
}
function closeAlbumKebab() {
    const menu = document.getElementById('album-kebab-menu');
    if (menu) menu.setAttribute('hidden', '');
}
// Click-away + ESC
document.addEventListener('click', (e) => {
    if (!e.target.closest('#album-kebab-menu') && !e.target.closest('#album-kebab-btn')) {
        closeAlbumKebab();
    }
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAlbumKebab();
});

// ============================================
// Mobile hero (collapsing toolbar)
// ============================================
// Populates the hero (title, meta, cover background) and sets up an
// IntersectionObserver that toggles body.hero-collapsed once the user
// has scrolled past the hero. Desktop ignores this entirely via CSS.
function syncAlbumHero(photoCount) {
    const hero = document.getElementById('album-hero');
    if (!hero || !albumData) return;

    const titleEl = document.getElementById('album-hero-title');
    const metaEl  = document.getElementById('album-hero-meta');
    const coverEl = document.getElementById('album-hero-cover');
    const blurEl  = document.getElementById('album-hero-blur');

    if (titleEl) titleEl.textContent = albumData.meta.title || '';
    if (metaEl) {
        const y = albumData.meta.year;
        const el = photoCount === 1 ? '1 Element' : `${photoCount} Elemente`;
        metaEl.textContent = y ? `${y} · ${el}` : el;
    }
    if (coverEl) {
        // Cover URL with multiple fallbacks:
        // 1) element that matches meta.thumbnail (preferred)
        // 2) any element with thumbnails.xl (photo/video)
        // 3) manual URL from meta.thumbnail (last resort)
        const elems = albumData.elements || [];
        let coverUrl = null;

        if (albumData.meta.thumbnail) {
            const match = elems.find(e => e.file === albumData.meta.thumbnail && e.thumbnails?.xl);
            if (match) coverUrl = `${API_BASE}${match.thumbnails.xl}`;
        }
        if (!coverUrl) {
            const any = elems.find(e => e.thumbnails?.xl);
            if (any) coverUrl = `${API_BASE}${any.thumbnails.xl}`;
        }
        if (!coverUrl && albumData.meta.thumbnail) {
            coverUrl = `${API_BASE}/api/thumbnail/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/${encodeURIComponent(albumData.meta.thumbnail)}?size=xl`;
        }

        if (coverUrl) {
            const bg = `url("${coverUrl}")`;
            coverEl.style.backgroundImage = bg;
            if (blurEl) blurEl.style.backgroundImage = bg;
        } else {
            coverEl.style.backgroundImage = '';
            if (blurEl) blurEl.style.backgroundImage = '';
            console.info('[MPD hero] No cover found — albumData.meta.thumbnail:', albumData.meta.thumbnail, 'elements with xl thumb:', elems.filter(e => e.thumbnails?.xl).length);
        }
    }

    // Initialize observer only once.
    if (!hero.dataset.heroObserverReady) {
        hero.dataset.heroObserverReady = '1';
        const sentinel = hero.querySelector('.album-hero-sentinel');
        if (sentinel && 'IntersectionObserver' in window) {
            // Sentinel sits at the bottom edge of the hero. When it has
            // scrolled out of the viewport upwards → hero is gone, header
            // becomes sticky → hero-collapsed folds in the title row.
            const obs = new IntersectionObserver(([entry]) => {
                document.body.classList.toggle('hero-collapsed', !entry.isIntersecting);
            }, { threshold: 0 });
            obs.observe(sentinel);
        }
    }
}

// Dezenter Hinweis unter dem Titel: „3 Dateien im Ordner sind noch nicht
// im Album" — nur wenn es welche gibt und das Konto kuratieren darf.
// Klick oeffnet den Einfuege-Dialog, der genau diese Dateien listet.
// Nach dem Einfuegen zaehlt refreshUnassignedHint() neu (album-insert.js).
function albumUnassignedHint(count) {
    const meta = document.getElementById('album-meta');
    if (!meta) return;
    let el = document.getElementById('album-unassigned-hint');
    if (!count || window.MPD_READ_ONLY) { if (el) el.remove(); return; }
    if (!el) {
        el = document.createElement('button');
        el.id = 'album-unassigned-hint';
        el.className = 'album-unassigned-hint';
        el.type = 'button';
        el.onclick = () => { if (typeof openAddPhotoModal === 'function') openAddPhotoModal(); };
        meta.insertAdjacentElement('afterend', el);
    }
    const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
    el.textContent = count === 1
        ? _t('album.unassigned_hint_one', null, '1 Datei im Ordner ist noch nicht im Album')
        : _t('album.unassigned_hint', { count }, count + ' Dateien im Ordner sind noch nicht im Album');
    el.title = _t('album.unassigned_hint_title', null, 'Klicken zum Einfügen');
}

async function refreshUnassignedHint() {
    try {
        const r = await fetch(`${API_BASE}/api/album/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/unassigned-photos`);
        if (!r.ok) return;
        const d = await r.json();
        albumUnassignedHint(d.count);
    } catch (_) {}
}

async function loadAlbum(name) {
    try {
        const response = await fetch(`${API_BASE}/api/album/${encodeURIComponent(albumSpace)}/${encodeURIComponent(name)}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        albumData = await response.json();
        lastSavedElementCount = (albumData.elements || []).length;
        window.MPD_READ_ONLY = !!albumData.read_only;
        loadModuleEntitlements();  // Tours-FAB soft-gaten (eingebettete Tours rendern weiter)
        // Hide edit button + mobile primary FAB when read_only
        // (FAB onclick=editToggle.click() would still fire even on display:none)
        const editToggle  = document.getElementById('edit-mode-toggle');
        const primaryFab  = document.getElementById('album-primary-fab');
        const display     = albumData.read_only ? 'none' : '';
        if (editToggle) editToggle.style.display = display;
        if (primaryFab) primaryFab.style.display = display;
        renderAlbum();
        fetchRecycleStatus();
        albumUnassignedHint(albumData.unassigned_count);

        // PDX v1.4: open empty album (freshly created for curated mixed workflow)
        // directly in edit mode — otherwise the user just sees an empty page
        // and has to click "Edit" first.
        if (!albumData.read_only && (albumData.elements || []).length === 0) {
            isEditMode = true;
            updateEditModeUI();
        }
    } catch (error) {
        showError(error.message);
    }
}

// Freigeschaltete Module (aus /health). Soft-Gating: nur die „Tour einfügen"-
// FAB-Aktion wird bei gesperrtem Tours-Modul ausgeblendet; bereits eingebettete
// Tour-Elemente rendern unabhängig davon weiter (album-render.js).
async function loadModuleEntitlements() {
    try {
        const health = await fetch(`${API_BASE}/health`).then(r => r.json());
        const mods = Array.isArray(health.modules) ? health.modules : null;
        window.MPD_MODULES = mods;
        if (mods && !mods.includes('tours')) {
            const el = document.getElementById('fab-item-tour');
            if (el) el.style.display = 'none';
        }
    } catch (_) { /* /health-Fehler → nichts ausblenden */ }
}

// Resize with debounce — only react to width changes.
// The Android keyboard only changes the height and would otherwise trigger
// a re-render that kills the contentEditable focus (keyboard flickers).
let lastWidth = window.innerWidth;
window.addEventListener('resize', () => {
    // Vollbild aendert die Fenstermasse. Ein Neuaufbau nimmt das Element,
    // das gerade im Vollbild liegt, aus dem Dokument — und der Browser
    // beendet Vollbild dann sofort, rund 200 ms nach dem Klick. Genau so
    // hat es sich gezeigt (15.09.2026: "fullscreen bricht bei
    // Aufbau sofort ab").
    //
    // lastWidth wird dabei BEWUSST nicht mitgezogen: Beim Verlassen kehrt
    // die Breite auf den alten Wert zurueck, der Vergleich oben greift,
    // und es gibt auch dann keinen unnoetigen Neuaufbau.
    if (document.fullscreenElement) return;
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
        // Noch einmal pruefen: resize kann feuern, BEVOR der Browser
        // document.fullscreenElement gesetzt hat.
        if (document.fullscreenElement) return;
        if (albumData) {
            const scrollPos = window.scrollY;
            if (isEditMode) {
                enableEditMode(); // re-render + re-init D&D + applyJustifiedSizes
            } else {
                renderAlbum();
            }
            requestAnimationFrame(() => window.scrollTo(0, scrollPos));
        }
    }, 200);
});

// ============================================
// Initialization
// ============================================

if (!albumName) {
    showError(window.MPD_I18N ? window.MPD_I18N.t('album.no_album_selected') : 'No album selected');
} else {
    // Save current album URL so index.html can redirect back if the WebView
    // Activity is recreated by Android on orientation change (JS context lost).
    try { localStorage.setItem('mpd-resume', JSON.stringify({ url: location.href, ts: Date.now() })); } catch(e) {}
    loadAlbum(albumName);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initEditMode);
} else {
    initEditMode();
}
