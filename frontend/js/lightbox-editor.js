/* ═══════════════════════════════════════════
 *  Lightbox — photo editor module
 *  Extends Lightbox.prototype
 *  Dependency: lightbox-core.js must be loaded first
 *  ═══════════════════════════════════════════ */

/* PDX v1.4: helpers for source_album photos. Edits/renames/deletes of a
 * referenced photo affect the original in the source album — for that
 * we show a confirmation dialog once per session. */
Lightbox.prototype._resolveAlbum = function(img) {
    return (img && img.source_album) || this.albumName;
};
Lightbox.prototype._confirmSourceEdit = async function(img) {
    if (!img || !img.source_album) return true;
    if (this._sourceEditAcked) return true;
    if (typeof window.mpdConfirm !== 'function') return true;  // defensive
    const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
    const ok = await window.mpdConfirm({
        icon        : '⚠️',
        title       : _t('lightbox.confirm_source_title', null, 'Originalfoto im Quell-Album bearbeiten?'),
        body        : _t('lightbox.confirm_source_body', { album: img.source_album },
                         `This photo lives in album „${album}". Changes (EXIF, pixels, rename) affect the original file there — not only this curated album.`),
        hint        : _t('lightbox.confirm_source_hint', null, 'This confirmation applies for the current session.'),
        hintType    : 'info',
        confirmLabel: _t('lightbox.confirm_source_ok', null, 'Verstanden, bearbeiten'),
    });
    if (ok) this._sourceEditAcked = true;
    return ok;
};

    /* ══════════════════════════════════════════
     *  Photo editor
     * ══════════════════════════════════════════ */

Lightbox.prototype.openEditor = async function() {
        const img = this.allImages[this.currentIndex];
        if (!img) return;
        if (!await this._confirmSourceEdit(img)) return;

        // Reset state
        const e = this._edit;
        e.rotation = 0; e.flip = false; e.flipV = false; e.straighten = 0;
        e.brightness = 0; e.contrast = 0; e.saturation = 0;
        e.straightenOpen = false;
        e.crop = null; e.cropActive = false;

        // Set editor image
        document.getElementById('lb-editor-img').src =
            `${window.location.origin}${img.thumbnails.xl}`;

        // Reset sliders
        ['brightness','contrast','saturation'].forEach(k => {
            const sl = document.getElementById('lb-sl-' + k);
            const sv = document.getElementById('lb-sv-' + k);
            if (sl) sl.value = 0;
            if (sv) sv.textContent = '0';
        });
        const stSlider = document.getElementById('lb-straighten-slider');
        if (stSlider) stSlider.value = 0;
        const stVal = document.getElementById('lb-straighten-val');
        if (stVal) stVal.textContent = '0°';

        // Close straighten panel + grid
        document.getElementById('lb-editor-straighten')?.classList.remove('visible');
        document.getElementById('lb-editor-grid')?.classList.remove('visible');
        document.getElementById('lb-ctx-straighten')?.classList.remove('active');
        document.getElementById('lb-ctx-flip-h')?.classList.remove('active');
        document.getElementById('lb-ctx-flip-v')?.classList.remove('active');

        // Clear status + reset BOTH save buttons (mobile + desktop)
        document.getElementById('lb-editor-status').textContent = '';
        document.querySelectorAll('#lb-editor-save-btn, #lb-editor-save-btn-d')
            .forEach(b => b.disabled = false);
        document.getElementById('lb-editor-title').textContent = img.file;
        const desktopTitle = document.getElementById('lb-editor-desktop-title');
        if (desktopTitle) desktopTitle.textContent = img.file;
        this._editHistory = [];
        this._editFuture  = [];
        this._editorUpdateHistory();

        // Slider-Events einmalig verdrahten
        if (!this._editListenersAttached) {
            this._attachEditorListeners();
            this._editListenersAttached = true;
        }

        this._editorApplyPreview();
        document.getElementById('lb-editor').classList.add('active');
};

Lightbox.prototype.closeEditor = function() {
        document.getElementById('lb-editor').classList.remove('active');
        const edImg = document.getElementById('lb-editor-img');
        edImg.style.transform = '';
        edImg.style.filter = '';
        clearTimeout(this._gridTimer);
};

