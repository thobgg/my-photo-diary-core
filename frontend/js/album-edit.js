/* MPD – Edit Mode
 * Undo/Redo, meta editing, text editing, text toolbar,
 * text-style selector, toggleShowLocked, initEditMode
 */

// ============================================
// Undo / Redo – history stack
// ============================================

function cloneState() {
    return {
        elements: JSON.parse(JSON.stringify(albumData.elements)),
        meta: JSON.parse(JSON.stringify(albumData.meta))
    };
}

function pushUndoState() {
    if (!albumData) return;
    undoStack.push(cloneState());
    if (undoStack.length > MAX_UNDO) undoStack.shift();
    redoStack.length = 0;
    updateUndoRedoUI();
}

function undo() {
    if (!undoStack.length || !isEditMode) return;
    redoStack.push(cloneState());
    const prev = undoStack.pop();
    albumData.elements = prev.elements;
    albumData.meta = prev.meta;
    hasUnsavedChanges = true;
    const scrollPos = window.scrollY;
    enableEditMode();
    requestAnimationFrame(() => window.scrollTo(0, scrollPos));
    updateUndoRedoUI();
    updateSaveIndicator('unsaved');
    scheduleAutoSave();
}

function redo() {
    if (!redoStack.length || !isEditMode) return;
    undoStack.push(cloneState());
    const next = redoStack.pop();
    albumData.elements = next.elements;
    albumData.meta = next.meta;
    hasUnsavedChanges = true;
    const scrollPos = window.scrollY;
    enableEditMode();
    requestAnimationFrame(() => window.scrollTo(0, scrollPos));
    updateUndoRedoUI();
    updateSaveIndicator('unsaved');
    scheduleAutoSave();
}

function updateUndoRedoUI() {
    const undoBtn = document.getElementById('undo-btn');
    const redoBtn = document.getElementById('redo-btn');
    if (undoBtn) undoBtn.disabled = undoStack.length === 0;
    if (redoBtn) redoBtn.disabled = redoStack.length === 0;
}

function clearUndoHistory() {
    undoStack.length = 0;
    redoStack.length = 0;
    updateUndoRedoUI();
}

// ============================================
// show_locked toggle (key icon in album header)
// ============================================

function toggleShowLocked() {
    if (!albumData) return;
    albumData.meta.show_locked = !(albumData.meta.show_locked ?? true);
    const scrollPos = window.scrollY;
    if (isEditMode) enableEditMode();
    else            renderAlbum();
    requestAnimationFrame(() => window.scrollTo(0, scrollPos));
}

// ============================================
// Edit mode init / toggle
// ============================================

function initEditMode() {
    const toggleButton = document.getElementById('edit-mode-toggle');
    if (!toggleButton) return;

    window.Companion?.init();

    toggleButton.addEventListener('click', () => {
        isEditMode = !isEditMode;
        updateEditModeUI();
    });

    document.addEventListener('keydown', (e) => {
        if (!isEditMode) return;
        const tag = document.activeElement?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA') return;
        if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
            e.preventDefault();
            if (e.shiftKey) redo(); else undo();
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'y') {
            e.preventDefault();
            redo();
        }
    });

    const undoBtn = document.getElementById('undo-btn');
    const redoBtn = document.getElementById('redo-btn');
    if (undoBtn) undoBtn.addEventListener('click', undo);
    if (redoBtn) redoBtn.addEventListener('click', redo);
}

