/* MPD – Auto-Save & Save
 * scheduleAutoSave, showTestOrderInspector, saveAlbumData, updateSaveIndicator
 */

// ============================================
// TEST MODE — for drag & drop verification
// TEST_MODE = true: no auto-save, manual save
// button, visual order display after each drop.
// Before production use: set TEST_MODE = false.
// ============================================
// TEST_MODE is declared in album-state.js as var TEST_MODE = false.

function scheduleAutoSave() {
    if (TEST_MODE) {
        // No auto-save — user saves manually
        showTestOrderInspector();
        return;
    }
    clearTimeout(window.autoSaveTimer);
    window.autoSaveTimer = setTimeout(saveAlbumData, 2000);
}

// Shows the current element order after each drop
// so we can verify everything is correct BEFORE saving
function showTestOrderInspector() {
    let inspector = document.getElementById('test-order-inspector');
    if (!inspector) {
        inspector = document.createElement('div');
        inspector.id = 'test-order-inspector';
        inspector.style.cssText = `
            position: fixed; bottom: 180px; left: 16px; z-index: 9999;
            background: rgba(0,0,0,0.88); color: #0f0; font-family: monospace;
            font-size: 11px; padding: 10px 14px; border-radius: 8px;
            max-height: 300px; overflow-y: auto; max-width: 320px;
            border: 1px solid #0f0; box-shadow: 0 4px 16px rgba(0,255,0,0.2);
        `;
        document.body.appendChild(inspector);
    }

    const lines = albumData.elements.map((e, i) => {
        const label = e.type === 'text'
            ? `📝 ${(e.text || '').substring(0, 28).replace(/\n/g, '↵') || '(leer)'}`
            : `🖼 ${e.file || e.id}`;
        return `${String(i+1).padStart(2,'0')} ${label}`;
    });

    inspector.innerHTML =
        `<div style="color:#ff0;margin-bottom:6px;font-weight:bold;">
            ⚠️ TEST-MODUS — kein Auto-Save<br>
            <span style="color:#aaa;font-weight:normal;">Reihenfolge nach Drop:</span>
         </div>` +
        lines.map(l => `<div>${l}</div>`).join('') +
        `<div style="margin-top:8px;border-top:1px solid #333;padding-top:6px;">
            <button onclick="saveAlbumData()" style="
                background:#0a0;color:white;border:none;padding:6px 14px;
                border-radius:4px;cursor:pointer;font-size:12px;width:100%;">
                ${(window.MPD_I18N ? window.MPD_I18N.t('album.save_now_btn') : '💾 Save now')}
            </button>
            <button onclick="document.getElementById('test-order-inspector').remove()" style="
                background:#333;color:#aaa;border:none;padding:4px 14px;
                border-radius:4px;cursor:pointer;font-size:11px;width:100%;margin-top:4px;">
                ${(window.MPD_I18N ? window.MPD_I18N.t('album.save_panic_close') : '✕ Close (do not save)')}
            </button>
         </div>`;
}

async function saveAlbumData() {
    if (!albumData || isSaving) return;

    // PDX v1.4: protection against accidental emptying.
    // When the album transitions from "had content" to "is now empty",
    // we show a one-time confirm. Freshly created empty albums (lastSaved=0)
    // and all saves with content go through without confirmation.
    const currentCount = (albumData.elements || []).length;
    if (currentCount === 0 && lastSavedElementCount > 0) {
        const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
        const ok = await mpdConfirm({
            icon: '<svg viewBox="0 0 24 24" fill="none" stroke="#e0a030" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
            title: _t('album.save_empty_title', 'Album wirklich leer speichern?'),
            body: _t('album.save_empty_body', 'Du hast alle Elemente entfernt. Beim Speichern wird das Album-Drehbuch leer — die Photos selbst bleiben unangetastet.'),
            confirmLabel: _t('album.save_empty_confirm_label', 'Leer speichern'),
            destructive: true,
        });
        if (!ok) {
            updateSaveIndicator('unsaved');
            return;
        }
    }

    isSaving = true;
    updateSaveIndicator('saving');

    const toggle = document.getElementById('edit-mode-toggle');
    if (toggle) toggle.disabled = true;

    try {
        const response = await fetch(
            `${API_BASE}/api/album/${encodeURIComponent(albumSpace)}/${encodeURIComponent(albumName)}/update`,
            {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(albumData)
            }
        );

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }

        hasUnsavedChanges = false;
        lastSavedElementCount = currentCount;
        updateSaveIndicator('saved');
        setTimeout(() => updateSaveIndicator('idle'), 2000);

    } catch (error) {
        console.error('Save error:', error);
        updateSaveIndicator('error');
        const _retryMsg = (window.MPD_I18N
            ? window.MPD_I18N.t('album.save_error_retry', { msg: error.message })
            : `Fehler beim Speichern:\n${error.message}\n\nErneut versuchen?`);
        // Kein natives confirm(): im Android-WebView-Wrapper liefert es
        // still `false`, der Nutzer bekaeme dann gar keine Rueckfrage und
        // der Speicherfehler bliebe unbemerkt. mpdConfirm liefert ein
        // Promise, deshalb ist die Funktion ohnehin schon async.
        // t() liefert bei unbekanntem Schluessel den Schluessel selbst zurueck,
        // ein `|| 'Text'`-Rueckfall greift also nie. Deshalb hier ein eigener
        // Helfer, der nur bei fehlendem i18n auf den deutschen Text faellt.
        const _t = (k, fb) => {
            if (!window.MPD_I18N) return fb;
            const v = window.MPD_I18N.t(k);
            return (v && v !== k) ? v : fb;
        };
        const _retry = window.mpdConfirm
            ? await window.mpdConfirm({
                title:        _t('album.save_error_title', 'Fehler beim Speichern'),
                body:         error.message,
                hint:         _t('album.save_error_hint', 'Erneut versuchen?'),
                hintType:     'warn',
                confirmLabel: _t('common.retry', 'Erneut versuchen'),
              })
            : false;
        if (_retry) {
            isSaving = false;
            saveAlbumData();
            return;
        }
    } finally {
        isSaving = false;
        if (toggle) toggle.disabled = false;
    }
}

function updateSaveIndicator(state) {
    const status = document.getElementById('edit-save-status');
    if (!status) return;
    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    const text = {
        saving:  _t('album.save_status_saving',  'Speichert…'),
        saved:   _t('album.save_status_saved',   'Gespeichert'),
        unsaved: _t('album.save_status_unsaved', 'Ungespeichert'),
        error:   _t('album.save_status_error',   'Fehler'),
        idle:    ''
    };
    // Line-art SVG icons — same vocabulary as the rest of the album header.
    // 'saving' uses a loader arc (CSS-spun); the others are static glyphs.
    const ICON = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    const icons = {
        saving:  `<svg class="save-icon save-icon-spin" ${ICON}><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>`,
        saved:   `<svg class="save-icon" ${ICON}><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
        unsaved: `<svg class="save-icon" ${ICON}><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
        error:   `<svg class="save-icon" ${ICON}><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
    };
    const t = text[state];
    if (!t) { status.innerHTML = ''; return; }
    status.innerHTML = `${icons[state] || ''}<span>${t}</span>`;
}
