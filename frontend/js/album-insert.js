/* MPD – Element insert functions
 * generateId, getLastArrayIndexForDomElem,
 * insertTextAtScrollPosition, removePhotoFromAlbum,
 * addPhotoToAlbum, insertSeparatorAtScrollPosition,
 * addDocumentToAlbum, addAudioToAlbum, insertMapFromDialog,
 * insertLinkAtScrollPosition
 */

function generateId() {
    // PDX v1.1: 4-digit IDs 0001-9999, max(existing) + 1
    const existing = albumData.elements
        .map(e => parseInt(e.id))
        .filter(n => !isNaN(n));
    const next = existing.length > 0 ? Math.max(...existing) + 1 : 1;
    return String(next).padStart(4, '0');
}

// Returns the albumData.elements index of the LAST element represented by a dom element.
// For text: that element's own index.
// For photo (edit grid): that photo's own index.
// For photo-row (view grid): index of the last photo in the row.
function getLastArrayIndexForDomElem(domElem) {
    if (domElem.dataset.type === 'photo-row') {
        const ids = JSON.parse(domElem.dataset.ids || '[]');
        if (!ids.length) return -1;
        return albumData.elements.findIndex(e => e.id === ids[ids.length - 1]);
    }
    if (domElem.dataset.id) {
        return albumData.elements.findIndex(e => e.id === domElem.dataset.id);
    }
    return -1;
}

function insertTextAtScrollPosition() {
    if (!albumData) return;

    const viewportMidY = (window._fabScrollY ?? window.scrollY) + window.innerHeight / 2;
    const items = Array.from(
        document.getElementById('content').querySelectorAll('.draggable-element')
    );

    let insertAfterIndex = -1;
    for (const item of items) {
        const midY = item.offsetTop + item.offsetHeight / 2;
        if (midY <= viewportMidY) {
            const idx = getLastArrayIndexForDomElem(item);
            if (idx !== -1) insertAfterIndex = idx;
        } else {
            break;
        }
    }

    const newId = generateId();
    pushUndoState();
    albumData.elements.splice(insertAfterIndex + 1, 0, { id: newId, type: 'text', text: '', tags: [] });
    hasUnsavedChanges = true;
    const _sy = window.scrollY;
    enableEditMode();
    requestAnimationFrame(() => window.scrollTo(0, _sy));

    requestAnimationFrame(() => {
        const newBlock = document.querySelector(`.text-block[data-id="${newId}"]`);
        if (!newBlock) return;
        newBlock.classList.add('new-block-highlight');
        setTimeout(() => newBlock.classList.remove('new-block-highlight'), 5000);

        const focusInner = () => {
            const inner = newBlock.querySelector('.text-inner') ?? newBlock;
            // makeTextEditable leaves contentEditable off on touch (drag/edit
            // arbitration). For a brand-new block this is a deliberate user
            // gesture, so flip it on directly — otherwise focus() does nothing.
            inner.contentEditable = true;
            inner.focus();
            const range = document.createRange();
            range.selectNodeContents(inner);
            range.collapse(false);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
        };

        const isMobile = window.matchMedia('(max-width: 768px)').matches;
        if (isMobile) {
            // iOS/Safari only shows the keyboard when focus() runs inside the user gesture.
            // Therefore: instant scroll + immediate focus, no setTimeout in between.
            newBlock.scrollIntoView({ block: 'center' });
            focusInner();
        } else {
            newBlock.scrollIntoView({ behavior: 'smooth', block: 'center' });
            setTimeout(focusInner, 350);
        }
    });
}

function removePhotoFromAlbum(id, file) {
    const idx = albumData.elements.findIndex(e => e.id === id);
    if (idx === -1) return;

    pushUndoState();
    albumData.elements.splice(idx, 1);
    hasUnsavedChanges = true;

    // No confirmation dialog — the action is reversible via the undo stack.
    // Toast shows the undo option (tap button + Ctrl+Z hint).
    if (typeof window.mpdToast === 'function') {
        const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
        window.mpdToast(_t('album.toast_photo_removed', { file }, `"${file}" aus Album entfernt`), {
            action   : _t('album.toast_undo', null, 'Undo'),
            onAction : () => { if (typeof undo === 'function') undo(); },
            duration : 4000,
        });
    }

    const photoItem = document.querySelector(`.photo-item[data-id="${CSS.escape(id)}"]`);
    const rerender = () => {
        const _sy = window.scrollY;
        if (isEditMode) enableEditMode();
        else renderAlbum();
        requestAnimationFrame(() => window.scrollTo(0, _sy));
        scheduleAutoSave();
    };

    if (photoItem) {
        photoItem.style.transition = 'opacity 0.25s, transform 0.25s';
        photoItem.style.opacity = '0';
        photoItem.style.transform = 'scale(0.9)';
        setTimeout(rerender, 250);
    } else {
        rerender();
    }
}