function updateEditModeUI() {
    const toggleButton = document.getElementById('edit-mode-toggle');
    const editLabel    = document.getElementById('edit-label');
    const content      = document.getElementById('content');
    const fabContainer = document.getElementById('fab-container');
    const thumbBtn     = document.getElementById('thumbnail-change-btn');
    const undoBtn      = document.getElementById('undo-btn');
    const redoBtn      = document.getElementById('redo-btn');
    const saveStatus   = document.getElementById('edit-save-status');
    const countEl      = document.getElementById('album-count');
    const _scrollPos   = window.scrollY;

    // Pencil/check toggle (header + primary FAB) is CSS-driven via
    // body.edit-mode-active — both SVGs sit in the DOM, only one shows.

    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;

    if (isEditMode) {
        clearUndoHistory();
        toggleButton.classList.add('active');
        if (editLabel) editLabel.textContent = _t('album.edit_label_done', 'Fertig');
        content.classList.add('edit-mode-active');
        document.body.classList.add('edit-mode-active');
        if (fabContainer) fabContainer.classList.remove('hidden');
        if (thumbBtn)  thumbBtn.classList.remove('hidden');
        if (undoBtn)   undoBtn.classList.remove('hidden');
        if (redoBtn)   redoBtn.classList.remove('hidden');
        if (saveStatus) { saveStatus.classList.remove('hidden'); saveStatus.textContent = ''; }
        if (countEl)   countEl.classList.add('hidden');
        enableEditMode();
    } else {
        toggleButton.classList.remove('active');
        if (editLabel) editLabel.textContent = _t('album.edit_label_edit', 'Bearbeiten');
        content.classList.remove('edit-mode-active');
        document.body.classList.remove('edit-mode-active');
        if (fabContainer) { fabContainer.classList.add('hidden'); closeFabMenu(); }
        if (thumbBtn)  thumbBtn.classList.add('hidden');
        if (undoBtn)   undoBtn.classList.add('hidden');
        if (redoBtn)   redoBtn.classList.add('hidden');
        if (saveStatus) saveStatus.classList.add('hidden');
        if (countEl)   countEl.classList.remove('hidden');
        disableEditMode();
    }

    requestAnimationFrame(() => window.scrollTo(0, _scrollPos));
}

// ============================================
// Meta editing (title, year)
// ============================================

function enableMetaEdit() {
    const titleEl = document.getElementById('album-title');
    const metaEl  = document.getElementById('album-meta');
    if (!titleEl || !metaEl) return;

    // Title → input
    const titleInput = document.createElement('input');
    titleInput.type        = 'text';
    titleInput.id          = 'meta-title-input';
    titleInput.className   = 'meta-edit-input meta-edit-title';
    titleInput.value       = albumData.meta.title || '';
    titleInput.placeholder = (window.MPD_I18N ? window.MPD_I18N.t('album.title_placeholder') : 'Album-Titel');
    titleInput.addEventListener('focus', () => pushUndoState());
    titleInput.addEventListener('input', () => {
        albumData.meta.title = titleInput.value.trim() || albumData.meta.title;
        hasUnsavedChanges = true;
        updateSaveIndicator('unsaved');
    });
    titleEl.replaceWith(titleInput);
    titleInput.id = 'album-title'; // preserve ID for disableMetaEdit

    // Year → input
    const yearInput = document.createElement('input');
    yearInput.type        = 'number';
    yearInput.id          = 'meta-year-input';
    yearInput.className   = 'meta-edit-input meta-edit-year';
    yearInput.value       = albumData.meta.year || '';
    yearInput.placeholder = (window.MPD_I18N ? window.MPD_I18N.t('album.year_placeholder') : 'Jahr');
    yearInput.min         = '1800';
    yearInput.max         = '2099';
    yearInput.addEventListener('focus', () => pushUndoState());
    yearInput.addEventListener('input', () => {
        const y = parseInt(yearInput.value);
        if (y >= 1800 && y <= 2099) {
            albumData.meta.year = y;
            hasUnsavedChanges = true;
            updateSaveIndicator('unsaved');
        }
    });
    metaEl.replaceWith(yearInput);
    yearInput.id = 'album-meta'; // preserve ID for disableMetaEdit
}

function disableMetaEdit() {
    const titleEl = document.getElementById('album-title');
    const metaEl  = document.getElementById('album-meta');
    if (!titleEl || !metaEl) return;

    // Input → h1 (only when currently input)
    if (titleEl.tagName === 'INPUT') {
        const h1 = document.createElement('h1');
        h1.id          = 'album-title';
        h1.textContent = albumData.meta.title || '';
        titleEl.replaceWith(h1);
    }

    // Input → span (only when currently input)
    if (metaEl.tagName === 'INPUT') {
        const sp = document.createElement('span');
        sp.id        = 'album-meta';
        sp.className = 'album-meta';
        sp.textContent = albumData.meta.year || '';
        metaEl.replaceWith(sp);
    }
}

function enableEditMode() {
    renderAlbum(true);
    document.querySelectorAll('.text-block').forEach(makeTextEditable);
    initDragAndDrop();
    enableMetaEdit();
}

