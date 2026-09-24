/* MPD – PDX v1.4 mixed picker
 * Modal for inserting photos from other albums (source_album references).
 *
 * Stage 1: album picker with live search and cover grid.
 * Stage 2: photo picker with multi-select, date header, shift-click range.
 * Selection persists across album switches, visible in the footer.
 *
 * Globals: openMixedPicker(), closeMixedPicker(force=false)
 *          handleMixedPickerEsc()  — called from the ESC handler in album-render.js
 */

(function() {
    'use strict';

    // ── State (Closure) ──────────────────────────────────────────────────
    let state = null;

    function _initState() {
        state = {
            stage           : 'albums',           // 'albums' | 'photos'
            allAlbums       : [],                 // all albums of the current space
            filterText      : '',                 // live filter in the album picker
            currentSource   : null,               // {space, name, title, ...}
            currentPhotos   : [],                 // photo elements of the selected source album
            currentPhotoIdx : new Map(),          // file → idx for range select
            lastClickedIdx  : -1,                 // for shift-click
            selection       : new Map(),          // key=`${source_album}/${file}` → {source_album, file, thumb, title}
        };
    }

    // ── Public Entry ─────────────────────────────────────────────────────
    window.openMixedPicker = async function() {
        _initState();
        _buildModal();
        document.getElementById('mpd-mixed-overlay').classList.add('open');
        await _loadAlbums();
        _renderStageAlbums();
        // Focus on search field
        setTimeout(() => document.getElementById('mp-search')?.focus(), 50);
    };

    window.closeMixedPicker = async function(force = false) {
        const sel = state ? state.selection.size : 0;
        if (!force && sel > 0) {
            const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
            const titleKey = sel === 1 ? 'mixed_picker.discard_title_one' : 'mixed_picker.discard_title_many';
            const ok = await window.mpdConfirm({
                icon        : '⚠️',
                title       : _t(titleKey, { count: sel }, `Discard ${sel} selected photos?`),
                body        : _t('mixed_picker.discard_body', null, 'You have not inserted the selection yet. Closing discards it.'),
                confirmLabel: _t('mixed_picker.discard_confirm_label', null, 'Verwerfen'),
                destructive : true,
            });
            if (!ok) return;
        }
        const ov = document.getElementById('mpd-mixed-overlay');
        if (ov) ov.remove();
        state = null;
    };

    // ESC behavior: stage 2 → stage 1, stage 1 → close (with confirm)
    window.handleMixedPickerEsc = function() {
        if (!state) return false;
        if (state.stage === 'photos') {
            state.stage = 'albums';
            state.currentSource = null;
            state.currentPhotos = [];
            state.lastClickedIdx = -1;
            _renderStageAlbums();
            return true;
        }
        window.closeMixedPicker(false);
        return true;
    };

    // ── Modal build ─────────────────────────────────────────────────────
    function _buildModal() {
        // In case an old one is still present
        document.getElementById('mpd-mixed-overlay')?.remove();
        _injectStyles();

        const ov = document.createElement('div');
        ov.id = 'mpd-mixed-overlay';
        ov.className = 'mpd-modal-overlay';
        ov.style.cssText = 'display:flex;align-items:center;justify-content:center';
        ov.addEventListener('click', (e) => {
            if (e.target === ov) window.closeMixedPicker(false);
        });
        // Reliably close on ESC — even when focus is in `mp-search`
        // (stage 1). The global ESC handler in album-render.js remains as a safety net.
        ov.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            e.preventDefault();
            e.stopPropagation();
            window.handleMixedPickerEsc();
        });

        const modal = document.createElement('div');
        modal.className = 'mpd-modal';
        modal.style.cssText = 'width:min(900px,calc(100vw - 2rem));max-height:90vh;display:flex;flex-direction:column;padding:0;overflow:hidden';

        modal.innerHTML = `
            <div id="mp-header" style="padding:1rem 1.2rem 0.6rem;border-bottom:1px solid var(--border,#333);display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">
                <button id="mp-back" type="button" style="background:none;border:none;color:var(--text,#e0e0e0);font-size:1.2rem;cursor:pointer;padding:.2rem .4rem;display:none">&#8592;</button>
                <h3 id="mp-title" style="margin:0;flex:1;font-size:1.05rem">Choose album</h3>
                <button type="button" onclick="closeMixedPicker(false)" style="background:none;border:none;color:var(--muted,#888);font-size:1.4rem;cursor:pointer;padding:.2rem .5rem">&times;</button>
            </div>
            <div id="mp-subbar" style="padding:.6rem 1.2rem;border-bottom:1px solid var(--border,#333)"></div>
            <div id="mp-body" style="flex:1;overflow-y:auto;padding:.8rem 1.2rem;min-height:200px"></div>
            <div id="mp-footer" class="mp-bar" role="region" aria-label="Auswahl">
                <div id="mp-selstrip" class="mp-bar-strip"></div>
                <div class="mp-bar-row">
                    <div class="mp-bar-info">
                        <span id="mp-selcount" class="mp-bar-count"></span>
                        <span id="mp-seldetail" class="mp-bar-detail"></span>
                    </div>
                    <button class="mp-cta" type="button" id="mp-commit-btn" onclick="_mixedCommit()">Insert</button>
                </div>
            </div>
        `;
        ov.appendChild(modal);
        document.body.appendChild(ov);

        document.getElementById('mp-back').addEventListener('click', window.handleMixedPickerEsc);
    }

    function _injectStyles() {
        if (document.getElementById('mp-styles')) return;
        const st = document.createElement('style');
        st.id = 'mp-styles';
        st.textContent = `
            .mp-bar {
                border-top: 1px solid var(--border, #333);
                padding: .55rem 1.2rem .7rem;
                display: none;
                flex-direction: column;
                gap: .5rem;
                background: rgba(0, 0, 0, 0.18);
            }
            .mp-bar.mp-bar-visible { display: flex; }
            .mp-bar-strip {
                display: flex;
                gap: .3rem;
                overflow-x: auto;
                padding: .1rem 0;
                min-height: 54px;
                scrollbar-width: thin;
            }
            .mp-bar-row {
                display: flex;
                align-items: center;
                gap: 1rem;
            }
            .mp-bar-info {
                flex: 1;
                min-width: 0;
                display: flex;
                flex-direction: column;
                gap: .1rem;
            }
            .mp-bar-count {
                font-size: .92rem;
                font-weight: 600;
                color: var(--text, #e0e0e0);
            }
            .mp-bar-detail {
                font-size: .72rem;
                color: var(--muted, #888);
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }
            .mp-bar-detail:empty { display: none; }
            .mp-cta {
                background: var(--blue, #2E75B6);
                color: #fff;
                border: none;
                border-radius: 6px;
                padding: .6rem 1.1rem;
                font: inherit;
                font-weight: 600;
                font-size: .95rem;
                cursor: pointer;
                white-space: nowrap;
                transition: background .15s, transform .05s;
            }
            .mp-cta:hover  { background: #4F81BD; }
            .mp-cta:active { transform: translateY(1px); }
            .mp-cta:focus-visible {
                outline: 2px solid #fff;
                outline-offset: 2px;
            }
        `;
        document.head.appendChild(st);
    }

    // ── Backend: all albums of the space ───────────────────────────────────
    async function _loadAlbums() {
        const body = document.getElementById('mp-body');
        const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
        body.innerHTML = `<div style="color:var(--muted,#888);text-align:center;padding:2rem">${_t('mixed_picker.loading_albums', null, 'Lade Alben …')}</div>`;
        try {
            const res = await fetch(`${API_BASE}/api/albums`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            // Filter: same space, not the current album
            state.allAlbums = (data.albums || [])
                .filter(a => a.space === albumSpace && a.name !== albumName)
                .sort((a, b) => (b.year || 0) - (a.year || 0));
        } catch (e) {
            body.innerHTML = `<div style="color:#e06868;text-align:center;padding:2rem">${_t('mixed_picker.error_generic', { msg: _escape(e.message) }, `Fehler: ${_escape(e.message)}`)}</div>`;
        }
    }

    // ── Stage 1: album picker ────────────────────────────────────────────
    function _renderStageAlbums() {
        state.stage = 'albums';
        document.getElementById('mp-back').style.display = 'none';
        document.getElementById('mp-title').textContent = (window.MPD_I18N ? window.MPD_I18N.t('mixed_picker.title') : 'Choose album');

        const sub = document.getElementById('mp-subbar');
        const _ts = (window.MPD_I18N ? window.MPD_I18N.t('mixed_picker.search_placeholder') : 'Album suchen …');
        sub.innerHTML = `
            <input type="text" id="mp-search" placeholder="${_ts}"
                   value="${_escape(state.filterText)}"
                   style="width:100%;padding:.45rem .6rem;border-radius:6px;border:1px solid var(--border,#444);background:var(--surface,#1a1a1a);color:var(--text,#e0e0e0);font:inherit">
        `;
        document.getElementById('mp-search').addEventListener('input', (e) => {
            state.filterText = e.target.value.toLowerCase();
            _renderAlbumGrid();
        });

        _renderAlbumGrid();
        _updateFooter();
    }

    function _renderAlbumGrid() {
        const body = document.getElementById('mp-body');
        const matches = state.allAlbums.filter(a => {
            if (!state.filterText) return true;
            return (a.title || '').toLowerCase().includes(state.filterText)
                || (a.name || '').toLowerCase().includes(state.filterText);
        });

        if (matches.length === 0) {
            const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
            const msg = state.filterText
                ? _t('mixed_picker.empty_no_match', 'Kein Treffer.')
                : _t('mixed_picker.empty_no_albums', 'Keine anderen Alben in diesem Space.');
            body.innerHTML = `<div style="color:var(--muted,#888);text-align:center;padding:2rem">${msg}</div>`;
            return;
        }

        body.innerHTML = '';
        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:.6rem';

        matches.forEach(album => {
            const card = document.createElement('div');
            card.style.cssText = 'cursor:pointer;border-radius:8px;overflow:hidden;background:var(--surface,#1a1a1a);border:1px solid var(--border,#333);transition:transform .15s,border-color .15s';
            card.onmouseenter = () => { card.style.borderColor = 'var(--blue,#2E75B6)'; };
            card.onmouseleave = () => { card.style.borderColor = 'var(--border,#333)'; };

            const thumbUrl = album.thumbnail
                ? `${API_BASE}/api/thumbnail/${encodeURIComponent(album.space)}/${encodeURIComponent(album.name)}/${encodeURIComponent(album.thumbnail)}?size=sm`
                : '';
            const photoCount = (album.photo_count || 0) + (album.video_count || 0);
            const matchSel = state.selection ? Array.from(state.selection.values()).filter(s => s.source_album === album.name).length : 0;
            const badge = matchSel > 0 ? `<span style="position:absolute;top:6px;right:6px;background:var(--blue,#2E75B6);color:#fff;border-radius:10px;padding:.1rem .4rem;font-size:.75rem;font-weight:600">${matchSel}</span>` : '';

            card.innerHTML = `
                <div style="position:relative;aspect-ratio:4/3;background:#222;display:flex;align-items:center;justify-content:center">
                    ${thumbUrl ? `<img src="${thumbUrl}" loading="lazy" decoding="async" alt="" style="width:100%;height:100%;object-fit:cover">` : `<span style="font-size:2rem;opacity:.4">📁</span>`}
                    ${badge}
                </div>
                <div style="padding:.4rem .6rem">
                    <div style="font-weight:500;font-size:.88rem;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${_escape(album.title || album.name)}</div>
                    <div style="font-size:.72rem;color:var(--muted,#888);margin-top:.15rem">${album.year || ''} · ${(window.MPD_I18N ? window.MPD_I18N.t(photoCount === 1 ? 'mixed_picker.photo_count_one' : 'mixed_picker.photo_count_many', { count: photoCount }) : `${photoCount} Foto${photoCount !== 1 ? 's' : ''}`)}</div>
                </div>
            `;
            card.onclick = () => _enterAlbum(album);
            grid.appendChild(card);
        });

        body.appendChild(grid);
    }

    // ── Stage 2: photo picker ─────────────────────────────────────────────
    async function _enterAlbum(album) {
        state.stage = 'photos';
        state.currentSource = album;
        state.currentPhotos = [];
        state.currentPhotoIdx = new Map();
        state.lastClickedIdx = -1;

        document.getElementById('mp-back').style.display = '';
        const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
        document.getElementById('mp-title').textContent = _t('mixed_picker.photos_from', { album: (album.title || album.name) }, `Pick photos from „${album}"`);

        const sub = document.getElementById('mp-subbar');
        sub.innerHTML = `<div style="font-size:.8rem;color:var(--muted,#888)">${_t('mixed_picker.photos_instructions', null, 'Click to select, Shift+Click for range, double-click = insert immediately')}</div>`;

        const body = document.getElementById('mp-body');
        body.innerHTML = `<div style="color:var(--muted,#888);text-align:center;padding:2rem">${_t('mixed_picker.loading_photos', null, 'Lade Fotos …')}</div>`;

        try {
            const res = await fetch(`${API_BASE}/api/album/${encodeURIComponent(album.space)}/${encodeURIComponent(album.name)}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            // Only local photos of the source album (none that already have source_album themselves — otherwise reference chain).
            // Also no videos in the picker (PDX v1.4: photo only for the start; extensible later).
            state.currentPhotos = (data.elements || [])
                .filter(e => e.type === 'photo' && e.file && !e.source_album && !e.error);
            state.currentPhotoIdx = new Map(state.currentPhotos.map((p, i) => [p.file, i]));
            _renderPhotoGrid();
        } catch (e) {
            body.innerHTML = `<div style="color:#e06868;text-align:center;padding:2rem">${_t('mixed_picker.error_generic', { msg: _escape(e.message) }, `Fehler: ${_escape(e.message)}`)}</div>`;
        }
        _updateFooter();
    }

    function _renderPhotoGrid() {
        const body = document.getElementById('mp-body');
        if (state.currentPhotos.length === 0) {
            const _emptyMsg = (window.MPD_I18N ? window.MPD_I18N.t('mixed_picker.empty_no_photos') : 'This album has no local photos.');
            body.innerHTML = `<div style="color:var(--muted,#888);text-align:center;padding:2rem">${_emptyMsg}</div>`;
            return;
        }

        body.innerHTML = '';
        // Date header grouped by YYYY-MM from the filename.
        const groups = _groupPhotosByMonth(state.currentPhotos);

        groups.forEach(({ label, items }) => {
            if (label) {
                const hd = document.createElement('div');
                hd.style.cssText = 'font-size:.78rem;color:var(--muted,#888);letter-spacing:.05em;text-transform:uppercase;margin:.6rem 0 .3rem;padding-top:.4rem;border-top:1px solid var(--border,#333)';
                hd.textContent = label;
                body.appendChild(hd);
            }
            const grid = document.createElement('div');
            grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));grid-auto-rows:110px;gap:.4rem';
            items.forEach(({ photo, idx }) => grid.appendChild(_buildPhotoTile(photo, idx)));
            body.appendChild(grid);
        });
    }

    function _buildPhotoTile(photo, globalIdx) {
        const key = _selKey(state.currentSource.name, photo.file);
        const isSelected = state.selection.has(key);

        const tile = document.createElement('div');
        tile.style.cssText = `position:relative;aspect-ratio:1;min-height:110px;cursor:pointer;border-radius:6px;overflow:hidden;background:#222;border:2px solid ${isSelected ? 'var(--blue,#2E75B6)' : 'transparent'};transition:border-color .12s,transform .12s`;
        tile.dataset.idx = globalIdx;
        tile.dataset.key = key;

        const thumbUrl = photo.thumbnails?.sm
            ? `${API_BASE}${photo.thumbnails.sm}`
            : `${API_BASE}/api/thumbnail/${encodeURIComponent(state.currentSource.space)}/${encodeURIComponent(state.currentSource.name)}/${encodeURIComponent(photo.file)}?size=sm`;

        tile.innerHTML = `
            <img src="${thumbUrl}" loading="lazy" decoding="async" alt="" style="width:100%;height:100%;object-fit:cover;display:block">
            <div class="mp-tile-check" style="position:absolute;top:4px;left:4px;width:22px;height:22px;border-radius:50%;background:${isSelected ? 'var(--blue,#2E75B6)' : 'rgba(0,0,0,0.5)'};color:#fff;display:flex;align-items:center;justify-content:center;font-size:.85rem;font-weight:700;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4)">${isSelected ? '✓' : ''}</div>
        `;

        tile.addEventListener('click', (e) => {
            if (e.shiftKey && state.lastClickedIdx >= 0) {
                _selectRange(state.lastClickedIdx, globalIdx);
            } else {
                _toggleOne(photo, globalIdx);
                state.lastClickedIdx = globalIdx;
            }
        });
        tile.addEventListener('dblclick', (e) => {
            e.preventDefault();
            // Ensure this photo is in the selection, then commit.
            if (!state.selection.has(key)) _toggleOne(photo, globalIdx);
            _mixedCommit();
        });

        // Zoom preview trigger (hover loupe + long press). When triggered,
        // select/commit for this click series is automatically suppressed.
        if (typeof window.attachZoomTrigger === 'function') {
            const xlUrl = photo.thumbnails?.xl
                ? `${API_BASE}${photo.thumbnails.xl}`
                : `${API_BASE}/api/thumbnail/${encodeURIComponent(state.currentSource.space)}/${encodeURIComponent(state.currentSource.name)}/${encodeURIComponent(photo.file)}?size=xl`;
            const fullUrl = `${API_BASE}/api/thumbnail/${encodeURIComponent(state.currentSource.space)}/${encodeURIComponent(state.currentSource.name)}/${encodeURIComponent(photo.file)}?size=full`;
            window.attachZoomTrigger(tile, xlUrl, {
                alt      : photo.file,
                blurhash : photo.blurhash,
                srcHigh  : fullUrl,
            });
        }

        return tile;
    }

    function _toggleOne(photo, globalIdx) {
        const key = _selKey(state.currentSource.name, photo.file);
        if (state.selection.has(key)) {
            state.selection.delete(key);
        } else {
            state.selection.set(key, _selectionEntry(photo));
        }
        _refreshTile(key);
        _updateFooter();
    }

    function _selectionEntry(photo) {
        const thumbUrl = photo.thumbnails?.sm
            ? `${API_BASE}${photo.thumbnails.sm}`
            : `${API_BASE}/api/thumbnail/${encodeURIComponent(state.currentSource.space)}/${encodeURIComponent(state.currentSource.name)}/${encodeURIComponent(photo.file)}?size=sm`;
        return {
            space        : state.currentSource.space,
            source_album : state.currentSource.name,
            file         : photo.file,
            thumb        : thumbUrl,
            title        : state.currentSource.title || state.currentSource.name,
            resolution   : photo.resolution,   // optional, may be undefined
            blurhash     : photo.blurhash,     // optional
        };
    }

    function _selectRange(fromIdx, toIdx) {
        const lo = Math.min(fromIdx, toIdx);
        const hi = Math.max(fromIdx, toIdx);
        // Range either fully selected or fully deselected — depends on target state.
        const allSelected = state.currentPhotos.slice(lo, hi + 1).every(p =>
            state.selection.has(_selKey(state.currentSource.name, p.file))
        );
        for (let i = lo; i <= hi; i++) {
            const photo = state.currentPhotos[i];
            const key = _selKey(state.currentSource.name, photo.file);
            if (allSelected) {
                state.selection.delete(key);
            } else if (!state.selection.has(key)) {
                state.selection.set(key, _selectionEntry(photo));
            }
            _refreshTile(key);
        }
        _updateFooter();
    }

    function _refreshTile(key) {
        const tile = document.querySelector(`#mp-body [data-key="${CSS.escape(key)}"]`);
        if (!tile) return;
        const sel = state.selection.has(key);
        tile.style.borderColor = sel ? 'var(--blue,#2E75B6)' : 'transparent';
        const check = tile.querySelector('.mp-tile-check');
        if (check) {
            check.style.background = sel ? 'var(--blue,#2E75B6)' : 'rgba(0,0,0,0.5)';
            check.textContent = sel ? '✓' : '';
        }
    }

    // ── Footer / selection strip ───────────────────────────────────────────
    function _updateFooter() {
        const footer = document.getElementById('mp-footer');
        const strip  = document.getElementById('mp-selstrip');
        const count  = document.getElementById('mp-selcount');
        const detail = document.getElementById('mp-seldetail');
        const cta    = document.getElementById('mp-commit-btn');
        const n = state.selection.size;

        if (n === 0) {
            footer.classList.remove('mp-bar-visible');
            return;
        }
        footer.classList.add('mp-bar-visible');

        // Count: primary line. Detail: per-album breakdown, only when
        // more than one source album is involved — otherwise redundant with the title.
        const byAlbum = new Map();
        state.selection.forEach(s => {
            byAlbum.set(s.title, (byAlbum.get(s.title) || 0) + 1);
        });
        count.textContent = `${n} photo${n !== 1 ? 's' : ''} selected`;
        if (byAlbum.size > 1) {
            const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
            detail.textContent = Array.from(byAlbum.entries())
                .map(([t, c]) => _t('mixed_picker.sel_breakdown_one', { count: c, album: t }, `${c} from „${t}"`)).join(' · ');
        } else {
            const only = Array.from(byAlbum.keys())[0] || '';
            const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
            detail.textContent = only ? _t('mixed_picker.sel_breakdown_solo', { album: only }, `from „${only}"`) : '';
        }

        const _tCta = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
        const ctaKey = n === 1 ? 'mixed_picker.cta_insert_one' : 'mixed_picker.cta_insert_many';
        cta.textContent = _tCta(ctaKey, { count: n }, `Insert ${n} photo${n !== 1 ? 's' : ''}`);

        // Render strip
        strip.innerHTML = '';
        state.selection.forEach((s, key) => {
            const mini = document.createElement('div');
            mini.style.cssText = 'position:relative;flex:0 0 auto;width:48px;height:48px;border-radius:4px;overflow:hidden;border:1px solid var(--border,#333);background:#222';
            mini.innerHTML = `
                <img src="${s.thumb}" loading="lazy" alt="" style="width:100%;height:100%;object-fit:cover;display:block">
                <button type="button" title="Aus Auswahl entfernen"
                        style="position:absolute;top:1px;right:1px;width:16px;height:16px;border-radius:50%;border:none;background:rgba(0,0,0,.7);color:#fff;font-size:.7rem;line-height:1;cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center">&times;</button>
            `;
            mini.querySelector('button').addEventListener('click', (e) => {
                e.stopPropagation();
                state.selection.delete(key);
                _refreshTile(key);
                _updateFooter();
                if (state.stage === 'albums') _renderAlbumGrid(); // refresh badge
            });
            strip.appendChild(mini);
        });
    }

    // ── Commit: insert selection into the album ──────────────────────────────
    window._mixedCommit = function() {
        if (!state || state.selection.size === 0) return;
        const items = Array.from(state.selection.values());

        // Insert position like in the other insert functions: after the element
        // in the middle of the screen. For an empty album: at the end (= position 0).
        const viewportMidY = (window._fabScrollY ?? window.scrollY) + window.innerHeight / 2;
        const els = Array.from(document.getElementById('content').querySelectorAll('.draggable-element'));
        let insertAfterIndex = -1;
        for (const el of els) {
            const midY = el.offsetTop + el.offsetHeight / 2;
            if (midY <= viewportMidY) {
                const idx = (typeof getLastArrayIndexForDomElem === 'function')
                    ? getLastArrayIndexForDomElem(el)
                    : -1;
                if (idx !== -1) insertAfterIndex = idx;
            } else break;
        }

        pushUndoState();
        // generateId() takes max(existing)+1 — so call it per element
        // (not all up-front in a Map, otherwise IDs would collide).
        // We set the thumbnails field client-side: backend would repair them
        // on the next get_album, but renderAlbum runs immediately and would
        // otherwise access undefined.thumbnails.m.
        let pos = insertAfterIndex + 1;
        items.forEach(it => {
            const encS = encodeURIComponent(it.space);
            const encA = encodeURIComponent(it.source_album);
            const encF = encodeURIComponent(it.file);
            const elem = {
                id           : generateId(),
                type         : 'photo',
                file         : it.file,
                source_album : it.source_album,
                thumbnails   : {
                    sm: `/api/thumbnail/${encS}/${encA}/${encF}?size=sm`,
                    m : `/api/thumbnail/${encS}/${encA}/${encF}?size=m`,
                    xl: `/api/thumbnail/${encS}/${encA}/${encF}?size=xl`,
                },
            };
            if (it.resolution) elem.resolution = it.resolution;
            if (it.blurhash)   elem.blurhash   = it.blurhash;
            albumData.elements.splice(pos, 0, elem);
            pos++;
        });
        hasUnsavedChanges = true;

        // Force-close the modal (selection is now inserted, no confirm needed)
        window.closeMixedPicker(true);

        const _sy = window.scrollY;
        if (isEditMode) enableEditMode(); else renderAlbum();
        requestAnimationFrame(() => window.scrollTo(0, _sy));
        scheduleAutoSave();

        if (typeof window.mpdToast === 'function') {
            const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
            const tk = items.length === 1 ? 'mixed_picker.toast_inserted_one' : 'mixed_picker.toast_inserted_many';
            window.mpdToast(_t(tk, { count: items.length }, `${items.length} photo${items.length !== 1 ? 's' : ''} inserted as reference`), { duration: 3000 });
        }
    };

    // ── Helpers ──────────────────────────────────────────────────────────
    function _selKey(albumName, file) { return albumName + '/' + file; }

    function _escape(s) {
        const div = document.createElement('div');
        div.textContent = s == null ? '' : String(s);
        return div.innerHTML;
    }

    // Extract date from filename — reused heuristic from album-render.js,
    // minimal here: YYYY-MM from the first digits.
    const _MONTH_RE = /^(?:(?:IMG|VID|PXL|DSC|MVIMG|Screenshot|WhatsApp)[-_ ]?)?(\d{4})[-_]?(\d{2})/;
    function _yearMonth(filename) {
        const m = _MONTH_RE.exec(filename || '');
        if (!m) return null;
        const y = +m[1], mo = +m[2];
        if (y < 1990 || y > 2100 || mo < 1 || mo > 12) return null;
        return `${m[1]}-${m[2]}`;
    }

    function _groupPhotosByMonth(photos) {
        const out = [];
        let currentLabel = null;
        let bucket = null;
        photos.forEach((photo, idx) => {
            const ym = _yearMonth(photo.file);
            const label = ym || '';
            if (label !== currentLabel) {
                bucket = { label, items: [] };
                out.push(bucket);
                currentLabel = label;
            }
            bucket.items.push({ photo, idx });
        });
        // If only a single bucket is created, leave the label empty (no value)
        if (out.length === 1) out[0].label = '';
        return out;
    }
})();
