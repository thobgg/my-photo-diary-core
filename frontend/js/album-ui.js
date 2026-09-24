/* MPD – UI: FAB, modals, separator dialog
 * toggleFabMenu, openFabMenu, closeFabMenu, fabAction,
 * openSeparatorDialog, insertSeparatorFromDialog,
 * openAddPhotoModal, closeAddPhotoModal,
 * openThumbnailModal, closeThumbnailModal,
 * openAddDocumentModal, closeAddDocumentModal,
 * openAddAudioModal, closeAddAudioModal,
 * openAddTourModal,
 * openDocumentModal, closeDocumentModal,
 * openVideoModal, closeVideoModal
 */

// ============================================
// Swipe-down-to-close (Touch helper)
// ============================================

function _addSwipeDownToClose(el, closeFn) {
    if (el._swipeDownBound) return;
    el._swipeDownBound = true;
    let startY = 0, startX = 0;
    el.addEventListener('pointerdown', (e) => {
        startY = e.clientY;
        startX = e.clientX;
    }, { passive: true });
    el.addEventListener('pointerup', (e) => {
        const dy = e.clientY - startY;
        const dx = Math.abs(e.clientX - startX);
        if (dy > 80 && dy > dx) closeFn();
    }, { passive: true });
}

// ============================================
// Universal FAB / speed-dial
// ============================================

function toggleFabMenu() { _fabMenuOpen ? closeFabMenu() : openFabMenu(); }

function openFabMenu() {
    const menu     = document.getElementById('fab-menu');
    const icon     = document.getElementById('fab-main-icon');
    const backdrop = document.getElementById('fab-backdrop');
    if (menu)     menu.style.display = 'flex';
    if (icon)     icon.textContent   = '×';
    if (backdrop) backdrop.classList.remove('hidden');
    _fabMenuOpen = true;
}

function closeFabMenu() {
    const menu     = document.getElementById('fab-menu');
    const icon     = document.getElementById('fab-main-icon');
    const backdrop = document.getElementById('fab-backdrop');
    if (menu) {
        menu.style.display = 'none';
        // Reset "more" state on close so phone shows the two primary items
        // again on the next open.
        menu.classList.remove('show-advanced');
    }
    if (icon)     icon.textContent   = '+';
    if (backdrop) backdrop.classList.add('hidden');
    _fabMenuOpen = false;
}

function toggleFabMore() {
    const menu = document.getElementById('fab-menu');
    if (!menu) return;
    menu.classList.toggle('show-advanced');
    const label = document.getElementById('fab-more-label');
    if (label) {
        const more = menu.classList.contains('show-advanced');
        label.textContent = (window.MPD_I18N
            ? window.MPD_I18N.t(more ? 'album.fab_less' : 'album.fab_more')
            : (more ? 'Weniger' : 'Mehr'));
    }
}

function fabAction(type) {
    window._fabScrollY = window.scrollY;  // save before closeFabMenu/focus
    closeFabMenu();
    if      (type === 'photo')     openAddPhotoModal();
    else if (type === 'text')      insertTextAtScrollPosition();
    else if (type === 'separator') openSeparatorDialog();
    else if (type === 'document')  openAddDocumentModal();
    else if (type === 'map')       openAddMapDialog();
    else if (type === 'audio')     openAddAudioModal();
    else if (type === 'tour')      openAddTourModal();
    else if (type === 'link')      openAddLinkDialog();
    else if (type === 'mixed')     openMixedPicker();
}

// ============================================
// Separator dialog
// ============================================

function openSeparatorDialog() {
    const existing = document.getElementById('separator-dialog');
    if (existing) existing.remove();
    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    const dialog = document.createElement('div');
    dialog.id = 'separator-dialog';
    dialog.className = 'separator-dialog-overlay';
    dialog.innerHTML = `
        <div class="separator-dialog-box">
            <h4 class="separator-dialog-title">${_t('add_modal.separator_title', 'Trenner einfügen')}</h4>
            <input id="separator-label-input" class="separator-dialog-input"
                   type="text" maxlength="80"
                   placeholder="${_t('add_modal.separator_placeholder', 'Label (optional) – z. B. „2006" oder „Ankunft"')}">
            <div class="sep-style-picker" id="sep-style-picker">
                <button class="sep-style-chip active" data-style="line"   title="Linie">—</button>
                <button class="sep-style-chip"        data-style="bold"   title="Fett">━</button>
                <button class="sep-style-chip"        data-style="dashed" title="Gestrichelt">╌ ╌</button>
                <button class="sep-style-chip"        data-style="dots"   title="Punkte">· · ·</button>
                <button class="sep-style-chip"        data-style="space"  title="Abstand">↕</button>
            </div>
            <div class="separator-dialog-actions">
                <button class="separator-dialog-btn separator-dialog-cancel"
                        onclick="document.getElementById('separator-dialog').remove()">${_t('add_modal.separator_cancel', 'Abbrechen')}</button>
                <button class="separator-dialog-btn separator-dialog-ok"
                        onclick="insertSeparatorFromDialog()">${_t('add_modal.separator_ok', 'Einfügen')}</button>
            </div>
        </div>`;
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.remove(); });
    document.body.appendChild(dialog);

    // Chip-Auswahl
    dialog.querySelectorAll('.sep-style-chip').forEach(chip => {
        chip.addEventListener('click', (e) => {
            e.stopPropagation();
            dialog.querySelectorAll('.sep-style-chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
        });
    });

    // BEFORE focus() — afterwards tablet scrolls up
    const input = document.getElementById('separator-label-input');
    input.focus();
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter')  insertSeparatorFromDialog();
        if (e.key === 'Escape') dialog.remove();
    });
}