Lightbox.prototype._attachEditorListeners = function() {
        [
            { id: 'lb-sl-brightness', val: 'lb-sv-brightness', key: 'brightness' },
            { id: 'lb-sl-contrast',   val: 'lb-sv-contrast',   key: 'contrast'   },
            { id: 'lb-sl-saturation', val: 'lb-sv-saturation', key: 'saturation' },
        ].forEach(({ id, val, key }) => {
            const sl = document.getElementById(id);
            const sv = document.getElementById(val);
            if (!sl) return;
            sl.addEventListener('pointerdown', () => { this._editorPushHistory(); });
            sl.addEventListener('input', () => {
                this._edit[key] = parseInt(sl.value, 10);
                sv.textContent = sl.value;
                this._editorApplyPreview();
            });
        });

        const stSlider = document.getElementById('lb-straighten-slider');
        const stVal    = document.getElementById('lb-straighten-val');
        if (stSlider) {
            stSlider.addEventListener('pointerdown', () => { this._editorPushHistory(); });
            stSlider.addEventListener('input', () => {
                this._edit.straighten = parseFloat(stSlider.value);
                stVal.textContent = stSlider.value + '°';
                this._editorApplyPreview();
            });
        }
};

Lightbox.prototype._editorPushHistory = function() {
        // Push current state as snapshot into history
        this._editHistory.push({...this._edit});
        this._editFuture = [];   // clear redo
        this._editorUpdateHistory();
};

Lightbox.prototype._editorUpdateHistory = function() {
        const canUndo = this._editHistory.length > 0;
        const canRedo = this._editFuture.length  > 0;
        ['lb-undo-btn','lb-undo-btn-d'].forEach(id => {
            const b = document.getElementById(id);
            if (b) b.disabled = !canUndo;
        });
        ['lb-redo-btn','lb-redo-btn-d'].forEach(id => {
            const b = document.getElementById(id);
            if (b) b.disabled = !canRedo;
        });
};

Lightbox.prototype.editorUndo = function() {
        if (!this._editHistory.length) return;
        this._editFuture.push({...this._edit});
        const prev = this._editHistory.pop();
        Object.assign(this._edit, prev);
        this._editorSyncControls();
        this._editorApplyPreview();
        this._editorUpdateHistory();
};

Lightbox.prototype.editorRedo = function() {
        if (!this._editFuture.length) return;
        this._editHistory.push({...this._edit});
        const next = this._editFuture.pop();
        Object.assign(this._edit, next);
        this._editorSyncControls();
        this._editorApplyPreview();
        this._editorUpdateHistory();
};

Lightbox.prototype._editorSyncControls = function() {
        // Sync slider values after undo/redo
        const e = this._edit;
        ['brightness','contrast','saturation'].forEach(k => {
            const sl = document.getElementById('lb-sl-' + k);
            const sv = document.getElementById('lb-sv-' + k);
            if (sl) sl.value = e[k];
            if (sv) sv.textContent = e[k];
        });
        const stSlider = document.getElementById('lb-straighten-slider');
        if (stSlider) stSlider.value = e.straighten;
        const stVal = document.getElementById('lb-straighten-val');
        if (stVal) stVal.textContent = e.straighten + '°';
        document.getElementById('lb-ctx-flip-h')?.classList.toggle('active', e.flip);
        document.getElementById('lb-ctx-flip-v')?.classList.toggle('active', e.flipV);
        const stOpen = e.straightenOpen;
        document.getElementById('lb-editor-straighten')?.classList.toggle('visible', stOpen);
        document.getElementById('lb-editor-grid')?.classList.toggle('visible', stOpen);
        document.getElementById('lb-ctx-straighten')?.classList.toggle('active', stOpen);
};

Lightbox.prototype._editorApplyPreview = function() {
        const e   = this._edit;
        const img = document.getElementById('lb-editor-img');
        if (!img) return;

        const totalRot = e.rotation + e.straighten;
        let transform  = `rotate(${totalRot}deg)`;
        if (e.flip  && !e.flipV) transform += ' scaleX(-1)';
        if (e.flipV && !e.flip)  transform += ' scaleY(-1)';
        if (e.flip  &&  e.flipV) transform += ' scaleX(-1) scaleY(-1)';
        img.style.transform = transform;

        const brightness = 1 + e.brightness / 100;
        const contrast   = 1 + e.contrast   / 100;
        const saturation = 1 + e.saturation  / 100;
        img.style.filter = `brightness(${brightness}) contrast(${contrast}) saturate(${saturation})`;

        // In crop mode the overlay must follow rotation/straighten changes
        if (e.cropActive) this._repositionCropOverlay();
};

