/* MPD – Durchlauf-Ansicht (08.09.2026)
 *
 * Alles Unzugeordnete eines Bestands: Dateien, die in keinem album.json
 * stehen — Kamera-Uploads im Durchlauf `2026`, per SMB kopierte Dateien,
 * lose Dateien in Albumordnern. Chronologisch in Tagesgruppen, Auswahl per
 * Klick, dann „Einsortieren nach…" (import-file je Datei) oder „Löschen".
 * Ohne Auswahl je Durchlauf-Jahr „Rest ins Jahresalbum" (year-album).
 *
 * Server: GET /api/timeline?space=&referenced=false (Cursor), Vertrag in
 * docs/API_CHANGES.md 08.09.2026. Die Lightbox laeuft nur zum Ansehen
 * (MPD_READ_ONLY) — geloescht wird ueber die Leiste, nicht im Bild.
 */

window.MPD_READ_ONLY = true;
if (typeof Lightbox !== 'undefined') {
    // lightbox-exif.js ist hier nicht geladen (braucht Leaflet); ohne Stub
    // wuerde show() ins Leere greifen.
    Lightbox.prototype.loadMetadata = async function () {};
}
albumName  = '';
albumSpace = '';
goBack = function () { window.location.href = '/'; };

const _dt = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;

const DL = {
    space   : null,          // 'shared' | 'personal'
    rights  : {},            // aus /api/whoami
    items   : [],            // Zeitstrahl-Eintraege (nur referenced=false)
    cursor  : null,
    total   : 0,
    loading : false,
    done    : false,
    selected: new Set(),     // Schluessel space/album/file
    observer: null,
    recycle : null,
};

const _key = it => `${it.space}/${it.album}/${it.file}`;
const _isYear = name => /^\d{4}$/.test(name || '');

/* ── Start ─────────────────────────────────────────────────────────── */

async function dlInit() {
    let whoami = {};
    try { whoami = await fetch('/api/whoami').then(r => r.json()); } catch (_) {}
    DL.rights = whoami.rights || {};
    const allowed = ['shared', 'personal'].filter(s => (DL.rights[s] || []).includes('contribute'));

    const params = new URLSearchParams(location.search);
    let want = params.get('space') || localStorage.getItem('mpd-durchlauf-space') || 'shared';
    if (!allowed.includes(want)) want = allowed[0] || null;

    const toggle = document.getElementById('dl-space-toggle');
    toggle.querySelectorAll('.dl-space-btn').forEach(btn => {
        const s = btn.dataset.space;
        btn.hidden = !allowed.includes(s);
        btn.onclick = () => dlSetSpace(s);
    });
    if (allowed.length < 2) toggle.style.display = 'none';

    if (!want) {
        document.getElementById('dl-status').textContent = _dt('durchlauf.no_access', null, 'Dieses Konto darf hier nicht beitragen.');
        return;
    }
    document.getElementById('dl-bar-all').onclick    = () => dlSelectAll(true);
    document.getElementById('dl-bar-none').onclick   = () => dlSelectAll(false);
    document.getElementById('dl-bar-import').onclick = dlImportSelected;
    document.getElementById('dl-bar-delete').onclick = dlDeleteSelected;

    DL.observer = new IntersectionObserver(entries => {
        if (entries.some(e => e.isIntersecting)) dlLoadMore();
    }, { rootMargin: '600px 0px' });
    DL.observer.observe(document.getElementById('dl-sentinel'));

    dlSetSpace(want);
}

function dlSetSpace(space) {
    DL.space = space;
    try { localStorage.setItem('mpd-durchlauf-space', space); } catch (_) {}
    history.replaceState(null, '', `/durchlauf?space=${space}`);
    document.querySelectorAll('.dl-space-btn').forEach(b => b.classList.toggle('active', b.dataset.space === space));
    DL.items = []; DL.cursor = null; DL.done = false; DL.total = 0;
    DL.selected.clear();
    document.getElementById('dl-groups').innerHTML = '';
    dlUpdateBar();
    dlFetchRecycle();
    dlUnscanned();
    dlLoadMore();
}