function insertSeparatorFromDialog() {
    const input      = document.getElementById('separator-label-input');
    const label      = input ? input.value.trim() : '';
    const activeChip = document.querySelector('#sep-style-picker .sep-style-chip.active');
    const style      = activeChip ? activeChip.dataset.style : 'line';
    document.getElementById('separator-dialog')?.remove();
    insertSeparatorAtScrollPosition(label, style);
}

// ============================================
// Web-Link dialog (PDX v1.4.1)
// ============================================

// `elem` gesetzt = bestehenden Link bearbeiten, sonst neu einfuegen.
function openAddLinkDialog(elem) {
    const existing = document.getElementById('link-dialog');
    if (existing) existing.remove();
    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    const esc = (v) => String(v || '').replace(/"/g, '&quot;');
    const isEdit = !!(elem && elem.id);

    const dialog = document.createElement('div');
    dialog.id = 'link-dialog';
    dialog.className = 'separator-dialog-overlay';
    dialog.innerHTML = `
        <div class="separator-dialog-box link-dialog-box">
            <h4 class="separator-dialog-title">${isEdit
                ? _t('add_modal.link_title_edit', 'Link bearbeiten')
                : _t('add_modal.link_title', 'Web-Link einfügen')}</h4>
            <input id="link-url-input" class="separator-dialog-input"
                   type="url" inputmode="url" autocomplete="off"
                   autocapitalize="off" spellcheck="false" maxlength="2048"
                   value="${esc(elem && elem.url)}"
                   placeholder="${_t('add_modal.link_url_placeholder', 'https://…')}">
            <input id="link-title-input" class="separator-dialog-input"
                   type="text" maxlength="120"
                   value="${esc(elem && elem.title)}"
                   placeholder="${_t('add_modal.link_title_placeholder', 'Titel (optional)')}">
            <input id="link-note-input" class="separator-dialog-input"
                   type="text" maxlength="200"
                   value="${esc(elem && elem.note)}"
                   placeholder="${_t('add_modal.link_note_placeholder', 'Notiz (optional)')}">
            <div id="link-dialog-err" class="link-dialog-err" hidden></div>
            <div class="separator-dialog-actions">
                <button class="separator-dialog-btn separator-dialog-cancel"
                        onclick="document.getElementById('link-dialog').remove()">${_t('add_modal.link_cancel', 'Abbrechen')}</button>
                <button class="separator-dialog-btn separator-dialog-ok"
                        onclick="submitLinkDialog()">${isEdit
                            ? _t('add_modal.link_save', 'Speichern')
                            : _t('add_modal.link_ok', 'Einfügen')}</button>
            </div>
        </div>`;
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.remove(); });
    dialog.dataset.editId = isEdit ? elem.id : '';
    document.body.appendChild(dialog);

    const urlInput = document.getElementById('link-url-input');
    urlInput.focus();
    dialog.querySelectorAll('.separator-dialog-input').forEach(inp => {
        inp.addEventListener('keydown', (e) => {
            if (e.key === 'Enter')  { e.preventDefault(); submitLinkDialog(); }
            if (e.key === 'Escape') dialog.remove();
        });
    });
}

// Prueft die URL clientseitig mit derselben Regel wie das Backend
// (_LINK_URL_RE in routers/albums.py): nur http/https.
function _normalizeLinkUrl(raw) {
    let url = (raw || '').trim();
    if (!url) return null;
    // Bequemlichkeit: "example.com/x" ohne Schema wird zu https://
    if (!/^[a-z][a-z0-9+.-]*:/i.test(url)) url = 'https://' + url;
    if (!/^https?:\/\/[^\s]+$/i.test(url)) return null;
    if (url.length > 2048) return null;
    return url;
}

function submitLinkDialog() {
    const _t  = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    const dlg = document.getElementById('link-dialog');
    if (!dlg) return;
    const url   = _normalizeLinkUrl(document.getElementById('link-url-input')?.value);
    const title = (document.getElementById('link-title-input')?.value || '').trim();
    const note  = (document.getElementById('link-note-input')?.value  || '').trim();

    if (!url) {
        // Kein natives alert() — im WebView-Wrapper still wirkungslos.
        const err = document.getElementById('link-dialog-err');
        if (err) {
            err.textContent = _t('add_modal.link_err_url', 'Bitte eine vollständige Adresse angeben (http:// oder https://).');
            err.hidden = false;
        }
        document.getElementById('link-url-input')?.focus();
        return;
    }

    const editId = dlg.dataset.editId;
    dlg.remove();

    if (editId) {
        const i = albumData.elements.findIndex(e => e.id === editId);
        if (i === -1) return;
        pushUndoState();
        const el = albumData.elements[i];
        el.url = url;
        if (title) el.title = title; else delete el.title;
        if (note)  el.note  = note;  else delete el.note;
        hasUnsavedChanges = true;
        const _sy = window.scrollY;
        renderAlbum(isEditMode);
        requestAnimationFrame(() => { window.scrollTo(0, _sy); scheduleAutoSave(); });
    } else {
        insertLinkAtScrollPosition(url, title, note);
    }
}

// ============================================
// Add photo
// ============================================