Lightbox.prototype._repositionCropOverlay = function(show) {
        const imgEl   = document.getElementById('lb-editor-img');
        const overlay = document.getElementById('lb-editor-crop-overlay');
        if (!imgEl || !overlay) return;
        if (!show && overlay.style.display === 'none') return;

        const areaEl   = overlay.parentElement;
        const imgRect  = imgEl.getBoundingClientRect();
        const areaRect = areaEl.getBoundingClientRect();

        // Fallback if imgRect is empty (image not measured yet) — uses the
        // image area minus the crop padding as the visible region.
        if (imgRect.width < 10 || imgRect.height < 10) {
            overlay.style.top    = '40px';
            overlay.style.left   = '40px';
            overlay.style.width  = (areaRect.width  - 80) + 'px';
            overlay.style.height = (areaRect.height - 112) + 'px';
        } else {
            overlay.style.top    = (imgRect.top  - areaRect.top)  + 'px';
            overlay.style.left   = (imgRect.left - areaRect.left) + 'px';
            overlay.style.width  = imgRect.width  + 'px';
            overlay.style.height = imgRect.height + 'px';
        }
        if (show) overlay.style.display = 'block';
        this._positionGrid();   // Bildrechteck hat sich geaendert
};

Lightbox.prototype.editorRotate = function(degrees) {
        this._editorPushHistory();
        this._edit.rotation = (this._edit.rotation + degrees) % 360;
        const grid = document.getElementById('lb-editor-grid');
        if (grid && !this._edit.straightenOpen) {
            this._positionGrid();
            grid.classList.add('visible');
            clearTimeout(this._gridTimer);
            this._gridTimer = setTimeout(() => {
                if (!this._edit.straightenOpen) grid.classList.remove('visible');
            }, 2000);
        }
        this._editorApplyPreview();
};

Lightbox.prototype.editorFlip = function(dir = 'h') {
        this._editorPushHistory();
        if (dir === 'v') {
            this._edit.flipV = !this._edit.flipV;
            document.getElementById('lb-ctx-flip-v')?.classList.toggle('active', this._edit.flipV);
        } else {
            this._edit.flip = !this._edit.flip;
            document.getElementById('lb-ctx-flip-h')?.classList.toggle('active', this._edit.flip);
        }
        this._editorApplyPreview();
};

Lightbox.prototype.editorToggleStraighten = function() {
        this._edit.straightenOpen = !this._edit.straightenOpen;
        const open = this._edit.straightenOpen;
        document.getElementById('lb-editor-straighten').classList.toggle('visible', open);
        if (open) this._positionGrid();
        document.getElementById('lb-editor-grid').classList.toggle('visible', open);
        document.getElementById('lb-ctx-straighten').classList.toggle('active', open);
};

/* Nach dem Speichern zeigen die Kacheln im Album weiter den alten Stand,
 * bis jemand F5 drueckt (13.09.2026). Das Lightbox-Bild wurde schon
 * immer neu geholt, die Thumbnails dahinter nicht.
 *
 * Der Server ist darauf vorbereitet: /api/thumbnail geht mit
 * `Cache-Control: no-cache` und einem ETag aus sha1(Pfad+mtime) raus. Eine
 * Bearbeitung aendert die mtime, also den ETag — der Browser bekaeme beim
 * naechsten Anfragen das neue Bild. Er fragt nur nicht von selbst, solange
 * das <img> unveraendert im DOM steht. Genau diesen Stups gibt die Funktion.
 *
 * Sie greift ueber alle Ansichten, die Thumbnails zeigen: Album, Durchlauf,
 * Zeitstrahl. Nicht geladene Lazy-Kacheln brauchen nichts — sie holen beim
 * Sichtbarwerden ohnehin frisch und revalidieren dabei. */
Lightbox.prototype._refreshThumbsFor = function(file) {
        if (!file) return;
        const needle = encodeURIComponent(file);
        const stamp  = Date.now();
        document.querySelectorAll('img[src*="/api/thumbnail/"]').forEach(el => {
            if (!el.src.includes(needle)) return;
            const base = el.src.replace(/([?&])t=\d+/, '$1').replace(/[?&]$/, '');
            el.src = base + (base.includes('?') ? '&' : '?') + 't=' + stamp;
        });
};