async function disableEditMode() {
    document.querySelectorAll('.text-block').forEach(makeTextReadonly);
    destroyDragAndDrop();
    disableMetaEdit();
    document.getElementById('content').classList.remove('edit-grid-mode');

    // Autosave is the default — anyone who clicks "Done" wants to save.
    // Undo/Redo catches mistakes, a confirm dialog was just friction here.
    if (hasUnsavedChanges) {
        await saveAlbumData();
    }
    // Scroll-Erhalt: renderAlbum() läuft hier NACH dem await, also erst nach
    // dem Netzwerk-Save. Der rAF-Restore des Aufrufers (updateEditModeUI) ist
    // da längst gefeuert — ohne eigenen Restore springt das Album nach dem
    // Re-Render an den Anfang. Position direkt um den Rebuild sichern.
    const _scrollPos = window.scrollY;
    renderAlbum();
    requestAnimationFrame(() => window.scrollTo(0, _scrollPos));
}

// ============================================
// Make text block editable
// ============================================

function makeTextEditable(elem) {
    if (elem.dataset.editListenersAttached) return;
    elem.dataset.editListenersAttached = 'true';

    let inner = elem.querySelector('.text-inner');
    if (!inner) {
        inner = document.createElement('div');
        inner.className = 'text-inner';
        inner.textContent = elem.textContent.trim();
        elem.innerHTML = '';
        elem.appendChild(inner);
    }

    inner.classList.add('editable-inner');
    elem.classList.add('editable');

    // Drag-vs-edit arbitration — applied to every device, not gated on
    // matchMedia. Earlier we tried `(hover: none) and (pointer: coarse)`,
    // but some Android WebViews report it as `false`, so the touch branch
    // never ran and the keyboard always popped up. The arbitration also
    // works fine for mouse: a click is by nature sub-280ms with near-zero
    // movement, so the same path activates editing.
    //
    // Default off; flips to editable when pointerdown→pointerup happens
    // within 280ms and under 8px of movement. Long-press / drag attempts
    // fall through to SortableJS untouched.
    inner.contentEditable = false;

    const TAP_MS = 280;   // safely under SortableJS delay (500ms)
    const TAP_PX = 8;     // matches Sortable touchStartThreshold
    let downX = 0, downY = 0, downAt = 0, tracking = false;

    inner.addEventListener('pointerdown', (e) => {
        if (inner.contentEditable === 'true') return;
        downX = e.clientX; downY = e.clientY;
        downAt = Date.now();
        tracking = true;
    });
    inner.addEventListener('pointermove', (e) => {
        if (!tracking) return;
        if (Math.abs(e.clientX - downX) > TAP_PX ||
            Math.abs(e.clientY - downY) > TAP_PX) {
            tracking = false;
        }
    });
    inner.addEventListener('pointerup', (e) => {
        if (!tracking) return;
        tracking = false;
        if (Date.now() - downAt > TAP_MS) return;
        inner.contentEditable = true;
        inner.focus();
        const range = document.createRange();
        range.selectNodeContents(inner);
        range.collapse(false);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    });
    inner.addEventListener('pointercancel', () => { tracking = false; });

    // Enter inserts a literal newline instead of a block element.
    // textContent then preserves it, and white-space:pre-wrap on .text-inner
    // renders it. Ohne diesen Handler baut der Browser <div>/<br>, die
    // textContent ersatzlos verschluckt — genau so sind die Umbrueche in den
    // aus V1 uebernommenen Texten verlorengegangen.
    inner.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            document.execCommand('insertText', false, '\n');
        }
    });

    updatePlaceholder(elem, inner);

    inner.addEventListener('focus', () => {
        pushUndoState();
        elem.classList.add('editing');
        showTextToolbar(elem);
    });
    inner.addEventListener('blur', () => {
        // Short delay: OK/emoji click should finish before blur
        setTimeout(() => {
            if (!elem.contains(document.activeElement)) {
                elem.classList.remove('editing');
                hideTextToolbar(elem);
                handleTextChange(elem, inner.textContent);
                updatePlaceholder(elem, inner);
                // Re-lock so the next interaction can pick tap-vs-drag freely.
                inner.contentEditable = false;
            }
        }, 150);
    });
    inner.addEventListener('input', () => {
        hasUnsavedChanges = true;
        updateSaveIndicator('unsaved');
        updatePlaceholder(elem, inner);
    });

    if (!elem.querySelector('.delete-text-btn')) {
        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'delete-text-btn';
        deleteBtn.innerHTML = '×';
        deleteBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('album.text_block_delete') : 'Delete text block');
        deleteBtn.onclick = (e) => { e.stopPropagation(); deleteTextBlock(elem); };
        deleteBtn.addEventListener('touchstart', (e) => e.stopPropagation(), { passive: false });
        // touchend direkt auswerten: Android WebView unterdrückt den synthetischen
        // Click nach touchstart.stopPropagation() — touchend umgeht das Problem.
        deleteBtn.addEventListener('touchend', (e) => {
            e.stopImmediatePropagation();
            e.preventDefault();
            deleteTextBlock(elem);
        }, { passive: false });
        elem.appendChild(deleteBtn);
    }

    if (!elem.querySelector('.style-select-btn')) {
        const styleBtn = document.createElement('button');
        styleBtn.className = 'style-select-btn';
        styleBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('album.text_block_style') : 'Choose style');
        styleBtn.innerHTML = styleEmoji(elem.dataset.style || 'default');
        styleBtn.onclick = (e) => {
            e.stopPropagation();
            openStyleDropdown(elem, styleBtn);
        };
        styleBtn.addEventListener('touchstart', (e) => e.stopPropagation(), { passive: false });
        elem.appendChild(styleBtn);
    }

    if (!elem.querySelector('.locked-toggle-btn')) {
        const id = elem.dataset.id;
        const elemData = albumData?.elements.find(e => e.id === id);
        const isLocked = elemData?.locked === true;
        const lockedBtn = document.createElement('button');
        lockedBtn.className = 'locked-toggle-btn';
        lockedBtn.title = (window.MPD_I18N
            ? window.MPD_I18N.t(isLocked ? 'album.text_block_locked_private' : 'album.text_block_locked_public')
            : (isLocked ? 'Private' : 'Public'));
        lockedBtn.innerHTML = _lockIconSvg(isLocked);
        lockedBtn.onclick = (e) => { e.stopPropagation(); toggleLocked(elem, lockedBtn); };
        lockedBtn.addEventListener('touchstart', (e) => e.stopPropagation(), { passive: false });
        elem.appendChild(lockedBtn);
    }
}