async function fetchRecycleStatus() {
    try {
        const resp = await fetch(API_BASE + '/api/recycle-status/' + albumSpace);
        if (resp.ok) {
            const data = await resp.json();
            window._recycleActive = data.recycled;
        }
    } catch (e) {
        window._recycleActive = null;
    }
}

// Fehler beim Loeschen als Dialog — geteilt mit der Lightbox
// (lightbox-editor.js). Der 409-Fall ist der wichtige: Seit 06.09. lehnt
// der Server ab, wenn ein ANDERES Album die Datei per source_album
// einbettet, und schickt die Alben als `used_by` mit. Vorher stand hier
// ein natives alert() mit „[object Object]", weil detail ein Objekt ist.
function showDeleteErrorDialog(status, result, file) {
    const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
    const d = (result && result.detail) || {};
    const usedBy = (status === 409 && d && Array.isArray(d.used_by)) ? d.used_by : null;
    if (usedBy && usedBy.length) {
        const albums = usedBy.map(a => '• ' + (a.title || a.album)).join('\n');
        return window.mpdAlert({
            icon    : '🔗',
            title   : _t('album.delete_used_by_title', null, 'Foto wird woanders gebraucht'),
            subtitle: file,
            body    : _t('album.delete_used_by_body', { count: usedBy.length, albums },
                         'Eingebettet in ' + usedBy.length + ' weiteren Album(en):\n' + albums),
            hint    : _t('album.delete_used_by_hint', null, 'Dort zuerst entfernen, dann hier löschen.'),
            hintType: 'warn',
        });
    }
    const text = typeof d === 'string' ? d : (d.detail || (result && result.statusText) || '');
    return window.mpdAlert({
        icon    : '⚠️',
        title   : _t('album.delete_failed_title', null, 'Löschen fehlgeschlagen'),
        subtitle: file,
        body    : text,
    });
}

function showWasThumbnailDialog(file) {
    const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
    return window.mpdAlert({
        icon : '🖼',
        title: _t('album.was_thumbnail_title', null, 'Titelbild entfernt'),
        body : _t('album.was_thumbnail_body', { file },
                  '„' + file + '" war das Titelbild des Albums. Bitte im Titelbild-Menü ein neues wählen.'),
    });
}

async function deletePhotoFile(id, file) {
    const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
    const recycleActive = window._recycleActive;
    const hint = recycleActive === true
        ? _t('album.delete_hint_recycle', null, 'Die Datei landet im Papierkorb.')
        : recycleActive === false
            ? _t('album.delete_hint_norecycle', null, 'No recycle bin active — the file will be deleted irreversibly.')
            : _t('album.delete_hint_default', null, 'This action cannot be undone.');

    const ok = await window.mpdConfirm({
        icon        : '🗑',
        title       : _t('album.delete_file_title', null, 'Delete file permanently?'),
        subtitle    : file,
        body        : _t('album.delete_file_body', null, 'Datei, Thumbnails und alle Backup-Versionen (.bak) werden entfernt.'),
        hint        : hint,
        hintType    : recycleActive === true ? 'info' : 'warn',
        confirmLabel: _t('album.delete_label', null, 'Delete'),
        destructive : true,
    });
    if (!ok) return;

    try {
        const resp = await fetch(
            API_BASE + '/api/album/' + albumSpace + '/' + encodeURIComponent(albumName) + '/file',
            {
                method : 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                // album_context: Dieser Weg IST der Album-Kontext (Datei
                // aus dem Albumordner). Ohne das Feld lehnt der Server
                // seit 06.09. ab — „im Album loeschen, nicht von hier".
                body   : JSON.stringify({ filename: file, album_context: true }),
            }
        );
        const result = await resp.json().catch(() => ({}));

        if (!resp.ok) {
            await showDeleteErrorDialog(resp.status, Object.assign({ statusText: resp.statusText }, result), file);
            return;
        }

        if (result.was_thumbnail) {
            await showWasThumbnailDialog(file);
        }

        // No pushUndoState — file is physically gone, undo would be misleading
        const idx = albumData.elements.findIndex(e => e.id === id);
        if (idx !== -1) {
            albumData.elements.splice(idx, 1);
            hasUnsavedChanges = true;
        }

        const photoItem = document.querySelector('.photo-item[data-id="' + CSS.escape(id) + '"]');
        const rerender = () => {
            const _sy = window.scrollY;
            if (isEditMode) enableEditMode();
            else renderAlbum();
            requestAnimationFrame(() => window.scrollTo(0, _sy));
            scheduleAutoSave();
        };

        if (photoItem) {
            photoItem.style.transition = 'opacity 0.25s, transform 0.25s';
            photoItem.style.opacity = '0';
            photoItem.style.transform = 'scale(0.9)';
            setTimeout(rerender, 250);
        } else {
            rerender();
        }

    } catch (err) {
        await window.mpdAlert({
            icon : '⚠️',
            title: _t('album.delete_failed_title', null, 'Löschen fehlgeschlagen'),
            body : err.message,
        });
    }
}