Lightbox.prototype.saveEdit = async function() {
        const img = this.allImages[this.currentIndex];
        if (!img) return;

        const e = this._edit;
        const noOp = e.rotation === 0 && !e.flip && !e.flipV && e.straighten === 0 &&
                     e.brightness === 0 && e.contrast === 0 && e.saturation === 0 &&
                     !e.crop;
        const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
        const saveBtn  = document.getElementById('lb-editor-save-btn');
        const saveBtnD = document.getElementById('lb-editor-save-btn-d');
        const statusEl = document.getElementById('lb-editor-status');

        // Ist nichts zu speichern, schloss sich der Editor frueher
        // kommentarlos. Von aussen sah das aus, als haette das Speichern
        // versagt — besonders wenn der Zuschnitt nicht uebernommen wurde
        // (dann bleibt e.crop null) oder der Rahmen praktisch das ganze Bild
        // umfasst. Jetzt sagt die Statuszeile kurz Bescheid, danach schliesst
        // er wie im Erfolgsfall.
        if (noOp) {
            if (statusEl) {
                statusEl.style.color = 'rgba(255,255,255,0.6)';
                statusEl.textContent = _t('lightbox.editor_status_nochange', null,
                                          'Keine Änderungen zu speichern');
            }
            setTimeout(() => this.closeEditor(), 1200);
            return;
        }
        saveBtn.disabled = true;
        if (saveBtnD) saveBtnD.disabled = true;
        statusEl.style.color = 'rgba(255,255,255,0.5)';
        statusEl.textContent = _t('lightbox.editor_status_saving', 'Speichern…');

        const body = {
            rotation:   ((e.rotation % 360) + 360) % 360,
            flip:       e.flip,
            flip_v:     e.flipV,
            straighten: e.straighten,
            brightness: e.brightness,
            contrast:   e.contrast,
            saturation: e.saturation,
        };
        if (e.crop) body.crop = e.crop;

        try {
            const url = `${window.location.origin}/api/photo/` +
                `${encodeURIComponent(this.albumSpace)}/` +
                `${encodeURIComponent(this._resolveAlbum(img))}/` +
                `${encodeURIComponent(img.file)}/edit`;

            const resp = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || resp.statusText);

            statusEl.style.color = '#4caf50';
            statusEl.textContent = _t('lightbox.editor_status_saved', '✓ Gespeichert') +
                (data.backup ? _t('lightbox.editor_status_backup_postfix', { name: data.backup }, ' · Backup: ' + data.backup) : '');

            // Lightbox-Bild cache-bust
            const lbImg = document.getElementById('lb-img');
            lbImg.src   = lbImg.src.split('?')[0] + '?t=' + Date.now();
            this._refreshThumbsFor(img.file);

            setTimeout(() => this.closeEditor(), 900);

        } catch(err) {
            statusEl.style.color = '#ff6b6b';
            statusEl.textContent = _t('lightbox.editor_status_error_prefix', { msg: err.message }, '⚠️ ' + err.message);
        } finally {
            // Always re-enable (even on success — closeEditor only fires 900ms later,
            // and the next openEditor session should start on clean state)
            saveBtn.disabled = false;
            if (saveBtnD) saveBtnD.disabled = false;
        }
};

Lightbox.prototype.resetToOriginal = async function() {
        const img = this.allImages[this.currentIndex];
        if (!img) return;

        const _resetMsg = (window.MPD_I18N ? window.MPD_I18N.t('lightbox.confirm_reset_msg') : 'Alle Bearbeitungen verwerfen und das Original wiederherstellen?');
        const _resetEn  = window.MPD_I18N && window.MPD_I18N.getLang && window.MPD_I18N.getLang() === 'en';
        // Mobil-sicher: natives confirm() bricht in Android-WebView still ab.
        const _okReset = (typeof _lbConfirm === 'function')
            ? await _lbConfirm({ title: _resetMsg, subtitle: img.file, destructive: true,
                                 confirmLabel: _resetEn ? 'Reset' : 'Zurücksetzen' })
            : confirm(_resetMsg + '\n\n' + img.file);
        if (!_okReset) return;

        const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
        const statusEl = document.getElementById('lb-editor-status');
        statusEl.style.color = 'rgba(255,255,255,0.5)';
        statusEl.textContent = _t('lightbox.editor_status_resetting', 'Resetting…');

        try {
            const url = `${window.location.origin}/api/photo/` +
                `${encodeURIComponent(this.albumSpace)}/` +
                `${encodeURIComponent(this._resolveAlbum(img))}/` +
                `${encodeURIComponent(img.file)}/edit`;

            const resp = await fetch(url, { method: 'DELETE' });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || resp.statusText);

            statusEl.style.color = '#4caf50';
            statusEl.textContent = '✓ Original wiederhergestellt';

            const lbImg = document.getElementById('lb-img');
            lbImg.src   = lbImg.src.split('?')[0] + '?t=' + Date.now();
            this._refreshThumbsFor(img.file);

            setTimeout(() => this.closeEditor(), 900);
        } catch(err) {
            statusEl.style.color = '#ff6b6b';
            statusEl.textContent = '⚠️ ' + err.message;
        }
};