async function openAddPhotoModal() {
    let modal = document.getElementById('add-photo-modal');
    if (modal) { modal.classList.remove('hidden'); return; }

    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    modal = document.createElement('div');
    modal.id = 'add-photo-modal';
    modal.className = 'add-photo-modal';
    modal.innerHTML = `
        <div class="add-photo-modal-inner">
            <div class="add-photo-modal-header">
                <div class="add-photo-titles">
                    <h2>${_t('add_modal.photo_title', 'Add photo')}</h2>
                    <span class="add-photo-hint">${_t('add_modal.photo_hint', 'Fotos im Ordner die noch nicht im Album sind')}</span>
                    <span class="add-photo-pickhint">${_t('add_modal.pick_hint', 'Tap to add')}</span>
                </div>
                <button class="add-photo-close" title="${_t('add_modal.close_title', 'Close')}" onclick="closeAddPhotoModal()">×</button>
            </div>
            <div class="add-photo-modal-body" id="add-photo-grid">
                <div class="add-photo-loading">${_t('add_modal.photo_loading', '⏳ Lade Fotos…')}</div>
            </div>
            <div class="add-photo-footer" id="add-photo-footer" hidden>
                <button type="button" class="add-photo-foot-btn" onclick="_addPhotoSelectAll(true)">${_t('add_modal.select_all', 'Alle')}</button>
                <button type="button" class="add-photo-foot-btn" onclick="_addPhotoSelectAll(false)">${_t('add_modal.select_none', 'Keine')}</button>
                <span class="add-photo-foot-count" id="add-photo-count"></span>
                <button type="button" class="add-photo-cta" id="add-photo-cta" onclick="_addPhotoCommit()" disabled>${_t('add_modal.insert_btn', 'Einfügen')}</button>
            </div>
        </div>
    `;
    modal.addEventListener('click', (e) => { if (e.target === modal) closeAddPhotoModal(); });
    document.body.appendChild(modal);

    try {
        const res = await fetch(`${API_BASE}/api/album/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/unassigned-photos`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const grid = document.getElementById('add-photo-grid');

        // Was MPD im Ordner NICHT anzeigen kann — sonst liest sich
        // „alle Fotos sind bereits im Album" wie ein Verlust (07.09.2026).
        // Im Leer-Fall INNERHALB der Meldung, sonst bricht das
        // :only-child-Zentrieren in edit-photo.css.
        const skippedText = (data.skipped_count > 0)
            ? _t('add_modal.photo_skipped',
                 '{n} weitere Datei(en) in diesem Ordner kann MPD nicht anzeigen: {list}. Sie bleiben unberührt liegen.')
                .replace('{n}', data.skipped_count)
                .replace('{list}', data.skipped.map(s => `${s.count}\u00d7 ${s.ext}`).join(', '))
            : '';

        if (data.count === 0) {
            grid.innerHTML = `<div class="add-photo-empty">${_t('add_modal.photo_empty', '✅ Alle Fotos sind bereits im Album.')}` +
                (skippedText ? `<span class="add-photo-skipped-inline">${skippedText}</span>` : '') +
                `</div>`;
            return;
        }

        grid.innerHTML = skippedText
            ? `<div class="add-photo-skipped">${skippedText}</div>`
            : '';
        // Mehrfachauswahl (08.09.2026): 'kann es sein, dass man nicht
        // mehrere gleichzeitig auswaehlen kann?' — konnte man nie). Muster
        // wie im Mixed-Picker: Tipp waehlt, Shift-Klick nimmt den Bereich
        // seit dem letzten Tipp, unten 'n einfuegen'. Doppelklick fuegt
        // sofort ein — der alte Ein-Tipp-Weg bleibt so erreichbar.
        _addPhotoState = { files: data.unassigned, selected: new Set(), last: -1 };
        document.getElementById('add-photo-footer').hidden = false;
        data.unassigned.forEach((file, idx) => {
            const thumb = document.createElement('div');
            thumb.className = 'add-photo-thumb';
            thumb.title = file.file;
            thumb.dataset.idx = String(idx);

            const img = document.createElement('img');
            img.src = (file.thumbnails && file.thumbnails.sm)
                ? `${API_BASE}${file.thumbnails.sm}`
                : `${API_BASE}/api/thumbnail/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/${encodeURIComponent(file.file)}?size=sm`;
            img.loading = 'lazy';
            img.draggable = false;

            const label = document.createElement('span');
            label.className = 'add-photo-label';
            label.textContent = file.file;

            thumb.appendChild(img);
            thumb.appendChild(label);
            thumb.onclick = (e) => _addPhotoToggle(idx, e.shiftKey);
            thumb.ondblclick = (e) => { e.preventDefault(); addPhotosToAlbum([file]); };
            grid.appendChild(thumb);
        });
        _addPhotoUpdate();

    } catch (err) {
        document.getElementById('add-photo-grid').innerHTML =
            `<div class="add-photo-error">${_t('add_modal.err_prefix', '❌ Fehler: {msg}').replace('{msg}', err.message)}</div>`;
    }
}

function closeAddPhotoModal() {
    const modal = document.getElementById('add-photo-modal');
    if (modal) modal.classList.add('hidden');
}

// ── Auswahl im Foto-Dialog ──
let _addPhotoState = null;

function _addPhotoToggle(idx, withShift) {
    const st = _addPhotoState;
    if (!st) return;
    if (withShift && st.last >= 0) {
        // Bereich vom letzten Tipp bis hier — Zielzustand ist der des Ankers
        const on = st.selected.has(st.last);
        const [a, b] = st.last < idx ? [st.last, idx] : [idx, st.last];
        for (let i = a; i <= b; i++) { if (on) st.selected.add(i); else st.selected.delete(i); }
    } else {
        if (st.selected.has(idx)) st.selected.delete(idx); else st.selected.add(idx);
        st.last = idx;
    }
    _addPhotoUpdate();
}

function _addPhotoSelectAll(on) {
    const st = _addPhotoState;
    if (!st) return;
    st.selected = on ? new Set(st.files.map((_, i) => i)) : new Set();
    _addPhotoUpdate();
}

function _addPhotoUpdate() {
    const st = _addPhotoState;
    if (!st) return;
    const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
    document.querySelectorAll('#add-photo-grid .add-photo-thumb').forEach(el => {
        el.classList.toggle('thumbnail-selected', st.selected.has(Number(el.dataset.idx)));
    });
    const n = st.selected.size;
    const count = document.getElementById('add-photo-count');
    const cta = document.getElementById('add-photo-cta');
    if (count) count.textContent = n === 1
        ? _t('add_modal.selected_one', null, '1 ausgewählt')
        : _t('add_modal.selected', { n }, `${n} ausgewählt`);
    if (cta) {
        cta.disabled = n === 0;
        cta.textContent = n > 0
            ? _t('add_modal.insert_n', { n }, `${n} einfügen`)
            : _t('add_modal.insert_btn', null, 'Einfügen');
    }
}

function _addPhotoCommit() {
    const st = _addPhotoState;
    if (!st || !st.selected.size) return;
    const files = [...st.selected].sort((a, b) => a - b).map(i => st.files[i]);
    addPhotosToAlbum(files);
}

// ============================================
// Switch thumbnail
// ============================================

function openThumbnailModal() {
    const existing = document.getElementById('thumbnail-modal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id        = 'thumbnail-modal';
    modal.className = 'add-photo-modal';
    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    modal.innerHTML = `
        <div class="add-photo-modal-inner">
            <div class="add-photo-modal-header">
                <h2>${_t('cover_picker.modal_title', 'Pick album cover')}</h2>
                <span class="add-photo-hint">${_t('cover_picker.modal_current', 'Current:')} <strong id="thumb-current">${albumData.meta.thumbnail}</strong></span>
                <button class="add-photo-close" onclick="closeThumbnailModal()">×</button>
            </div>
            <div class="add-photo-modal-body" id="thumbnail-grid"></div>
            <div class="thumbnail-modal-footer">
                <button class="thumbnail-ok-btn" onclick="closeThumbnailModal()">${_t('cover_picker.modal_apply', '✔ Apply')}</button>
            </div>
        </div>
    `;
    modal.addEventListener('click', (e) => { if (e.target === modal) closeThumbnailModal(); });
    document.body.appendChild(modal);

    // Photos directly from albumData — no API call, immediately available
    const photos = albumData.elements.filter(e => e.type === 'photo' && e.thumbnails);
    const grid   = document.getElementById('thumbnail-grid');

    if (photos.length === 0) {
        grid.innerHTML = '<div class="add-photo-empty">Keine Fotos im Album.</div>';
        return;
    }

    photos.forEach(elem => {
        const item    = document.createElement('div');
        item.className = 'add-photo-thumb';
        if (elem.file === albumData.meta.thumbnail) {
            item.classList.add('thumbnail-selected');
        }
        const img   = document.createElement('img');
        img.src     = `${API_BASE}${elem.thumbnails.sm}`;
        img.alt     = elem.file;
        img.title   = elem.file;
        img.loading = 'lazy';
        item.appendChild(img);

        // Zoom preview (hover loupe + long press) — same as Mixed-Picker.
        // The trigger suppresses the next click, so select-on-tap doesn't fire.
        if (typeof window.attachZoomTrigger === 'function') {
            const xlUrl = elem.thumbnails.xl
                ? `${API_BASE}${elem.thumbnails.xl}`
                : `${API_BASE}${elem.thumbnails.sm}`;
            window.attachZoomTrigger(item, xlUrl, {
                alt     : elem.file,
                blurhash: elem.blurhash,
            });
        }

        item.addEventListener('click', async () => {
            let thumbnailName = elem.file;

            // PDX v1.4: for a referenced photo, do a spec-compliant silent copy as cover.<ext>.
            // meta.thumbnail remains a local filename.
            if (elem.source_album) {
                try {
                    const res = await fetch(
                        `${API_BASE}/api/album/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/cover-from-source`,
                        {
                            method : 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body   : JSON.stringify({ source_album: elem.source_album, file: elem.file }),
                        }
                    );
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.detail || res.statusText);
                    thumbnailName = data.thumbnail;
                    if (typeof window.mpdToast === 'function') {
                        window.mpdToast(`Cover als „${thumbnailName}" kopiert`, { duration: 2500 });
                    }
                } catch (err) {
                    window.mpdToast('Fehler beim Cover-Kopieren: ' + err.message, { duration: 4000 });
                    return;
                }
            }

            pushUndoState();
            albumData.meta.thumbnail = thumbnailName;
            hasUnsavedChanges = true;
            updateSaveIndicator('unsaved');
            const cur = document.getElementById('thumb-current');
            if (cur) cur.textContent = thumbnailName;
            grid.querySelectorAll('.add-photo-thumb').forEach(el => el.classList.remove('thumbnail-selected'));
            item.classList.add('thumbnail-selected');
        });
        grid.appendChild(item);
    });
}

function closeThumbnailModal() {
    const modal = document.getElementById('thumbnail-modal');
    if (modal) modal.remove();
}

// ============================================
// Add document
// ============================================

async function openAddDocumentModal() {
    let modal = document.getElementById('add-document-modal');
    if (modal) { modal.classList.remove('hidden'); return; }

    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    modal = document.createElement('div');
    modal.id = 'add-document-modal';
    modal.className = 'add-photo-modal';
    modal.innerHTML = `
        <div class="add-photo-modal-inner">
            <div class="add-photo-modal-header">
                <div class="add-photo-titles">
                    <h2>${_t('add_modal.document_title', 'Add document')}</h2>
                    <span class="add-photo-hint">${_t('add_modal.document_hint', 'PDF-Dateien im Ordner die noch nicht im Album sind')}</span>
                    <span class="add-photo-pickhint">${_t('add_modal.pick_hint', 'Tap to add')}</span>
                </div>
                <button class="add-photo-close" title="${_t('add_modal.close_title', 'Close')}" onclick="closeAddDocumentModal()">×</button>
            </div>
            <div class="add-photo-modal-body" id="add-document-grid">
                <div class="add-photo-loading">${_t('add_modal.document_loading', '⏳ Lade Dokumente…')}</div>
            </div>
        </div>
    `;
    modal.addEventListener('click', (e) => { if (e.target === modal) closeAddDocumentModal(); });
    document.body.appendChild(modal);

    try {
        const res = await fetch(`${API_BASE}/api/album/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/unassigned-documents`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const grid = document.getElementById('add-document-grid');

        if (data.count === 0) {
            grid.innerHTML = `<div class="add-photo-empty">${_t('add_modal.document_empty_title', '✅ Keine unzugewiesenen PDFs gefunden.')}<br><small>${_t('add_modal.document_empty_hint', 'PDF-Datei in den Album-Ordner kopieren.')}</small></div>`;
            return;
        }

        grid.innerHTML = '';
        data.unassigned.forEach(file => {
            const thumb = document.createElement('div');
            thumb.className = 'add-photo-thumb';
            thumb.title = file.file;

            if (file.is_pdf) {
                const icon = document.createElement('div');
                icon.style.cssText = 'font-size:2.5rem;text-align:center;padding:12px 0;';
                icon.textContent = '📄';
                thumb.appendChild(icon);
            } else {
                const img = document.createElement('img');
                img.src = `${API_BASE}/api/thumbnail/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/${encodeURIComponent(file.file)}?size=sm`;
                img.loading = 'lazy';
                thumb.appendChild(img);
            }

            const label = document.createElement('span');
            label.className = 'add-photo-label';
            label.textContent = file.file;
            thumb.appendChild(label);

            thumb.onclick = () => addDocumentToAlbum(file);
            grid.appendChild(thumb);
        });

    } catch (err) {
        document.getElementById('add-document-grid').innerHTML =
            `<div class="add-photo-error">${_t('add_modal.err_prefix', '❌ Fehler: {msg}').replace('{msg}', err.message)}</div>`;
    }
}

function closeAddDocumentModal() {
    const modal = document.getElementById('add-document-modal');
    if (modal) modal.classList.add('hidden');
}

// ============================================
// Add audio
// ============================================

async function openAddAudioModal() {
    let modal = document.getElementById('add-audio-modal');
    if (modal) { modal.classList.remove('hidden'); return; }

    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    modal = document.createElement('div');
    modal.id = 'add-audio-modal';
    modal.className = 'add-photo-modal';
    modal.innerHTML = `
        <div class="add-photo-modal-inner">
            <div class="add-photo-modal-header">
                <div class="add-photo-titles">
                    <h2>${_t('add_modal.audio_title', 'Add audio')}</h2>
                    <span class="add-photo-hint">${_t('add_modal.audio_hint', 'Audio-Dateien im Ordner (MP3, M4A, OGG, WAV)')}</span>
                    <span class="add-photo-pickhint">${_t('add_modal.pick_hint', 'Tap to add')}</span>
                </div>
                <button class="add-photo-close" title="${_t('add_modal.close_title', 'Close')}" onclick="closeAddAudioModal()">×</button>
            </div>
            <div class="add-photo-modal-body" id="add-audio-grid">
                <div class="add-photo-loading">${_t('add_modal.audio_loading', '⏳ Lade Audio-Dateien…')}</div>
            </div>
        </div>
    `;
    modal.addEventListener('click', (e) => { if (e.target === modal) closeAddAudioModal(); });
    document.body.appendChild(modal);

    try {
        const res = await fetch(`${API_BASE}/api/album/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/unassigned-audio`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const grid = document.getElementById('add-audio-grid');

        if (data.count === 0) {
            grid.innerHTML = `<div class="add-photo-empty">${_t('add_modal.audio_empty_title', '✅ Keine unzugewiesenen Audio-Dateien gefunden.')}<br><small>${_t('add_modal.audio_empty_hint', 'MP3, M4A, OGG oder WAV in den Album-Ordner kopieren.')}</small></div>`;
            return;
        }

        grid.innerHTML = '';
        data.unassigned.forEach(file => {
            const item = document.createElement('div');
            item.className = 'add-audio-item';
            item.innerHTML = `<span class="add-audio-icon">&#127925;</span><span class="add-audio-name">${file.file}</span>`;
            item.onclick = () => addAudioToAlbum(file);
            grid.appendChild(item);
        });

    } catch (err) {
        document.getElementById('add-audio-grid').innerHTML =
            `<div class="add-photo-error">${_t('add_modal.err_prefix', '❌ Fehler: {msg}').replace('{msg}', err.message)}</div>`;
    }
}

function closeAddAudioModal() {
    const modal = document.getElementById('add-audio-modal');
    if (modal) modal.classList.add('hidden');
}

// ============================================
// Tour (placeholder)
// ============================================

// MPD Tours browser inside album edit: lists all global tours (GET /api/tours),
// pre-filtered to the album's photo date range, one click copies the GPX into
// the album folder (if needed) and inserts the tour element.
async function openAddTourModal() {
    closeFabMenu();
    const existing = document.getElementById('add-tour-modal');
    if (existing) existing.remove();

    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;

    // Album photo date range, derived from photo filenames (YYYY-MM-DD prefix).
    const range = _albumPhotoDateRange();

    const modal = document.createElement('div');
    modal.id = 'add-tour-modal';
    modal.className = 'add-photo-modal';

    const box = document.createElement('div');
    box.className = 'add-photo-box tour-browser-box';
    box.innerHTML = `
        <div class="add-photo-header">
            <h4>${_t('add_modal.tour_title', '🗺️ Tour einfügen')}</h4>
            <button class="add-photo-close" title="${_t('add_modal.close_title', 'Close')}" onclick="closeAddTourModal()">×</button>
        </div>
        <div class="add-photo-body">
            <div class="tour-browser-bar">
                <span class="tour-browser-range" id="tour-browser-range"></span>
                <label class="tour-browser-showall">
                    <input type="checkbox" id="tour-browser-showall">
                    ${_t('add_modal.tour_browser_show_all', 'Alle Touren zeigen')}
                </label>
            </div>
            <div class="tour-browser-list" id="tour-browser-list">
                <div class="tour-browser-empty">…</div>
            </div>
            <div class="tour-browser-status" id="tour-browser-status"></div>
        </div>`;

    modal.appendChild(box);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeAddTourModal(); });
    document.body.appendChild(modal);
    _addSwipeDownToClose(modal, closeAddTourModal);

    // ESC closes the tour modal. Listener is removed again in closeAddTourModal.
    modal._onEscape = (e) => { if (e.key === 'Escape') closeAddTourModal(); };
    document.addEventListener('keydown', modal._onEscape);
    requestAnimationFrame(() => modal.classList.add('active'));

    const rangeEl = document.getElementById('tour-browser-range');
    rangeEl.textContent = range
        ? `${_fmtIsoDate(range.min)} – ${_fmtIsoDate(range.max)}`
        : _t('add_modal.tour_no_range', 'Kein Fotozeitraum erkennbar');

    // Load global tours + GPX already in the album folder, in parallel.
    let allTours = [];
    let folderSet = new Set();
    try {
        const [toursRes, gpxRes] = await Promise.all([
            fetch(`${API_BASE}/api/tours`),
            fetch(`${API_BASE}/api/gpx-files/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}`),
        ]);
        if (toursRes.ok) { const d = await toursRes.json(); allTours  = d.tours || []; }
        if (gpxRes.ok)   { const d = await gpxRes.json();  folderSet = new Set(d.files || []); }
    } catch (e) {}

    const showAll = document.getElementById('tour-browser-showall');
    if (!range) { showAll.checked = true; showAll.disabled = true; }  // no range → show all
    showAll.addEventListener('change', renderBrowser);

    function renderBrowser() {
        const list = document.getElementById('tour-browser-list');
        if (!list) return;
        list.innerHTML = '';

        const useFilter = range && !showAll.checked;
        const shown = useFilter
            ? allTours.filter(t => t.date && t.date >= range.min && t.date <= range.max)
            : allTours.slice();

        // GPX physically in the album folder but not in the global tours list.
        const toursNames = new Set(allTours.map(t => t.filename));
        const folderOnly = [...folderSet].filter(f => !toursNames.has(f)).sort().reverse();

        if (!shown.length && !folderOnly.length) {
            const empty = document.createElement('div');
            empty.className = 'tour-browser-empty';
            empty.textContent = useFilter
                ? _t('add_modal.tour_browser_none_in_range', '— Keine Touren in diesem Zeitraum —')
                : _t('add_modal.tour_empty', '— Keine Touren gefunden —');
            list.appendChild(empty);
            return;
        }

        shown.forEach(t => list.appendChild(_tourBrowserRow(t, folderSet.has(t.filename), folderSet)));

        if (folderOnly.length) {
            const sec = document.createElement('div');
            sec.className = 'tour-browser-section';
            sec.textContent = _t('add_modal.tour_folder_section', 'Im Album-Ordner');
            list.appendChild(sec);
            folderOnly.forEach(f => list.appendChild(
                _tourBrowserRow({ filename: f, date: '', label: '' }, true, folderSet)));
        }
    }

    renderBrowser();
}

// Build one clickable tour row. `folderSet` is passed so the click handler can
// decide whether a copy is still needed.
function _tourBrowserRow(t, inAlbum, folderSet) {
    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    const row = document.createElement('div');
    row.className = 'tour-browser-row';

    const meta = [];
    if (t.dist)     meta.push(`<span class="tbr-dist">📍 ${t.dist} km</span>`);
    if (t.duration) meta.push(`<span>⏱ ${t.duration}</span>`);
    if (t.hr_avg)   meta.push(`<span>♥ Ø${t.hr_avg}</span>`);

    row.innerHTML = `
        <div class="tbr-main">
            <div class="tbr-date">${t.date ? _fmtIsoDate(t.date) : t.filename}</div>
            <div class="tbr-sport">${t.label || ''}</div>
            <div class="tbr-meta">${meta.join('')}</div>
        </div>
        ${inAlbum ? `<span class="tbr-badge">${_t('add_modal.tour_in_album', 'im Album')}</span>` : ''}`;

    row.addEventListener('click', () => selectTourFromBrowser(t.filename, folderSet));
    return row;
}

// Copy the GPX into the album folder (unless it is already there) and insert the
// tour element with defaults. 409 from /copy means the file already exists → OK.
async function selectTourFromBrowser(filename, folderSet) {
    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    const status = document.getElementById('tour-browser-status');
    try {
        if (!folderSet || !folderSet.has(filename)) {
            if (status) { status.style.color = ''; status.textContent = _t('add_modal.tour_copying', 'Kopiere …'); }
            const res = await fetch(`${API_BASE}/api/tours/copy`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ filename, album: albumName, space: albumSpace }),
            });
            if (!res.ok && res.status !== 409) {
                const d = await res.json().catch(() => ({}));
                throw new Error(d.detail || res.status);
            }
        }
        addTourToAlbum({ file: filename });
        closeAddTourModal();
    } catch (e) {
        if (status) {
            status.style.color = '#f05a5a';
            status.textContent = `${_t('add_modal.tour_copy_error', 'Fehler')}: ${e.message}`;
        }
    }
}