// Ordner ohne album.json im Bestand (Scan-Kandidaten): eine Zeile je
// Ordner mit Sprung in den Scan-Dialog der Startseite. Nur mit
// Kuratorrecht — nur der darf Alben anlegen.
async function dlUnscanned() {
    const box = document.getElementById('dl-unscanned');
    box.innerHTML = ''; box.hidden = true;
    if (!(DL.rights[DL.space] || []).includes('curate')) return;
    try {
        const d = await fetch('/api/albums/without-json').then(r => r.ok ? r.json() : { folders: [] });
        const rows = (d.folders || []).filter(f => f.space === DL.space);
        if (!rows.length) return;
        for (const f of rows) {
            const row = document.createElement('div'); row.className = 'dl-unscanned-row';
            const txt = document.createElement('span');
            txt.textContent = _dt('durchlauf.unscanned_row', { folder: f.folder_name, count: f.media_count },
                                  `Ordner „${f.folder_name}“ ohne Album — ${f.media_count} Dateien`);
            const a = document.createElement('a'); a.href = '/?scan=1'; a.className = 'dl-unscanned-link';
            a.textContent = _dt('durchlauf.unscanned_action', null, 'Album anlegen');
            row.append(txt, a); box.appendChild(row);
        }
        box.hidden = false;
    } catch (_) {}
}

async function dlFetchRecycle() {
    DL.recycle = null;
    try {
        const r = await fetch(`/api/recycle-status/${DL.space}`);
        if (r.ok) DL.recycle = !!(await r.json()).recycled;
    } catch (_) {}
}

/* ── Laden + Rendern ───────────────────────────────────────────────── */