/* ══════════════════════════════════════════
 *  Crop UI (overlay with drag handles)
 * ══════════════════════════════════════════ */

Lightbox.prototype.openCropMode = function() {
        if (this._edit.cropActive) return;
        this._edit.cropActive = true;

        // Snapshot of current geometry state, for cancel-restore
        this._cropSnapshot = {
            rotation:   this._edit.rotation,
            straighten: this._edit.straighten,
            crop:       this._edit.crop ? { ...this._edit.crop } : null,
        };

        this._cropRect = this._edit.crop
            ? { ...this._edit.crop }
            : { x: 0, y: 0, w: 1, h: 1 };   // starts at the image edge, user crops inward

        // Put editor into crop mode: hides bottom panel, shows crop footer
        document.getElementById('lb-editor').classList.add('crop-mode');
        this._syncCropFooterHeight();

        // Overlay exactly over the visible image. Double requestAnimationFrame
        // so that layout after class change (padding, sidebar-hide) is stable.
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                this._repositionCropOverlay(true);
                this._cropRender();
                this._cropAttachListeners();
            });
        });

        // Wire straighten slider in crop footer (unless already wired)
        this._attachCropStraightenSlider();
        this._syncCropStraightenSlider();
};

/* Der Bildbereich muss unten so viel Platz lassen, wie der Crop-Footer
 * tatsaechlich braucht — nicht die 72 px, die frueher fest im CSS standen.
 * Im WebView kommt zur 50 px hohen Neige-Skala die Systemleiste dazu
 * (--inset-bottom), und dann schob sich der Footer ueber die unteren
 * Anfasspunkte: Der Rahmen liess sich nach unten nicht mehr ziehen
 * (13.09.2026). Gemessen statt geraten, und bei jeder
 * Groessenaenderung neu. */
Lightbox.prototype._syncCropFooterHeight = function() {
        const editor = document.getElementById('lb-editor');
        const footer = document.getElementById('lb-editor-crop-footer');
        if (!editor || !footer) return;
        const h = Math.round(footer.getBoundingClientRect().height);
        if (h > 0) editor.style.setProperty('--crop-footer-h', h + 'px');
};

Lightbox.prototype.closeCropMode = function(commit) {
        if (!this._edit.cropActive) return;

        if (commit) {
            // Take over crop rect (full-image crop → treat as "no crop")
            const r = this._cropRect;
            if (r.x < 0.005 && r.y < 0.005 && r.w > 0.99 && r.h > 0.99) {
                this._edit.crop = null;
            } else {
                this._edit.crop = { ...r };
            }
            this._editorPushHistory();
        } else {
            // Cancel → write snapshot back (rotation/straighten/crop)
            const s = this._cropSnapshot;
            if (s) {
                this._edit.rotation   = s.rotation;
                this._edit.straighten = s.straighten;
                this._edit.crop       = s.crop;
            }
            this._editorSyncControls();
            this._syncCropStraightenSlider();
            this._editorApplyPreview();
        }

        this._edit.cropActive = false;
        this._cropSnapshot    = null;
        document.getElementById('lb-editor').classList.remove('crop-mode');
        document.getElementById('lb-editor-crop-overlay').style.display = 'none';
        this._cropDrag = null;
};

Lightbox.prototype._buildTiltScale = function(root) {
        if (root.dataset.tiltBuilt) return;
        root.dataset.tiltBuilt = '1';
        const RANGE = 45;
        const PX    = 8;
        const numbers = root.querySelector('.lb-tilt-scale-numbers');
        const ticks   = root.querySelector('.lb-tilt-scale-ticks');
        for (let v = -RANGE; v <= RANGE; v += 15) {
            const n = document.createElement('div');
            n.className = 'lb-tilt-scale-num';
            if (v === 0) n.classList.add('zero');
            if (Math.abs(v) >= RANGE - 5) n.classList.add('outer');
            n.textContent = (v > 0 ? '+' : '') + v + '°';
            n.style.left = ((v + RANGE) * PX) + 'px';
            numbers.appendChild(n);
        }
        for (let v = -RANGE; v <= RANGE; v += 1) {
            const t = document.createElement('div');
            t.className = 'lb-tilt-scale-tick';
            if (v === 0)             t.classList.add('zero');
            else if (v % 5 === 0)    t.classList.add('major');
            else                     t.classList.add('minor');
            t.style.left = ((v + RANGE) * PX) + 'px';
            ticks.appendChild(t);
        }
};