function updatePlaceholder(elem, inner) {
    if (!inner) inner = elem.querySelector('.text-inner');
    if (!inner) return;
    elem.classList.toggle('is-empty', inner.textContent.trim() === '');
}

// ============================================
// Text toolbar: OK button + emoji picker
// ============================================

function showTextToolbar(elem) {
    if (elem.querySelector('.text-edit-toolbar')) return;

    const toolbar = document.createElement('div');
    toolbar.className = 'text-edit-toolbar';

    // Emoji button: desktop only. Phone/tablet use the system emoji keyboard.
    const isDesktop = window.matchMedia('(min-width: 1024px)').matches;
    if (isDesktop) {
        const emojiBtn = document.createElement('button');
        emojiBtn.className = 'text-toolbar-btn emoji-picker-btn';
        emojiBtn.innerHTML = '😀';
        emojiBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('album.text_block_emoji') : 'Insert emoji');
        emojiBtn.addEventListener('mousedown', (e) => e.preventDefault());
        emojiBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleEmojiPicker(elem, toolbar);
        });
        toolbar.appendChild(emojiBtn);
    }

    // Companion button (only if enabled)
    if (window.Companion?.isEnabled()) {
        const aiBtn = document.createElement('button');
        aiBtn.className = 'text-toolbar-btn companion-btn';
        aiBtn.innerHTML = '✨';
        aiBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('album.text_block_ai') : 'Formulierungshilfe');
        aiBtn.addEventListener('mousedown', (e) => e.preventDefault());
        aiBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const inner = elem.querySelector('.text-inner');
            window.Companion.openFor(inner, aiBtn);
        });
        toolbar.appendChild(aiBtn);
    }

    // OK button
    const okBtn = document.createElement('button');
    okBtn.className = 'text-toolbar-btn text-ok-btn';
    okBtn.textContent = (window.MPD_I18N ? window.MPD_I18N.t('common.ok') : 'OK') || 'OK';
    okBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('album.text_block_ok') : 'Finish editing');
    okBtn.addEventListener('mousedown', (e) => e.preventDefault());
    okBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const inner = elem.querySelector('.text-inner');
        const scrollPos = window.scrollY;
        if (inner) inner.blur();
        requestAnimationFrame(() => window.scrollTo(0, scrollPos));
        elem.classList.remove('editing');
        hideTextToolbar(elem);
        if ((inner?.textContent || '').trim() === '') {
            // Empty block → delete without confirm (user intentionally cleared it)
            const id = elem.dataset.id;
            const idx = albumData?.elements.findIndex(el => el.id === id);
            if (idx !== -1) {
                pushUndoState();
                albumData.elements.splice(idx, 1);
                hasUnsavedChanges = true;
                elem.remove();
                scheduleAutoSave();
            }
            return;
        }
        handleTextChange(elem, inner?.textContent || '');
        updatePlaceholder(elem, inner);
    });
    toolbar.appendChild(okBtn);

    elem.appendChild(toolbar);
}