// Earliest/latest photo date in the current album, from the YYYY-MM-DD filename
// prefix (photos are stored as e.g. 2005-01-09_18-59-11.jpg). Null if none parse.
function _albumPhotoDateRange() {
    if (!albumData || !Array.isArray(albumData.elements)) return null;
    let min = null, max = null;
    const re = /^(\d{4}-\d{2}-\d{2})/;
    for (const el of albumData.elements) {
        if (el.type !== 'photo' || !el.file) continue;
        const m = re.exec(el.file);
        if (!m) continue;
        const d = m[1];
        if (min === null || d < min) min = d;
        if (max === null || d > max) max = d;
    }
    return min ? { min, max } : null;
}

function _fmtIsoDate(ds) {
    if (!ds || ds.length < 10) return ds || '';
    const d = new Date(ds + 'T12:00:00');
    if (isNaN(d.getTime())) return ds;
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

function closeAddTourModal() {
    const modal = document.getElementById('add-tour-modal');
    if (!modal) return;
    if (modal._onEscape) {
        document.removeEventListener('keydown', modal._onEscape);
        modal._onEscape = null;
    }
    modal.classList.remove('active');
    setTimeout(() => modal.remove(), 200);
}

// ============================================
// Document modal (PDF reader / image full view)
// ============================================

function openDocumentModal(elem) {
    let modal = document.getElementById('document-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'document-modal';
        modal.className = 'document-modal';
        modal.innerHTML = `
            <div class="document-modal-inner">
                <div class="document-modal-header">
                    <span id="document-modal-title" class="document-modal-title"></span>
                    <button class="document-modal-close" onclick="closeDocumentModal()">×</button>
                </div>
                <div class="document-modal-body" id="document-modal-body"></div>
            </div>`;
        modal.addEventListener('click', (e) => { if (e.target === modal) closeDocumentModal(); });
        document.body.appendChild(modal);
        _addSwipeDownToClose(modal, closeDocumentModal);
    }
    const titleEl = document.getElementById('document-modal-title');
    const body    = document.getElementById('document-modal-body');
    if (titleEl) titleEl.textContent = elem.title || elem.file;
    body.innerHTML = '';

    const isPdf = elem.file && elem.file.toLowerCase().endsWith('.pdf');
    const url   = elem.document_url
        ? `${API_BASE}${elem.document_url}`
        : `${API_BASE}/api/document/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/${encodeURIComponent(elem.file)}`;

    const istText = elem.file && /\.(txt|md)$/i.test(elem.file);

    if (isPdf) {
        // Kein <iframe>: Android-WebView und Chromium-Browser auf Android
        // haben keinen PDF-Renderer — der Rahmen bliebe leer. pdf.js zeichnet
        // die Seiten selbst auf <canvas> und funktioniert damit ueberall
        // gleich: Desktop, Tablet, Handy, Wrapper-App und Share-Empfaenger.
        renderPdfInto(body, url);
    } else if (istText) {
        /* Dritter Zweig seit 15.09.2026. Vorher gab es nur PDF und Bild —
           eine Textdatei waere im else-Zweig als <img> gelandet und haette
           ein kaputtes Bild ergeben. Im Album steht sie aufklappbar; hier
           gibt es mehr Platz und einen eigenen Scrollbereich, ohne dass
           sich das Album darunter verschiebt. */
        const _tt = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
        const box = document.createElement('pre');
        box.className = 'document-modal-text';
        box.textContent = _tt('album.document_loading', 'Wird geladen …');
        body.appendChild(box);
        fetch(url, { credentials: 'same-origin' })
            .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
            .then(text => {
                /* Auch hier ein Deckel, nur ein hoeherer als im Album
                   (dort 100 KB): Das Modal ist die "ganze Datei"-Ansicht,
                   aber ein Logfile mit 50 MB legt den Browser lahm. */
                const MAX = 2 * 1024 * 1024;
                if (text.length > MAX) {
                    box.textContent = text.slice(0, MAX);
                    const rest = document.createElement('div');
                    rest.className = 'document-text-more';
                    rest.textContent = _tt('album.document_truncated',
                        'Gekürzt — die vollständige Datei liegt im Albumordner.');
                    body.appendChild(rest);
                } else {
                    box.textContent = text;
                }
            })
            .catch(err => { box.textContent = '⚠️ ' + err.message; });
    } else {
        const img = document.createElement('img');
        img.src = url;
        img.className = 'document-modal-img';
        body.appendChild(img);
    }

    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
}

// ── PDF-Anzeige via pdf.js ────────────────────────────────────────────
// pdf.min.js (320 KB) + pdf.worker.min.js (1,06 MB) liegen lokal unter
// /js/. Geladen wird erst beim ersten Oeffnen eines PDFs, damit normales
// Albumblaettern nichts davon merkt.

let _pdfLibPromise = null;

function _loadPdfLib() {
    if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
    if (_pdfLibPromise) return _pdfLibPromise;
    _pdfLibPromise = new Promise((resolve, reject) => {
        const sc = document.createElement('script');
        sc.src = '/js/pdf.min.js';
        sc.onload = () => {
            if (!window.pdfjsLib) { reject(new Error('pdfjsLib fehlt')); return; }
            window.pdfjsLib.GlobalWorkerOptions.workerSrc = '/js/pdf.worker.min.js';
            resolve(window.pdfjsLib);
        };
        sc.onerror = () => reject(new Error('pdf.min.js nicht ladbar'));
        document.head.appendChild(sc);
    });
    return _pdfLibPromise;
}

async function renderPdfInto(container, url) {
    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;

    container.classList.add('is-pdf');
    const wrap = document.createElement('div');
    wrap.className = 'pdf-pages';
    const status = document.createElement('div');
    status.className = 'pdf-status';
    status.textContent = _t('album.pdf_loading', 'PDF wird geladen…');
    container.appendChild(status);
    container.appendChild(wrap);

    // Merker: ein spaeter eintreffendes Rendering darf ein inzwischen
    // geschlossenes oder neu befuelltes Modal nicht mehr beschreiben.
    const token = Symbol('pdf');
    container._pdfToken = token;

    try {
        const pdfjsLib = await _loadPdfLib();
        if (container._pdfToken !== token) return;

        const doc = await pdfjsLib.getDocument({ url, withCredentials: true }).promise;
        if (container._pdfToken !== token) return;

        /* Fenster einmal messen — Breite UND Hoehe. Die Hoehe fehlte bis
           zum 15.09.2026, und deshalb wurde jede Seite auf Fensterbreite
           gezogen: ein Supermarktbon (rund 80 mm breit) wuchs dabei aufs
           Zweieinhalbfache und fuellte scrollend den ganzen Schirm,
           waehrend ein A4-Blatt daneben genauso breit dastand. Die
           Seitengroesse des PDFs kam ueberhaupt nicht vor.
           Gemessen wird erst hier, nach den await-Punkten: Davor ist das
           Modal noch `display:none` und alle Masse waeren 0. */
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const cssWidth = Math.max(280, container.clientWidth - 24);
        /* Die Hoehe NICHT am Kasten messen: Der Modalrahmen hat keine
           feste Hoehe, er waechst mit dem Inhalt bis 92vh — und Inhalt
           gibt es hier noch keinen, `clientHeight` waere die Hoehe der
           Ladezeile. Also am Fenster rechnen, wie es das CSS auch tut,
           abzueglich der Kopfzeile. */
        const rahmen = container.closest('.document-modal-inner');
        const kopf = rahmen && rahmen.querySelector('.document-modal-header');
        const cssHeight = Math.max(200,
            Math.round(window.innerHeight * 0.92) - (kopf ? kopf.offsetHeight : 48) - 24);

        for (let n = 1; n <= doc.numPages; n++) {
            if (container._pdfToken !== token) return;
            const page = await page_render(doc, n, cssWidth, cssHeight, dpr);
            if (container._pdfToken !== token) return;
            wrap.appendChild(page);
            status.textContent = doc.numPages > 1
                ? `${n} / ${doc.numPages}`
                : '';
            if (n === doc.numPages) status.remove();
        }
    } catch (err) {
        status.remove();
        const box = document.createElement('div');
        box.className = 'pdf-error';
        box.textContent = _t('album.pdf_failed', 'Das PDF konnte nicht angezeigt werden.');
        const a = document.createElement('a');
        // mpdSetExternalLink aus album-render.js — im WebView waere
        // target="_blank" wirkungslos.
        if (window.mpdSetExternalLink) mpdSetExternalLink(a, url);
        else { a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
        a.className = 'pdf-error-link';
        a.textContent = _t('album.pdf_open_extern', 'Extern öffnen');
        box.appendChild(a);
        wrap.appendChild(box);
        console.error('[MPD pdf]', err);
    }
}

async function page_render(doc, pageNo, cssWidth, cssHeight, dpr) {
    const page = await doc.getPage(pageNo);
    const base = page.getViewport({ scale: 1 });

    /* Originalgroesse, aber nie groesser als das Fenster.

       Ein PDF kennt seine wirkliche Groesse: Es rechnet in Punkten zu
       1/72 Zoll. Ein Kassenbon ist rund 80 mm breit, ein A4-Blatt 210 mm
       — und so sollen sie auch aussehen. Bis zum 15.09.2026 wurde
       stattdessen jede Seite auf Fensterbreite gezogen, der Bon also
       aufs Zweieinhalbfache aufgeblasen.

       PT_JE_PX = 96/72: Die uebliche Umrechnung von Punkt in Bildpunkt,
       dasselbe, was in jedem PDF-Betrachter "100 %" bedeutet. Auf einem
       Bildschirm mit anderer Punktdichte ist es eine Naeherung — die
       Verhaeltnisse untereinander stimmen aber immer.

       Die beiden Einpass-Faktoren stehen daneben und greifen nur, wenn
       die Seite sonst nicht hineinpasst: Ein A4-Blatt waere bei 100 %
       rund 1120 Bildpunkte hoch und damit hoeher als das Modal, es wird
       also verkleinert. Der Bon bleibt klein. */
    const PT_JE_PX = 96 / 72;
    const scale = Math.min(cssWidth / base.width,
                           cssHeight / base.height,
                           PT_JE_PX);
    const viewport = page.getViewport({ scale: scale * dpr });

    const canvas = document.createElement('canvas');
    canvas.className = 'pdf-page';
    canvas.width  = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    canvas.style.width  = Math.floor(viewport.width / dpr) + 'px';
    canvas.style.height = Math.floor(viewport.height / dpr) + 'px';

    await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
    return canvas;
}

function closeDocumentModal() {
    const modal = document.getElementById('document-modal');
    if (modal) modal.classList.remove('active');
    // Laufendes PDF-Rendering entwerten und Speicher der Canvas freigeben.
    const body = document.getElementById('document-modal-body');
    if (body) { body._pdfToken = null; body.innerHTML = ''; body.classList.remove('is-pdf'); }
    document.body.style.overflow = '';
}

// ============================================
// Video modal
// ============================================

function openVideoModal(elem) {
    let modal = document.getElementById('video-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'video-modal';
        modal.className = 'video-modal';
        modal.innerHTML = `
            <div class="video-modal-inner">
                <button class="video-modal-close" onclick="closeVideoModal()">×</button>
                <video id="video-modal-player" controls preload="auto"></video>
            </div>`;
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeVideoModal();
        });
        document.body.appendChild(modal);
        _addSwipeDownToClose(modal, closeVideoModal);
    }
    const player = document.getElementById('video-modal-player');
    player.src = `${API_BASE}/api/video/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/${encodeURIComponent(elem.file)}`;
    player.load();
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeVideoModal() {
    const modal  = document.getElementById('video-modal');
    const player = document.getElementById('video-modal-player');
    if (player) { player.pause(); player.src = ''; }
    if (modal)  { modal.classList.remove('active'); }
    document.body.style.overflow = '';
}