Lightbox.prototype._attachCropStraightenSlider = function() {
        if (this._cropStraightenWired) return;
        this._cropStraightenWired = true;
        const root  = document.getElementById('lb-tilt-scale-c');
        if (!root) return;
        this._buildTiltScale(root);

        const RANGE = 45;
        const PX    = 8;
        const track = root.querySelector('.lb-tilt-scale-track');

        let dragging = false;
        let startX   = 0;
        let startVal = 0;

        // Snap to 0.1° — otherwise you land on 12.478° and it's annoying.
        const setVal = (v) => {
            v = Math.max(-RANGE, Math.min(RANGE, v));
            v = Math.round(v * 10) / 10;
            this._edit.straighten = v;
            this._syncCropStraightenSlider();
            this._editorApplyPreview();
        };

        root.addEventListener('pointerdown', (e) => {
            dragging = true;
            startX   = e.clientX;
            startVal = this._edit.straighten;
            root.setPointerCapture(e.pointerId);
            track.classList.add('dragging');
            this._gridAlign(true);
            this._editorPushHistory();
        });
        root.addEventListener('pointermove', (e) => {
            if (!dragging) return;
            const dv = -(e.clientX - startX) / PX;
            setVal(startVal + dv);
        });
        const stopDrag = () => {
            dragging = false;
            track.classList.remove('dragging');
            this._gridAlign(false);
        };
        root.addEventListener('pointerup',     stopDrag);
        root.addEventListener('pointercancel', stopDrag);

        // Doppelklick / Doppeltap → 0°
        root.addEventListener('dblclick', () => setVal(0));

        // Wheel/Trackpad: horizontal-deltaX bevorzugt, sonst vertikal
        root.addEventListener('wheel', (e) => {
            e.preventDefault();
            const dv = e.deltaX !== 0 ? -e.deltaX / PX : e.deltaY / 30;
            if (!this._wheelHistoryPushed) {
                this._editorPushHistory();
                this._wheelHistoryPushed = true;
                clearTimeout(this._wheelHistoryTimer);
                this._wheelHistoryTimer = setTimeout(() => {
                    this._wheelHistoryPushed = false;
                }, 600);
            }
            setVal(this._edit.straighten + dv);
            this._gridAlign(true);
            clearTimeout(this._wheelGridTimer);
            this._wheelGridTimer = setTimeout(() => this._gridAlign(false), 500);
        }, { passive: false });
};

/* Gitter waehrend des Ausrichtens: einblenden und engmaschig schalten.
 * Zwei Gruende (13.09.2026): Im Zuschneiden-Modus sitzt die
 * Neigeskala im Footer, ohne dass `straightenOpen` gesetzt ist — das Gitter
 * kam dort gar nicht. Und 18 Felder sind zu weitmaschig, um einen Horizont
 * an eine Linie zu legen. Beim Loslassen bleibt es kurz stehen, damit man
 * das Ergebnis noch gegen die Linien pruefen kann. */
/* Das Gitter ueber das BILD legen, nicht ueber den Bereich.
 *
 * Es hing mit `inset: 0` am ganzen Bildbereich. Bei einem flachen Bild —
 * Panorama, Querformat auf einem breiten Schirm — fuellt das Foto nur einen
 * Streifen davon, und die Linien verteilen sich ueber die gesamte Flaeche:
 * im Motiv landen dann nur wenige. Deshalb war die Verdopplung der
 * Maschenweite am 13.09.2026 nicht wahrnehmbar. Auf dem Bild sind
 * es jetzt immer 36 Felder, unabhaengig vom Format.
 *
 * Gemessen wird mit offsetWidth/offsetHeight, also OHNE die Drehung. Das ist
 * Absicht: Das Gitter soll waagerecht stehenbleiben, waehrend sich das Bild
 * darunter neigt — sonst dreht sich die Referenz mit und taugt nicht zum
 * Ausrichten. */
Lightbox.prototype._positionGrid = function() {
        const imgEl = document.getElementById('lb-editor-img');
        const grid  = document.getElementById('lb-editor-grid');
        if (!imgEl || !grid) return;
        const w = imgEl.offsetWidth, h = imgEl.offsetHeight;
        if (w < 10 || h < 10) {           // noch nicht vermessen: ganzer Bereich
            grid.style.top = grid.style.left = '0';
            grid.style.right = grid.style.bottom = '0';
            grid.style.width = grid.style.height = '';
            return;
        }
        grid.style.top    = imgEl.offsetTop  + 'px';
        grid.style.left   = imgEl.offsetLeft + 'px';
        grid.style.width  = w + 'px';
        grid.style.height = h + 'px';
        grid.style.right  = 'auto';
        grid.style.bottom = 'auto';
};