function hideTextToolbar(elem) {
    elem.querySelector('.text-edit-toolbar')?.remove();
}

function toggleEmojiPicker(elem, toolbar) {
    let picker = toolbar.querySelector('.emoji-picker-popup');
    if (picker) { picker.remove(); return; }

    picker = document.createElement('div');
    picker.className = 'emoji-picker-popup';

    EMOJI_SET.forEach(emoji => {
        const btn = document.createElement('button');
        btn.className = 'emoji-pick';
        btn.textContent = emoji;
        btn.addEventListener('mousedown', (e) => e.preventDefault());
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            insertEmojiAtCursor(elem, emoji);
            picker.remove();
        });
        picker.appendChild(btn);
    });

    toolbar.appendChild(picker);
}

function insertEmojiAtCursor(elem, emoji) {
    const inner = elem.querySelector('.text-inner');
    if (!inner) return;
    inner.focus();

    const sel = window.getSelection();
    if (sel.rangeCount) {
        const range = sel.getRangeAt(0);
        range.deleteContents();
        range.insertNode(document.createTextNode(emoji));
        range.collapse(false);
        sel.removeAllRanges();
        sel.addRange(range);
    } else {
        inner.textContent += emoji;
    }

    hasUnsavedChanges = true;
    updateSaveIndicator('unsaved');
}

function makeTextReadonly(elem) {
    const inner = elem.querySelector('.text-inner');
    if (inner) {
        inner.contentEditable = false;
        inner.classList.remove('editable-inner');
    }
    elem.classList.remove('editable', 'editing');
    delete elem.dataset.editListenersAttached;
    elem.querySelector('.text-edit-toolbar')?.remove();
    elem.querySelector('.delete-text-btn')?.remove();
    elem.querySelector('.style-select-btn')?.remove();
    elem.querySelector('.style-dropdown')?.remove();
    elem.querySelector('.locked-toggle-btn')?.remove();
}

function handleTextChange(elem, newText) {
    const id = elem.dataset.id;
    const idx = albumData?.elements.findIndex(e => e.id === id);
    if (idx !== -1) {
        albumData.elements[idx].text = newText.trim();
        hasUnsavedChanges = true;
        scheduleAutoSave();
    }
}

// ============================================
// Text-style selector
// ============================================

function styleEmoji(style) {
    return { default: '📝', info: 'ℹ️', quote: '💬', note: '📌', heading: '🔠' }[style] || '📝';
}

function styleLabel(style) {
    const fb = { default: 'Standard', info: 'Info', quote: 'Zitat', note: 'Notiz', heading: 'Überschrift' }[style] || 'Standard';
    if (!window.MPD_I18N) return fb;
    const key = 'album.style_' + (style || 'default');
    const v = window.MPD_I18N.t(key);
    return (v === key) ? fb : v;
}

function openStyleDropdown(elem, btn) {
    // Close existing dropdowns
    document.querySelectorAll('.style-dropdown').forEach(d => d.remove());

    const styles = ['default', 'heading', 'info', 'quote', 'note'];
    const current = elem.dataset.style || 'default';

    const dropdown = document.createElement('div');
    dropdown.className = 'style-dropdown';

    styles.forEach(s => {
        const option = document.createElement('button');
        option.className = 'style-option' + (s === current ? ' active' : '');
        option.innerHTML = `${styleEmoji(s)} ${styleLabel(s)}`;
        option.onclick = (e) => {
            e.stopPropagation();
            handleStyleChange(elem, btn, s);
            dropdown.remove();
        };
        dropdown.appendChild(option);
    });

    btn.appendChild(dropdown);

    // Click outside closes dropdown
    setTimeout(() => {
        document.addEventListener('click', () => dropdown.remove(), { once: true });
    }, 0);
}