// Mehrere Fotos auf einmal (08.09.2026): EIN Undo-Schritt, EIN Rendern,
// EIN Speichern. Reihenfolge = Reihenfolge der Liste (der Server liefert
// den Ordner sortiert, bei ISO-Namen also chronologisch). Eingefuegt wird
// an der Scrollposition, wie beim Einzelfoto.
function addPhotosToAlbum(files) {
    if (!files || !files.length) return;
    const viewportMidY = (window._fabScrollY ?? window.scrollY) + window.innerHeight / 2;
    const items = Array.from(
        document.getElementById('content').querySelectorAll('.draggable-element')
    );
    let insertAfterIndex = -1;
    for (const item of items) {
        const midY = item.offsetTop + item.offsetHeight / 2;
        if (midY <= viewportMidY) {
            const idx = getLastArrayIndexForDomElem(item);
            if (idx !== -1) insertAfterIndex = idx;
        } else break;
    }

    pushUndoState();
    const newIds = [];
    files.forEach((file, i) => {
        const newId = generateId();
        newIds.push(newId);
        // thumbnails/original kommen seit 08.09. aus /unassigned-photos mit;
        // fehlen sie (aelterer Server), baut mpdThumbUrls() sie beim Rendern.
        albumData.elements.splice(insertAfterIndex + 1 + i, 0, {
            id:   newId,
            type: file.type || 'photo',
            file: file.file,
            thumbnails: file.thumbnails || undefined,
            original:   file.original   || undefined,
            tags: []
        });
    });
    hasUnsavedChanges = true;
    closeAddPhotoModal();
    document.getElementById('add-photo-modal')?.remove();
    const _sy = window.scrollY;
    if (isEditMode) enableEditMode(); else renderAlbum();
    requestAnimationFrame(() => {
        window.scrollTo(0, _sy);
        scheduleAutoSave();
        if (typeof refreshUnassignedHint === 'function') refreshUnassignedHint();
        let first = null;
        for (const id of newIds) {
            const el = document.querySelector(`.photo-item[data-id="${CSS.escape(id)}"]`);
            if (!el) continue;
            if (!first) first = el;
            el.classList.add('new-photo-highlight');
            setTimeout(() => el.classList.remove('new-photo-highlight'), 2500);
        }
        if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
}

function addPhotoToAlbum(file) {
    addPhotosToAlbum([file]);
}

function insertSeparatorAtScrollPosition(label, style) {
    if (!albumData) return;
    const scrollY = window._fabScrollY ?? window.scrollY;
    const viewportMidY = scrollY + window.innerHeight / 2;
    const items = Array.from(document.getElementById('content').querySelectorAll('.draggable-element'));
    let insertAfterIndex = -1;
    for (const item of items) {
        const midY = item.offsetTop + item.offsetHeight / 2;
        if (midY <= viewportMidY) {
            const idx = getLastArrayIndexForDomElem(item);
            if (idx !== -1) insertAfterIndex = idx;
        } else break;
    }
    const newId = generateId();
    const newElem = { id: newId, type: 'separator' };
    if (label) newElem.label = label;
    if (style && style !== 'line') newElem.style = style;
    pushUndoState();
    albumData.elements.splice(insertAfterIndex + 1, 0, newElem);
    hasUnsavedChanges = true;
    const _sy = window.scrollY;
    enableEditMode();
    requestAnimationFrame(() => {
        window.scrollTo(0, _sy);
        const el = document.querySelector(`.separator-element[data-id="${newId}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        scheduleAutoSave();
    });
}

// PDX v1.4.1: Web-Link. Verweist nach draußen und hat deshalb — anders als
// alle anderen Element-Typen — keine Datei im Albumordner.
function insertLinkAtScrollPosition(url, title, note) {
    if (!albumData) return;
    const scrollY = window._fabScrollY ?? window.scrollY;
    const viewportMidY = scrollY + window.innerHeight / 2;
    const items = Array.from(document.getElementById('content').querySelectorAll('.draggable-element'));
    let insertAfterIndex = -1;
    for (const item of items) {
        const midY = item.offsetTop + item.offsetHeight / 2;
        if (midY <= viewportMidY) {
            const idx = getLastArrayIndexForDomElem(item);
            if (idx !== -1) insertAfterIndex = idx;
        } else break;
    }
    const newId = generateId();
    const newElem = { id: newId, type: 'link', url: url };
    if (title) newElem.title = title;
    if (note)  newElem.note  = note;
    pushUndoState();
    albumData.elements.splice(insertAfterIndex + 1, 0, newElem);
    hasUnsavedChanges = true;
    const _sy = window.scrollY;
    enableEditMode();
    requestAnimationFrame(() => {
        window.scrollTo(0, _sy);
        const el = document.querySelector(`.link-element[data-id="${newId}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        scheduleAutoSave();
    });
}

function addDocumentToAlbum(file) {
    const viewportMidY = (window._fabScrollY ?? window.scrollY) + window.innerHeight / 2;
    const items = Array.from(document.getElementById('content').querySelectorAll('.draggable-element'));
    let insertAfterIndex = -1;
    for (const item of items) {
        const midY = item.offsetTop + item.offsetHeight / 2;
        if (midY <= viewportMidY) {
            const idx = getLastArrayIndexForDomElem(item);
            if (idx !== -1) insertAfterIndex = idx;
        } else break;
    }

    const newId = generateId();
    pushUndoState();
    albumData.elements.splice(insertAfterIndex + 1, 0, {
        id:   newId,
        type: 'document',
        file: file.file,
    });
    hasUnsavedChanges = true;
    closeAddDocumentModal();
    document.getElementById('add-document-modal')?.remove();
    const _sy = window.scrollY;
    if (isEditMode) enableEditMode(); else renderAlbum();
    requestAnimationFrame(() => {
        window.scrollTo(0, _sy);
        scheduleAutoSave();
        const el = document.querySelector(`.document-element[data-id="${newId}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
}

function addAudioToAlbum(file) {
    const viewportMidY = (window._fabScrollY ?? window.scrollY) + window.innerHeight / 2;
    const items = Array.from(document.getElementById('content').querySelectorAll('.draggable-element'));
    let insertAfterIndex = -1;
    for (const item of items) {
        const midY = item.offsetTop + item.offsetHeight / 2;
        if (midY <= viewportMidY) {
            const idx = getLastArrayIndexForDomElem(item);
            if (idx !== -1) insertAfterIndex = idx;
        } else break;
    }
    const newId = generateId();
    pushUndoState();
    albumData.elements.splice(insertAfterIndex + 1, 0, { id: newId, type: 'audio', file: file.file });
    hasUnsavedChanges = true;
    closeAddAudioModal();
    document.getElementById('add-audio-modal')?.remove();
    const _sy = window.scrollY;
    if (isEditMode) enableEditMode(); else renderAlbum();
    requestAnimationFrame(() => {
        window.scrollTo(0, _sy);
        scheduleAutoSave();
        const el = document.querySelector(`.audio-element[data-id="${newId}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
}

function addTourToAlbum(file) {
    const viewportMidY = (window._fabScrollY ?? window.scrollY) + window.innerHeight / 2;
    const items = Array.from(document.getElementById('content').querySelectorAll('.draggable-element'));
    let insertAfterIndex = -1;
    for (const item of items) {
        const midY = item.offsetTop + item.offsetHeight / 2;
        if (midY <= viewportMidY) {
            const idx = getLastArrayIndexForDomElem(item);
            if (idx !== -1) insertAfterIndex = idx;
        } else break;
    }
    const newId = generateId();
    const newElem = { id: newId, type: 'tour', file: file.file };
    if (file.title)                         newElem.title          = file.title;
    if (file.show_elevation === false)      newElem.show_elevation = false;  // default true
    if (file.map_layer && file.map_layer !== 'osm') newElem.map_layer = file.map_layer;
    pushUndoState();
    albumData.elements.splice(insertAfterIndex + 1, 0, newElem);
    hasUnsavedChanges = true;
    const _sy = window.scrollY;
    if (isEditMode) enableEditMode(); else renderAlbum();
    requestAnimationFrame(() => {
        window.scrollTo(0, _sy);
        scheduleAutoSave();
        const el = document.querySelector(`.tour-element[data-id="${newId}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
}