Lightbox.prototype._gridAlign = function(on) {
        const grid = document.getElementById('lb-editor-grid');
        if (!grid) return;
        clearTimeout(this._gridTimer);
        if (on) {
            this._positionGrid();
            grid.classList.add('visible', 'fine');
            return;
        }
        grid.classList.remove('fine');
        this._gridTimer = setTimeout(() => {
            if (!this._edit.straightenOpen) grid.classList.remove('visible');
        }, 1200);
};

Lightbox.prototype._syncCropStraightenSlider = function() {
        const root = document.getElementById('lb-tilt-scale-c');
        const sv   = document.getElementById('lb-straighten-val-c');
        const v    = this._edit.straighten;
        if (root) root.style.setProperty('--value', v);
        if (sv)   sv.textContent = v.toFixed(1).replace('.', ',') + '°';
};

Lightbox.prototype._cropAttachListeners = function() {
        if (this._cropListeners) return;
        const overlay = document.getElementById('lb-editor-crop-overlay');
        this._cropListeners = {
            down: (ev) => this._cropPointerDown(ev),
            move: (ev) => this._cropPointerMove(ev),
            up:   (ev) => this._cropPointerUp(ev),
            // Geraet gedreht oder Tastatur eingeblendet: Footerhoehe und
            // Overlay stimmen sonst nicht mehr.
            resize: () => {
                if (!this._edit.cropActive) return;   // Listener bleibt registriert, s. _cropAttachListeners
                this._syncCropFooterHeight();
                this._repositionCropOverlay();
            },
        };
        overlay.addEventListener('pointerdown',   this._cropListeners.down);
        overlay.addEventListener('pointermove',   this._cropListeners.move);
        overlay.addEventListener('pointerup',     this._cropListeners.up);
        overlay.addEventListener('pointercancel', this._cropListeners.up);
        window.addEventListener('resize', this._cropListeners.resize);
};

Lightbox.prototype._cropRender = function() {
        const r      = this._cropRect;
        const rectEl = document.getElementById('lb-editor-crop-rect');
        if (!rectEl) return;
        rectEl.style.left   = (r.x * 100) + '%';
        rectEl.style.top    = (r.y * 100) + '%';
        rectEl.style.width  = (r.w * 100) + '%';
        rectEl.style.height = (r.h * 100) + '%';

        // Position dim divs around the crop rect (top/right/bottom/left)
        const right  = 1 - r.x - r.w;
        const bottom = 1 - r.y - r.h;
        const dims = document.querySelectorAll('.lb-editor-crop-dim');
        dims.forEach(d => {
            const side = d.dataset.dim;
            d.style.left = d.style.right = d.style.top = d.style.bottom = '';
            if (side === 'top') {
                d.style.top = '0'; d.style.left = '0'; d.style.right = '0';
                d.style.height = (r.y * 100) + '%';
            } else if (side === 'bottom') {
                d.style.bottom = '0'; d.style.left = '0'; d.style.right = '0';
                d.style.height = (bottom * 100) + '%';
            } else if (side === 'left') {
                d.style.left = '0';
                d.style.top = (r.y * 100) + '%';
                d.style.width = (r.x * 100) + '%';
                d.style.height = (r.h * 100) + '%';
            } else if (side === 'right') {
                d.style.right = '0';
                d.style.top = (r.y * 100) + '%';
                d.style.width = (right * 100) + '%';
                d.style.height = (r.h * 100) + '%';
            }
        });
};

Lightbox.prototype._cropPointerDown = function(e) {
        if (!this._edit.cropActive) return;
        const handle = e.target.dataset.handle;
        const onRect = e.target.id === 'lb-editor-crop-rect';
        const mode   = handle || (onRect ? 'move' : null);
        if (!mode) return;   // pass through clicks on the actions bar etc.

        e.preventDefault();
        const overlay = document.getElementById('lb-editor-crop-overlay');
        const r = overlay.getBoundingClientRect();

        this._cropDrag = {
            mode,
            startX:    e.clientX,
            startY:    e.clientY,
            startRect: { ...this._cropRect },
            ow:        r.width,
            oh:        r.height,
        };
        try { overlay.setPointerCapture(e.pointerId); } catch(_) {}
};