async function dlLoadMore() {
    if (DL.loading || DL.done || !DL.space) return;
    DL.loading = true;
    const status = document.getElementById('dl-status');
    status.textContent = _dt('durchlauf.loading', null, 'Lade…');
    try {
        // folder=durchlauf,album: kopierte Ordner ohne album.json sind kein
        // Rest, sondern Scan-Kandidaten — die stehen oben als Zeile (dlUnscanned).
        const q = new URLSearchParams({ space: DL.space, referenced: 'false', folder: 'durchlauf,album', limit: '200' });
        if (DL.cursor) q.set('cursor', DL.cursor);
        const r = await fetch(`/api/timeline?${q}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = await r.json();
        DL.total  = d.total || 0;
        DL.cursor = d.next_cursor;
        DL.done   = !d.next_cursor;
        dlAppend(d.items || []);
        dlUpdateMeta();
        status.textContent = DL.items.length === 0
            ? _dt('durchlauf.empty', null, 'Nichts Unzugeordnetes — alles ist in Alben.')
            : (DL.done ? '' : _dt('durchlauf.more', null, '… weitere laden'));
    } catch (e) {
        status.textContent = e.message;
    } finally {
        DL.loading = false;
    }
}

function dlUpdateMeta() {
    const n = DL.total;
    document.getElementById('dl-meta').textContent = n === 1
        ? _dt('durchlauf.meta_count_one', null, '1 Datei')
        : _dt('durchlauf.meta_count', { count: n }, `${n} Dateien`);
    dlRenderYearBar();
}

function dlAppend(items) {
    const groups = document.getElementById('dl-groups');
    for (const it of items) {
        const idx = DL.items.push(it) - 1;
        const day = (it.date || '').slice(0, 10) || '—';
        let grid = groups.querySelector(`.dl-group[data-day="${day}"] .dl-grid`);
        if (!grid) {
            const g = document.createElement('section');
            g.className = 'dl-group'; g.dataset.day = day;
            const head = document.createElement('div'); head.className = 'dl-group-head';
            const dEl = document.createElement('span'); dEl.className = 'dl-group-date'; dEl.textContent = dlFormatDay(day);
            const cEl = document.createElement('span'); cEl.className = 'dl-group-count';
            const pick = document.createElement('button'); pick.type = 'button'; pick.className = 'dl-group-pick';
            pick.textContent = _dt('durchlauf.pick_day', null, 'Tag wählen');
            pick.onclick = () => dlToggleDay(g);
            head.append(dEl, cEl, pick); g.appendChild(head);
            grid = document.createElement('div'); grid.className = 'dl-grid'; g.appendChild(grid);
            groups.appendChild(g);
        }
        grid.appendChild(dlTile(it, idx));
        const g = grid.parentElement;
        g.querySelector('.dl-group-count').textContent = String(grid.children.length);
    }
}

function dlFormatDay(day) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return day;
    try {
        const lang = (window.MPD_I18N && window.MPD_I18N.getLang && window.MPD_I18N.getLang()) || 'de';
        return new Date(day + 'T12:00:00').toLocaleDateString(lang === 'en' ? 'en-GB' : 'de-DE',
            { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
    } catch (_) { return day; }
}

function dlTile(it, idx) {
    const tile = document.createElement('div');
    tile.className = 'dl-tile'; tile.dataset.idx = String(idx); tile.dataset.key = _key(it);
    tile.title = it.file;
    if (it.blurhash && window.MPDBlurHash) {
        const ph = document.createElement('div'); ph.className = 'dl-ph';
        try { const u = window.MPDBlurHash.toDataURL(it.blurhash, 32, 32); if (u) ph.style.backgroundImage = `url(${u})`; } catch (_) {}
        tile.appendChild(ph);
    }
    const img = document.createElement('img');
    img.alt = ''; img.decoding = 'async'; img.loading = 'lazy';
    img.src = it.thumbnails && (it.thumbnails.sm || it.thumbnails.m) || '';
    img.onload = () => img.classList.add('loaded');
    tile.appendChild(img);
    if (!_isYear(it.album)) {
        const b = document.createElement('span'); b.className = 'dl-badge';
        b.textContent = _dt('durchlauf.folder_badge', { album: it.album }, `in ${it.album}`);
        tile.appendChild(b);
    }
    if (it.type === 'video') {
        const v = document.createElement('span'); v.className = 'dl-video'; v.textContent = '▶';
        tile.appendChild(v);
    }
    const z = document.createElement('button'); z.type = 'button'; z.className = 'dl-zoom';
    z.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>';
    z.onclick = (e) => { e.stopPropagation(); dlOpenLightbox(idx); };
    tile.appendChild(z);
    tile.onclick = () => dlToggle(tile);
    return tile;
}

/* ── Auswahl ───────────────────────────────────────────────────────── */

function dlToggle(tile, force) {
    const k = tile.dataset.key;
    const on = force !== undefined ? force : !DL.selected.has(k);
    if (on) DL.selected.add(k); else DL.selected.delete(k);
    tile.classList.toggle('selected', on);
    dlUpdateBar();
}

function dlToggleDay(group) {
    const tiles = [...group.querySelectorAll('.dl-tile')];
    const allOn = tiles.every(t => DL.selected.has(t.dataset.key));
    tiles.forEach(t => dlToggle(t, !allOn));
}

function dlSelectAll(on) {
    document.querySelectorAll('.dl-tile').forEach(t => dlToggle(t, on));
}

function dlUpdateBar() {
    const n = DL.selected.size;
    const bar = document.getElementById('dl-bar');
    bar.hidden = n === 0;
    document.getElementById('dl-bar-count').textContent = n === 1
        ? _dt('durchlauf.selected_one', null, '1 ausgewählt')
        : _dt('durchlauf.selected', { count: n }, `${n} ausgewählt`);
    document.querySelectorAll('.dl-group').forEach(g => {
        const tiles = [...g.querySelectorAll('.dl-tile')];
        const allOn = tiles.length && tiles.every(t => DL.selected.has(t.dataset.key));
        g.querySelector('.dl-group-pick').textContent = allOn
            ? _dt('durchlauf.unpick_day', null, 'Tag abwählen')
            : _dt('durchlauf.pick_day', null, 'Tag wählen');
    });
    dlRenderYearBar();
}

function dlSelectedItems() {
    // Aufnahmereihenfolge (aufsteigend), damit die Grundreihenfolge im
    // Zielalbum stimmt — der Zeitstrahl liefert absteigend.
    return DL.items.filter(it => DL.selected.has(_key(it)))
        .sort((a, b) => (a.date || '').localeCompare(b.date || ''));
}

function dlRemoveKeys(keys) {
    const set = new Set(keys);
    DL.items = DL.items.filter(it => !set.has(_key(it)));
    keys.forEach(k => DL.selected.delete(k));
    document.querySelectorAll('.dl-tile').forEach(t => {
        if (set.has(t.dataset.key)) {
            const g = t.closest('.dl-group');
            t.remove();
            if (g && !g.querySelector('.dl-tile')) g.remove();
            else if (g) g.querySelector('.dl-group-count').textContent = String(g.querySelectorAll('.dl-tile').length);
        }
    });
    DL.total = Math.max(0, DL.total - keys.length);
    dlUpdateMeta();
    dlUpdateBar();
    if (!DL.items.length) document.getElementById('dl-status').textContent =
        DL.done ? _dt('durchlauf.empty', null, 'Nichts Unzugeordnetes — alles ist in Alben.') : '';
    if (DL.items.length < 40) dlLoadMore();
}

/* ── Jahresalbum-Leiste (ohne Auswahl) ─────────────────────────────── */

function dlRenderYearBar() {
    const bar = document.getElementById('dl-yearbar');
    const rights = DL.rights[DL.space] || [];
    const years = [...new Set(DL.items.filter(it => _isYear(it.album)).map(it => it.album))].sort().reverse();
    if (DL.selected.size || !years.length || !rights.includes('contribute')) { bar.hidden = true; bar.innerHTML = ''; return; }
    bar.innerHTML = '';
    for (const y of years) {
        const b = document.createElement('button'); b.type = 'button'; b.className = 'dl-bar-btn';
        b.textContent = _dt('durchlauf.btn_year', { year: y }, `Rest ${y} ins Jahresalbum`);
        b.onclick = () => dlYearAlbum(y);
        bar.appendChild(b);
    }
    bar.hidden = false;
}

async function dlYearAlbum(year) {
    const album = `${year} - das Jahr`;
    const count = DL.items.filter(it => it.album === year).length;
    const ok = await window.mpdConfirm({
        icon: '📚',
        title: _dt('durchlauf.year_confirm_title', null, 'Rest ins Jahresalbum?'),
        body : _dt('durchlauf.year_confirm_body', { count, year, album },
                   `Alle ${count} Dateien aus „${year}“ wandern nach „${album}“.`),
        confirmLabel: _dt('durchlauf.year_confirm_ok', null, 'Verschieben'),
    });
    if (!ok) return;
    try {
        const r = await fetch(`/api/album/${DL.space}/year-album`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ year }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : `HTTP ${r.status}`);
        let msg = _dt('durchlauf.year_done', { moved: d.moved, album: d.album }, `${d.moved} Dateien nach „${d.album}“ verschoben`);
        if (d.duplicates) msg += _dt('durchlauf.import_dupes', { n: d.duplicates }, `, ${d.duplicates} Dubletten aufgeräumt`);
        if (d.renamed) msg += _dt('durchlauf.import_renamed', { n: d.renamed }, `, ${d.renamed} umbenannt`);
        if (d.skipped_exists) msg += _dt('durchlauf.year_skipped', { skipped: d.skipped_exists }, `, ${d.skipped_exists} übersprungen`);
        window.mpdToast(msg, { duration: 5000 });
        dlSetSpace(DL.space);
    } catch (e) {
        window.mpdAlert({ icon: '⚠️', title: _dt('album.delete_failed_title', null, 'Fehler'), body: e.message });
    }
}

/* ── Einsortieren ──────────────────────────────────────────────────── */

async function dlImportSelected() {
    const sel = dlSelectedItems();
    if (!sel.length) return;
    const target = await dlPickAlbum();
    if (!target) return;
    const btn = document.getElementById('dl-bar-import'); btn.disabled = true;
    const count = document.getElementById('dl-bar-count');
    let done = 0, failed = 0, dupes = 0, renamed = 0; const okKeys = []; const errors = [];
    for (const it of sel) {
        count.textContent = _dt('durchlauf.import_running', { done, total: sel.length }, `Einsortieren ${done}/${sel.length}…`);
        const tile = document.querySelector(`.dl-tile[data-key="${CSS.escape(_key(it))}"]`);
        if (tile) tile.classList.add('busy');
        try {
            const r = await fetch(`/api/album/${DL.space}/${encodeURIComponent(target)}/import-file`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ from_space: it.space, from_album: it.album, filename: it.file }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) {
                throw new Error(typeof d.detail === 'string' ? d.detail : `HTTP ${r.status}`);
            }
            if (d.duplicate) dupes++;
            if (d.renamed_from) renamed++;
            okKeys.push(_key(it)); done++;
        } catch (e) {
            failed++; errors.push(`${it.file}: ${e.message}`);
            if (tile) tile.classList.remove('busy');
        }
    }
    btn.disabled = false;
    dlRememberAlbum(target);
    dlRemoveKeys(okKeys);
    let msg = _dt('durchlauf.import_done', { done, album: target }, `${done} Dateien nach „${target}“ einsortiert`);
    if (dupes) msg += _dt('durchlauf.import_dupes', { n: dupes }, `, ${dupes} Dubletten aufgeräumt`);
    if (renamed) msg += _dt('durchlauf.import_renamed', { n: renamed }, `, ${renamed} umbenannt`);
    if (failed) msg += _dt('durchlauf.import_failed', { failed }, `, ${failed} fehlgeschlagen`);
    window.mpdToast(msg, { duration: 5000 });
    if (failed) window.mpdAlert({ icon: '⚠️', title: _dt('durchlauf.import_failed_title', null, 'Nicht alles einsortiert'), body: errors.slice(0, 8).join('\n') });
}

function dlRecentAlbums() {
    try { return JSON.parse(localStorage.getItem('mpd-durchlauf-recent') || '[]'); } catch (_) { return []; }
}
function dlRememberAlbum(name) {
    const list = [name, ...dlRecentAlbums().filter(n => n !== name)].slice(0, 5);
    try { localStorage.setItem('mpd-durchlauf-recent', JSON.stringify(list)); } catch (_) {}
}

function dlPickAlbum() {
    return new Promise(async resolve => {
        let albums = [];
        try {
            const d = await fetch('/api/albums').then(r => r.json());
            albums = (d.albums || d || []).filter(a => a.space === DL.space && !_isYear(a.name));
        } catch (_) {}
        const recent = dlRecentAlbums();
        albums.sort((a, b) => {
            const ra = recent.indexOf(a.name), rb = recent.indexOf(b.name);
            if (ra !== rb) return (ra === -1 ? 99 : ra) - (rb === -1 ? 99 : rb);
            return (b.year || 0) - (a.year || 0) || a.name.localeCompare(b.name);
        });

        const modal = document.createElement('div'); modal.className = 'dl-modal';
        const box = document.createElement('div'); box.className = 'dl-modal-box';
        const head = document.createElement('div'); head.className = 'dl-modal-head';
        head.textContent = _dt('durchlauf.import_title', null, 'Einsortieren nach…');
        const search = document.createElement('input'); search.className = 'dl-modal-search';
        search.placeholder = _dt('durchlauf.import_search', null, 'Album suchen…');
        const list = document.createElement('div'); list.className = 'dl-modal-list';
        const foot = document.createElement('div'); foot.className = 'dl-modal-foot';
        const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'dl-bar-btn dl-bar-btn--ghost';
        cancel.textContent = _dt('durchlauf.import_cancel', null, 'Abbrechen');
        foot.appendChild(cancel);
        box.append(head, search, list, foot); modal.appendChild(box); document.body.appendChild(modal);

        const finish = (val) => { modal.remove(); document.removeEventListener('keydown', esc); resolve(val); };
        const esc = (e) => { if (e.key === 'Escape') finish(null); };
        document.addEventListener('keydown', esc);
        cancel.onclick = () => finish(null);
        modal.onclick = (e) => { if (e.target === modal) finish(null); };

        const render = () => {
            const q = search.value.trim().toLowerCase();
            list.innerHTML = '';
            const hits = albums.filter(a => !q || (a.title || a.name).toLowerCase().includes(q) || a.name.toLowerCase().includes(q));
            if (!hits.length) {
                const e = document.createElement('div'); e.className = 'dl-modal-empty';
                e.textContent = _dt('durchlauf.import_empty', null, 'Kein Album in diesem Bestand.');
                list.appendChild(e); return;
            }
            for (const a of hits) {
                const b = document.createElement('button'); b.type = 'button'; b.className = 'dl-modal-item';
                const y = document.createElement('span'); y.className = 'dl-modal-year'; y.textContent = a.year || '';
                const t = document.createElement('span'); t.textContent = a.title || a.name;
                b.append(y, t);
                if (recent.includes(a.name)) { const r = document.createElement('span'); r.className = 'dl-modal-recent'; r.textContent = _dt('durchlauf.import_recent', null, 'zuletzt'); b.appendChild(r); }
                b.onclick = () => finish(a.name);
                list.appendChild(b);
            }
        };
        search.oninput = render; render(); search.focus();
    });
}

/* ── Loeschen ──────────────────────────────────────────────────────── */

async function dlDeleteSelected() {
    const sel = dlSelectedItems();
    if (!sel.length) return;
    const hint = DL.recycle === true
        ? _dt('durchlauf.delete_hint_recycle', null, 'Die Dateien landen im Papierkorb.')
        : DL.recycle === false
            ? _dt('durchlauf.delete_hint_norecycle', null, 'Kein Papierkorb aktiv — unwiderruflich.')
            : _dt('lightbox.confirm_delete_hint_default', null, 'Diese Aktion kann nicht rückgängig gemacht werden.');
    const ok = await window.mpdConfirm({
        icon: '🗑',
        title: _dt('durchlauf.delete_title', null, 'Dateien löschen?'),
        body : _dt('durchlauf.delete_body', { count: sel.length }, `${sel.length} Dateien werden entfernt.`),
        hint, hintType: DL.recycle === true ? 'info' : 'warn',
        confirmLabel: _dt('durchlauf.delete_ok', null, 'Löschen'),
        destructive: true,
    });
    if (!ok) return;
    const btn = document.getElementById('dl-bar-delete'); btn.disabled = true;
    const count = document.getElementById('dl-bar-count');
    let done = 0, failed = 0; const okKeys = [];
    for (const it of sel) {
        count.textContent = _dt('durchlauf.delete_running', { done, total: sel.length }, `Lösche ${done}/${sel.length}…`);
        try {
            const r = await fetch(`/api/album/${it.space}/${encodeURIComponent(it.album)}/file`, {
                method: 'DELETE', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: it.file, album_context: true }),
            });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            okKeys.push(_key(it)); done++;
        } catch (_) { failed++; }
    }
    btn.disabled = false;
    dlRemoveKeys(okKeys);
    let msg = _dt('durchlauf.delete_done', { done }, `${done} Dateien gelöscht`);
    if (failed) msg += _dt('durchlauf.delete_failed', { failed }, `, ${failed} fehlgeschlagen`);
    window.mpdToast(msg, { duration: 4000 });
}

/* ── Lightbox (nur ansehen) ────────────────────────────────────────── */

function dlOpenLightbox(idx) {
    const photos = DL.items.filter(it => it.type === 'photo');
    const pos = photos.indexOf(DL.items[idx]);
    if (pos < 0) return;
    window.lightbox.allImages  = photos;
    window.lightbox.albumName  = DL.items[idx].album;
    window.lightbox.albumSpace = DL.space;
    allImages = photos;   // album-state.js-Global, das album-render fuer navigate() erwartet
    openLightbox(pos);
}

document.addEventListener('DOMContentLoaded', dlInit);