function handleStyleChange(elem, btn, newStyle) {
    pushUndoState();
    // Update CSS classes (only visual styles, not 'hidden')
    ['default', 'heading', 'info', 'quote', 'note'].forEach(s => {
        elem.classList.remove(`text-style-${s}`);
    });
    if (newStyle !== 'default') {
        elem.classList.add(`text-style-${newStyle}`);
    }

    // Update data-style and button icon
    elem.dataset.style = newStyle;
    btn.innerHTML = styleEmoji(newStyle);
    btn.appendChild(btn.querySelector('.style-dropdown') || document.createElement('span'));

    // Update albumData
    const id = elem.dataset.id;
    const idx = albumData?.elements.findIndex(e => e.id === id);
    if (idx !== -1) {
        if (newStyle === 'default') {
            delete albumData.elements[idx].style;
        } else {
            albumData.elements[idx].style = newStyle;
        }
        hasUnsavedChanges = true;
        scheduleAutoSave();
    }
}

function toggleLocked(elem, btn) {
    const id = elem.dataset.id;
    const idx = albumData?.elements.findIndex(e => e.id === id);
    if (idx === -1) return;

    pushUndoState();
    const elemData = albumData.elements[idx];
    const nowLocked = elemData.locked !== true;

    if (nowLocked) elemData.locked = true;
    else           delete elemData.locked;

    hasUnsavedChanges = true;
    scheduleAutoSave();

    // Re-render so the effect is immediately visible:
    //  - show_locked=false: the element disappears
    //  - show_locked=true : outline/dim are set, the toggle button icon
    //    reflects the new state (the old btn/elem is then stale).
    const scrollPos = window.scrollY;
    if (isEditMode) enableEditMode();
    else            renderAlbum();
    requestAnimationFrame(() => window.scrollTo(0, scrollPos));
}

function deleteTextBlock(elem) {
    const id = elem.dataset.id;
    const idx = albumData?.elements.findIndex(e => e.id === id);
    if (idx === -1) return;

    // Kein Bestätigungs-Dialog — konsistent mit removePhotoFromAlbum().
    // Text ist per Undo wiederherstellbar; Toast zeigt die Option an.
    pushUndoState();
    albumData.elements.splice(idx, 1);
    hasUnsavedChanges = true;

    if (typeof window.mpdToast === 'function') {
        const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
        window.mpdToast(_t('album.toast_text_removed', 'Textfeld entfernt'), {
            action  : _t('album.toast_undo', 'Undo'),
            onAction: () => { if (typeof undo === 'function') undo(); },
            duration: 4000,
        });
    }

    elem.style.transition = 'opacity 0.2s, transform 0.2s';
    elem.style.opacity    = '0';
    elem.style.transform  = 'scale(0.95)';
    setTimeout(() => { elem.remove(); scheduleAutoSave(); }, 200);
}

// ============================================
// Separator-Stil wählen
// ============================================

function openSepStyleDropdown(elemData, btn, sepEl) {
    document.querySelectorAll('.sep-style-dropdown').forEach(d => d.remove());

    const styles = [
        { key: 'line',   label: 'Linie' },
        { key: 'bold',   label: 'Fett' },
        { key: 'dashed', label: 'Gestrichelt' },
        { key: 'dots',   label: 'Punkte' },
        { key: 'space',  label: 'Abstand' },
    ];

    const dropdown = document.createElement('div');
    dropdown.className = 'sep-style-dropdown style-dropdown';

    const current = elemData.style || 'line';
    styles.forEach(s => {
        const opt = document.createElement('button');
        opt.className = 'style-option' + (s.key === current ? ' active' : '');
        opt.innerHTML = `${_sepStyleChar(s.key)} ${s.label}`;
        opt.onclick = (e) => {
            e.stopPropagation();
            changeSepStyle(elemData, sepEl, btn, s.key);
            dropdown.remove();
        };
        dropdown.appendChild(opt);
    });

    btn.appendChild(dropdown);
    setTimeout(() => {
        document.addEventListener('click', () => dropdown.remove(), { once: true });
    }, 0);
}

function changeSepStyle(elemData, sepEl, btn, newStyle) {
    pushUndoState();

    ['line', 'bold', 'dashed', 'dots', 'space'].forEach(s => sepEl.classList.remove(`sep-style-${s}`));
    if (newStyle !== 'line') sepEl.classList.add(`sep-style-${newStyle}`);

    const idx = albumData?.elements.findIndex(e => e.id === elemData.id);
    if (idx !== -1) {
        if (newStyle === 'line') delete albumData.elements[idx].style;
        else albumData.elements[idx].style = newStyle;
        elemData.style = albumData.elements[idx].style;
        hasUnsavedChanges = true;
        scheduleAutoSave();
    }

    btn.textContent = _sepStyleChar(newStyle);
}