Lightbox.prototype._cropPointerMove = function(e) {
        if (!this._cropDrag) return;
        const d   = this._cropDrag;
        const dx  = (e.clientX - d.startX) / d.ow;
        const dy  = (e.clientY - d.startY) / d.oh;
        const s   = d.startRect;
        const MIN = 0.05;
        const r   = { ...s };

        switch (d.mode) {
            case 'move':
                r.x = Math.max(0, Math.min(1 - s.w, s.x + dx));
                r.y = Math.max(0, Math.min(1 - s.h, s.y + dy));
                break;
            case 'nw':
                r.x = Math.max(0, Math.min(s.x + s.w - MIN, s.x + dx));
                r.y = Math.max(0, Math.min(s.y + s.h - MIN, s.y + dy));
                r.w = s.w - (r.x - s.x);
                r.h = s.h - (r.y - s.y);
                break;
            case 'ne':
                r.y = Math.max(0, Math.min(s.y + s.h - MIN, s.y + dy));
                r.w = Math.max(MIN, Math.min(1 - s.x, s.w + dx));
                r.h = s.h - (r.y - s.y);
                break;
            case 'sw':
                r.x = Math.max(0, Math.min(s.x + s.w - MIN, s.x + dx));
                r.w = s.w - (r.x - s.x);
                r.h = Math.max(MIN, Math.min(1 - s.y, s.h + dy));
                break;
            case 'se':
                r.w = Math.max(MIN, Math.min(1 - s.x, s.w + dx));
                r.h = Math.max(MIN, Math.min(1 - s.y, s.h + dy));
                break;
        }
        this._cropRect = r;
        this._cropRender();
};

Lightbox.prototype._cropPointerUp = function(e) {
        if (this._cropDrag) {
            const overlay = document.getElementById('lb-editor-crop-overlay');
            try { overlay.releasePointerCapture(e.pointerId); } catch(_) {}
        }
        this._cropDrag = null;
};


Lightbox.prototype.deletePhoto = async function() {
        const img = this.allImages[this.currentIndex];
        if (!img) return;

        const recycleActive = window._recycleActive;
        const _tt = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
        const hint = recycleActive === true
            ? _tt('lightbox.confirm_delete_hint_recycle', 'Die Datei landet im Papierkorb.')
            : recycleActive === false
                ? _tt('lightbox.confirm_delete_hint_norecycle', 'No recycle bin active — the file will be deleted irreversibly.')
                : _tt('lightbox.confirm_delete_hint_default', 'This action cannot be undone.');

        const ok = await window.mpdConfirm({
            icon        : '🗑',
            title       : _tt('lightbox.confirm_delete_title', 'Delete photo permanently?'),
            subtitle    : img.file,
            body        : _tt('lightbox.confirm_delete_body', 'Datei, Thumbnails und alle Backup-Versionen (.bak) werden entfernt.'),
            hint        : hint,
            hintType    : recycleActive === true ? 'info' : 'warn',
            confirmLabel: _tt('lightbox.confirm_delete_label', 'Delete'),
            destructive : true,
        });
        if (!ok) return;

        try {
            const url = `${window.location.origin}/api/album/` +
                `${encodeURIComponent(this.albumSpace)}/` +
                `${encodeURIComponent(this.albumName)}/file`;

            const resp = await fetch(url, {
                method : 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                // album_context: Die Web-Lightbox laeuft immer aus einem
                // Album heraus (album.html) — anders als in der App, die
                // auch einen Zeitstrahl hat. Siehe Loeschschutz 06.09.
                body   : JSON.stringify({ filename: img.file, album_context: true }),
            });
            const result = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                // Dialog aus album-insert.js (409 mit used_by, sonst Fehlertext).
                await showDeleteErrorDialog(resp.status, Object.assign({ statusText: resp.statusText }, result), img.file);
                return;
            }

            if (result.was_thumbnail) {
                await showWasThumbnailDialog(img.file);
            }

            // Keep background album in sync: remove entry from albumData and
            // re-render the grid so the deleted photo doesn't linger after
            // the lightbox closes.
            if (typeof albumData !== 'undefined' && albumData && Array.isArray(albumData.elements)) {
                const idx = albumData.elements.findIndex(e => e.file === img.file);
                if (idx !== -1) {
                    albumData.elements.splice(idx, 1);
                    if (typeof renderAlbum === 'function') {
                        try {
                            const _sy = window.scrollY;
                            renderAlbum();
                            requestAnimationFrame(() => window.scrollTo(0, _sy));
                        } catch(_) {}
                    }
                }
            }

            this.allImages.splice(this.currentIndex, 1);
            if (this.allImages.length === 0) { this.close(); return; }
            if (this.currentIndex >= this.allImages.length) {
                this.currentIndex = this.allImages.length - 1;
            }
            this.show();

        } catch(err) {
            await window.mpdAlert({
                icon : '⚠️',
                title: _tt('album.delete_failed_title', 'Löschen fehlgeschlagen'),
                body : err.message,
            });
        }
};