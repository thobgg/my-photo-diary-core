/* ═══════════════════════════════════════════
 *  Lightbox — EXIF & metadata module
 *  Extends Lightbox.prototype
 *  Dependency: lightbox-core.js must be loaded first
 *  ═══════════════════════════════════════════ */

/* Kartenkacheln OHNE Schlüssel (05.09.2026, korrigiert am selben Tag).
 * Vorher lag hier ein persönlicher MapTiler-Schlüssel im Klartext — für
 * eine ausgelieferte Installation falsch. Der erste Versuch schwenkte auf
 * Carto; das war ein Fehlgriff: Carto verlangt seit Kurzem selbst einen
 * Schlüssel und brennt sonst „API KEY REQUIRED" in jede Kachel. Jetzt
 * OpenStreetMap-Standardkacheln — schlüsselfrei, wie überall in MPD. */
const _LB_MT_URL   = 'https://tile.openstreetmap.de/{z}/{x}/{y}.png';
const _LB_MT_ATTR  = '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> · Kacheln: FOSSGIS e.V.';

/* ── DOM helpers ─────────────────────────────────────── */

function _el(tag, attrs, ...children) {
    attrs = attrs || {};
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === 'class')       el.className = v;
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
        else if (k === 'style')  el.setAttribute('style', v);
        else                     el.setAttribute(k, v);
    }
    children.forEach(c => {
        if (c == null) return;
        el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return el;
}

function _metaGroup() {
    const el = document.createElement('div');
    el.className = 'lb-meta-group';
    for (const c of arguments) { if (c) el.appendChild(c); }
    return el;
}

function _metaRow(label, value) {
    const row = document.createElement('div');
    row.className = 'lb-meta-item';
    const lbl = document.createElement('span');
    lbl.className = 'lb-meta-label';
    lbl.textContent = label;
    const val = document.createElement('span');
    val.className = 'lb-meta-value';
    val.textContent = value;
    row.appendChild(lbl);
    row.appendChild(val);
    return row;
}

function _metaFullRow(value) {
    const row = document.createElement('div');
    row.className = 'lb-meta-item';
    const val = document.createElement('span');
    val.className = 'lb-meta-value';
    val.textContent = value;
    row.appendChild(val);
    return row;
}

// Inline line-art icons used inside the EXIF sidebar — same vocabulary
// as the rest of the lightbox (Lucide-style outline, currentColor).
const _EXIF_SVG_ATTR = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
const _EXIF_ICONS = {
    calendar: `<svg ${_EXIF_SVG_ATTR}><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>`,
    camera:   `<svg ${_EXIF_SVG_ATTR}><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>`,
    pin:      `<svg ${_EXIF_SVG_ATTR}><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`,
    chat:     `<svg ${_EXIF_SVG_ATTR}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
    pencil:   `<svg ${_EXIF_SVG_ATTR}><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`,
    x:        `<svg ${_EXIF_SVG_ATTR}><path d="M18 6L6 18M6 6l12 12"/></svg>`,
    search:   `<svg ${_EXIF_SVG_ATTR}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
    loader:   `<svg ${_EXIF_SVG_ATTR} class="exif-icon-spin"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>`,
    xcircle:  `<svg ${_EXIF_SVG_ATTR}><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
    warn:     `<svg ${_EXIF_SVG_ATTR}><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
};
function _exifIcon(name) { return _EXIF_ICONS[name] || ''; }

function _metaH3(text, iconName) {
    const h = document.createElement('h3');
    h.style.cssText = 'font-size:0.95rem;margin-bottom:0.75rem;display:flex;align-items:center;gap:8px;';
    if (iconName) {
        const icon = document.createElement('span');
        icon.className = 'lb-meta-h3-icon';
        icon.innerHTML = _exifIcon(iconName);
        h.appendChild(icon);
    }
    const label = document.createElement('span');
    label.textContent = text;
    h.appendChild(label);
    return h;
}

function _parseExifDate(raw) {
    if (!raw) return null;
    try {
        if (typeof raw === 'number')  return new Date(raw * 1000);
        if (raw.includes('T'))        return new Date(raw);
        if (raw.match(/^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$/)) {
            return new Date(raw.slice(0,4) + '-' + raw.slice(5,7) + '-' + raw.slice(8,10) + 'T' + raw.slice(11));
        }
        return new Date(raw);
    } catch(e) { return null; }
}

function _isoStemFromExif(rawDt) {
    if (!rawDt) return '';
    try {
        const pad = n => String(n).padStart(2, '0');
        if (rawDt.match(/^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}/)) {
            return rawDt.slice(0,4) + '-' + rawDt.slice(5,7) + '-' + rawDt.slice(8,10)
                + '_' + rawDt.slice(11,13) + '-' + rawDt.slice(14,16) + '-' + rawDt.slice(17,19);
        }
        const d = new Date(rawDt);
        if (!isNaN(d)) {
            return d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate())
                + '_' + pad(d.getHours()) + '-' + pad(d.getMinutes()) + '-' + pad(d.getSeconds());
        }
    } catch(e) { /* ignore */ }
    return '';
}

/* ── Metadaten-Sektionen ─────────────────────────────────────── */

function _buildBasicSection(img) {
    return _metaGroup(
        _metaRow(_exifT('exif_view.label_filename', 'Datei:'), img.file),
        _metaRow(_exifT('exif_view.label_resolution', 'Resolution:'), img.resolution.width + ' × ' + img.resolution.height)
    );
}

function _exifT(k, fb) { return (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb; }

/* Mobil-sicherer Bestätigungsdialog. Android-WebView/PWA (mpd.apk) blockt
 * native confirm()/alert()/prompt() — sie liefern dann still `false`, sodass
 * Schreibvorgänge (Umbenennen, EXIF/GPS speichern) unbemerkt abbrechen. Daher
 * über den eigenen mpdConfirm-Dialog gehen; Fallback auf window.confirm, falls
 * mpd-dialog.js mal nicht geladen ist. Gibt ein Promise<boolean> zurück. */
function _lbConfirm(opts) {
    if (window.mpdConfirm) return window.mpdConfirm(opts);
    const txt = [opts.title, opts.subtitle, opts.body, opts.hint].filter(Boolean).join('\n');
    return Promise.resolve(window.confirm(txt));
}

function _buildDateSection(exif) {
    const raw = exif.datetime || exif.DateTimeOriginal || exif.CreateDate || exif.DateTime;
    if (!raw) return null;
    const group = _metaGroup(_metaH3(_exifT('exif_view.section_date', 'Aufnahme'), 'calendar'));
    const date  = _parseExifDate(raw);
    if (date && !isNaN(date.getTime())) {
        const formatted = date.toLocaleString('de-DE', {
            weekday: 'long', year: 'numeric', month: 'long',
            day: 'numeric', hour: '2-digit', minute: '2-digit'
        });
        group.appendChild(_metaFullRow(formatted));
    } else {
        const row = document.createElement('div');
        row.className = 'lb-meta-item';
        const span = document.createElement('span');
        span.className = 'lb-meta-value';
        span.style.color = '#ff6b6b';
        const warnTxt = (window.MPD_I18N ? window.MPD_I18N.t('exif_editor.warn_unknown_date', { raw }) : 'Unbekanntes Datum: ' + raw);
        span.innerHTML = `${_exifIcon('warn')}<span style="margin-left:6px;vertical-align:middle">${warnTxt}</span>`;
        span.style.display = 'inline-flex';
        span.style.alignItems = 'center';
        row.appendChild(span);
        group.appendChild(row);
    }
    return group;
}

function _buildCameraSection(exif) {
    if (!exif.camera && !exif.lens) return null;
    const group = _metaGroup(_metaH3(_exifT('exif_view.section_camera', 'Kamera'), 'camera'));
    if (exif.camera) {
        const make  = (exif.camera.make  || '').trim();
        const model = (exif.camera.model || '').trim();
        /* Many vendors (Canon, Nikon) repeat the make in the model — dedup */
        const cam = (model && model.toLowerCase().startsWith(make.toLowerCase()))
            ? model
            : (make + ' ' + model).trim();
        group.appendChild(_metaFullRow(cam));
    }
    if (exif.lens) group.appendChild(_metaFullRow(exif.lens));
    return group;
}

function _buildGpsSection(exif, img) {
    if (!exif.gps || !exif.gps.lat || !exif.gps.lon) return null;
    const mapId  = 'map-' + img.file.replace(/\./g, '-');
    const mapDiv = document.createElement('div');
    mapDiv.id = mapId;
    mapDiv.className = 'lb-map';
    const link = document.createElement('a');
    link.href = 'https://www.openstreetmap.org/?mlat=' + exif.gps.lat + '&mlon=' + exif.gps.lon + '&zoom=15';
    link.target = '_blank';
    link.className = 'lb-map-link';
    const osmLabel = _exifT('exif_view.link_osm', 'Open in OpenStreetMap');
    link.innerHTML = `${_exifIcon('pin')}<span style="margin-left:6px;vertical-align:middle">${osmLabel}</span>`;
    link.style.display = 'inline-flex';
    link.style.alignItems = 'center';
    return _metaGroup(_metaH3(_exifT('exif_view.section_gps', 'Standort'), 'pin'), mapDiv, link);
}

function _buildDescriptionSection(exif) {
    if (!exif.description) return null;
    const row = document.createElement('div');
    row.className = 'lb-meta-item';
    const val = document.createElement('span');
    val.className = 'lb-meta-value';
    val.style.whiteSpace = 'pre-wrap';
    val.textContent = exif.description;
    row.appendChild(val);
    return _metaGroup(_metaH3(_exifT('exif_view.section_description', 'Beschreibung'), 'chat'), row);
}

function _makeInput(type, id, value, styleCss) {
    const el = document.createElement('input');
    el.type  = type;
    el.id    = id;
    if (value !== undefined) el.value = value;
    el.setAttribute('style', styleCss);
    return el;
}

function _makeBtn(id, text, styleCss) {
    const el = document.createElement('button');
    el.id = id;
    el.textContent = text;
    el.setAttribute('style', styleCss);
    return el;
}

const _inputBase = 'background:#1a2535;border:1px solid rgba(255,255,255,0.2);border-radius:4px;color:#e8e8e8;';
const _btnBase   = 'background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.2);border-radius:4px;color:#e8e8e8;cursor:pointer;';

function _buildExifEditSection(exif, img) {
    const rawDt       = exif.datetime;
    const isoSugg     = _isoStemFromExif(rawDt);
    const currentStem = img.file.replace(/\.[^.]+$/, '');
    const renameSugg  = isoSugg && isoSugg !== currentStem ? isoSugg : currentStem;
    const alt         = exif.gps ? (exif.gps.altitude || '') : '';

    // Datum vorbelegen
    let dtValue = '';
    if (rawDt) {
        try {
            if (rawDt.match(/^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}/)) {
                dtValue = rawDt.slice(0,4) + '-' + rawDt.slice(5,7) + '-' + rawDt.slice(8,10)
                        + 'T' + rawDt.slice(11,16);
            } else {
                const d = new Date(rawDt);
                if (!isNaN(d)) {
                    const pad = n => String(n).padStart(2,'0');
                    dtValue = d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate())
                            + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
                }
            }
        } catch(e) { /* ignore */ }
    }

    const dtInput   = _makeInput('datetime-local', 'lb-exif-dt', dtValue,
        _inputBase + 'padding:4px 6px;font-size:0.85rem;width:100%;box-sizing:border-box;margin-top:4px;');
    dtInput.dataset.original = dtValue;
    const _pickLabel = () => (window.MPD_I18N ? window.MPD_I18N.t('exif_editor.btn_pick_on_map') : 'Pick on map');
    const mapToggle = _makeBtn('lb-exif-map-toggle', '',
        _btnBase + 'padding:4px 10px;font-size:0.82rem;display:inline-flex;align-items:center;gap:6px;');
    mapToggle.innerHTML = `${_exifIcon('pin')}<span>${_pickLabel()}</span>`;
    const mapDiv    = document.createElement('div');
    mapDiv.id = 'lb-exif-map';
    mapDiv.setAttribute('style', 'height:0;border-radius:4px;width:100%;box-sizing:border-box;margin-top:4px;transition:height 0.2s;overflow:hidden;');

    const geoSearch = _makeInput('text', 'lb-exif-geo-search', undefined,
        'flex:1;' + _inputBase + 'padding:3px 6px;font-size:0.78rem;box-sizing:border-box;');
    geoSearch.placeholder = (window.MPD_I18N ? window.MPD_I18N.t('exif_editor.geo_search_placeholder') : 'Ort suchen… (Enter)');
    const geoBtn  = _makeBtn('lb-exif-geo-btn', '', _btnBase + 'padding:3px 8px;font-size:0.78rem;white-space:nowrap;display:inline-flex;align-items:center;');
    geoBtn.innerHTML = _exifIcon('search');
    const coordsSpan = document.createElement('span');
    coordsSpan.id = 'lb-exif-coords';
    coordsSpan.setAttribute('style', 'display:block;font-size:0.75rem;color:#aaa;margin-bottom:4px;');
    coordsSpan.textContent = (window.MPD_I18N ? window.MPD_I18N.t('exif_editor.coords_no_pin') : 'Kein Pin gesetzt');
    const altInput = _makeInput('number', 'lb-exif-alt', String(alt),
        _inputBase + 'padding:3px 6px;font-size:0.78rem;width:100%;box-sizing:border-box;');
    altInput.placeholder = (window.MPD_I18N ? window.MPD_I18N.t('exif_editor.alt_placeholder') : 'Altitude in meters (optional)');
    altInput.step = 'any';

    const geoRow = document.createElement('div');
    geoRow.setAttribute('style', 'display:flex;gap:4px;margin-bottom:5px;');
    geoRow.appendChild(geoSearch);
    geoRow.appendChild(geoBtn);
    const mapInfo = document.createElement('div');
    mapInfo.id = 'lb-exif-map-info';
    mapInfo.setAttribute('style', 'display:none;margin-top:5px;width:100%;box-sizing:border-box;');
    mapInfo.appendChild(geoRow);
    mapInfo.appendChild(coordsSpan);
    mapInfo.appendChild(altInput);

    const stemInput = _makeInput('text', 'lb-exif-stem', renameSugg,
        'display:block;' + _inputBase + 'padding:4px 6px;font-size:0.82rem;width:100%;box-sizing:border-box;margin-top:4px;');
    const stemHint = document.createElement('div');
    stemHint.setAttribute('style', 'display:block;font-size:0.72rem;color:#888;margin-top:3px;');
    const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
    const hintKey = (isoSugg && isoSugg !== currentStem) ? 'exif_editor.filename_iso_suggestion' : 'exif_editor.filename_current';
    const hintFb  = '.jpg · Aktuell: ' + currentStem + '.jpg'
        + (isoSugg && isoSugg !== currentStem ? ' · ISO-Vorschlag aus EXIF' : '');
    stemHint.textContent = _t(hintKey, { stem: currentStem }, hintFb);

    const saveBtn   = _makeBtn('lb-exif-save', _t('exif_editor.btn_save', null, 'Speichern'),
        'background:#2E75B6;border:none;border-radius:4px;color:#fff;padding:6px 14px;font-size:0.85rem;cursor:pointer;');
    const renameBtn = _makeBtn('lb-exif-rename', _t('exif_editor.btn_rename', null, 'Umbenennen'),
        _btnBase + 'padding:6px 14px;font-size:0.85rem;');
    const statusSpan = document.createElement('span');
    statusSpan.id = 'lb-exif-status';
    statusSpan.setAttribute('style', 'font-size:0.8rem;width:100%;');

    const dtRow = document.createElement('div');
    dtRow.className = 'lb-meta-item';
    const dtLbl = document.createElement('span'); dtLbl.className = 'lb-meta-label'; dtLbl.textContent = _t('exif_editor.label_datetime', null, 'Datum/Zeit:');
    dtRow.appendChild(dtLbl); dtRow.appendChild(dtInput);

    const gpsRow = document.createElement('div');
    gpsRow.className = 'lb-meta-item'; gpsRow.style.marginTop = '8px';
    const gpsLbl = document.createElement('span'); gpsLbl.className = 'lb-meta-label'; gpsLbl.textContent = _t('exif_editor.label_gps', null, 'GPS:');
    gpsRow.appendChild(gpsLbl); gpsRow.appendChild(mapToggle);

    const stemWrap = document.createElement('div');
    stemWrap.setAttribute('style', 'margin-top:8px;width:100%;box-sizing:border-box;');
    const stemLbl = document.createElement('span'); stemLbl.className = 'lb-meta-label'; stemLbl.textContent = _t('exif_editor.label_filename', null, 'Dateiname:');
    stemWrap.appendChild(stemLbl); stemWrap.appendChild(stemInput); stemWrap.appendChild(stemHint);

    const btnRow = document.createElement('div');
    btnRow.setAttribute('style', 'margin-top:10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;');
    btnRow.appendChild(saveBtn); btnRow.appendChild(renameBtn); btnRow.appendChild(statusSpan);

    const group = document.createElement('div');
    group.className = 'lb-meta-group lb-exif-edit-group';
    group.appendChild(_metaH3('EXIF bearbeiten', 'pencil'));
    group.appendChild(dtRow);
    group.appendChild(gpsRow);
    group.appendChild(mapDiv);
    group.appendChild(mapInfo);
    group.appendChild(stemWrap);
    group.appendChild(btnRow);
    return group;
}

/* ══════════════════════════════════════════
 *  METADATA / EXIF — Haupt-Methode
 * ══════════════════════════════════════════ */
Lightbox.prototype.loadMetadata = async function(img) {
    const metadataDiv = document.getElementById('lb-metadata');
    const API_BASE    = window.location.origin;

    try {
        const response = await fetch(
            API_BASE + '/api/photo/' + encodeURIComponent(this.albumSpace) + '/' +
            encodeURIComponent(img.source_album || this.albumName) + '/' + encodeURIComponent(img.file) + '/exif'
        );
        const exif = await response.json();

        // Push date / time / location to the mobile topbar (no-op on desktop)
        this._updateMobileTopbar(exif);

        const frag = document.createDocumentFragment();
        frag.appendChild(_buildBasicSection(img));

        const sections = [
            _buildDateSection(exif),
            _buildCameraSection(exif),
            _buildGpsSection(exif, img),
            _buildDescriptionSection(exif),
        ];
        sections.forEach(s => { if (s) frag.appendChild(s); });

        if (exif.error) {
            const p = document.createElement('p');
            p.style.cssText = 'color:#888;font-size:0.85rem;';
            p.textContent = exif.error;
            frag.appendChild(_metaGroup(p));
        }

        const canEdit = !window.MPD_READ_ONLY && img.file.match(/\.jpe?g$/i);
        if (canEdit) frag.appendChild(_buildExifEditSection(exif, img));

        metadataDiv.innerHTML = '';
        metadataDiv.appendChild(frag);

        // Event-Handler verdrahten
        if (canEdit) {
            document.getElementById('lb-exif-save')
                ?.addEventListener('click', () => this._saveExif(img));
            document.getElementById('lb-exif-map-toggle')
                ?.addEventListener('click', () => this._toggleExifMap(exif));
            document.getElementById('lb-exif-rename')
                ?.addEventListener('click', () => this._renameFile(img));

            const dtInput   = document.getElementById('lb-exif-dt');
            const stemInput = document.getElementById('lb-exif-stem');
            if (dtInput && stemInput) {
                dtInput.addEventListener('input', () => {
                    const v = dtInput.value;
                    if (v && v.length >= 16) {
                        stemInput.value = v.substring(0,10) + '_' + v.substring(11).replace(/:/g,'-') + '-00';
                    }
                });
            }
        }

        // GPS-Karte
        if (exif.gps && exif.gps.lat && exif.gps.lon && typeof L !== 'undefined') {
            setTimeout(() => {
                const mapId  = 'map-' + img.file.replace(/\./g, '-');
                const mapDiv = document.getElementById(mapId);
                if (mapDiv && !mapDiv._leaflet_id) {
                    const map = L.map(mapId).setView([exif.gps.lat, exif.gps.lon], 13);
                    L.tileLayer(_LB_MT_URL, { attribution: _LB_MT_ATTR }).addTo(map);
                    L.marker([exif.gps.lat, exif.gps.lon]).addTo(map);
                    mapDiv._leafletInstance = map;
                }
            }, 300);
        }

    } catch (error) {
        console.error('EXIF error:', error);
        metadataDiv.innerHTML = '';
        const errFrag = document.createDocumentFragment();
        const _resLbl = (window.MPD_I18N ? window.MPD_I18N.t('exif_view.label_resolution') : 'Resolution:');
        errFrag.appendChild(_metaGroup(
            _metaRow(_exifT('exif_view.label_filename', 'File:'), img.file),
            _metaRow(_resLbl, img.resolution.width + ' × ' + img.resolution.height)
        ));
        const p = document.createElement('p');
        p.style.cssText = 'color:#ff6b6b;font-size:0.85rem;';
        p.textContent = (window.MPD_I18N ? window.MPD_I18N.t('exif_view.load_failed') : '⚠️ EXIF data could not be loaded');
        errFrag.appendChild(_metaGroup(p));
        metadataDiv.appendChild(errFrag);
    }
};

/* ══════════════════════════════════════════
 *  DATEI UMBENENNEN
 * ══════════════════════════════════════════ */
Lightbox.prototype._renameFile = async function(img) {
    const stemInput = document.getElementById('lb-exif-stem');
    const statusEl  = document.getElementById('lb-exif-status');
    const renameBtn = document.getElementById('lb-exif-rename');

    const newStem = stemInput ? stemInput.value.trim() : '';
    if (!newStem) {
        statusEl.style.color = '#f0a500';
        statusEl.textContent = _exifT('lightbox.exif_filename_empty', 'Dateiname darf nicht leer sein.');
        return;
    }
    const currentStem = img.file.replace(/\.[^.]+$/, '');
    if (newStem === currentStem) {
        statusEl.style.color = '#f0a500';
        statusEl.textContent = _exifT('lightbox.exif_name_unchanged', 'Name unchanged — nothing to do.');
        return;
    }

    statusEl.style.color = '#888';
    statusEl.textContent = _exifT('lightbox.exif_checking', 'Checking…');
    renameBtn.disabled = true;

    try {
        // PDX v1.4: for a referenced photo the backend writes into the source album
        if (img.source_album && !await this._confirmSourceEdit(img)) {
            statusEl.textContent = '';
            renameBtn.disabled = false;
            return;
        }
        const base     = window.location.origin;
        const encSpace = encodeURIComponent(this.albumSpace);
        const encAlbum = encodeURIComponent(this._resolveAlbum(img));
        const encFile  = encodeURIComponent(img.file);

        const previewResp = await fetch(
            base + '/api/photo/' + encSpace + '/' + encAlbum + '/' + encFile + '/rename/preview',
            { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ new_stem: newStem }) }
        );
        if (!previewResp.ok) throw new Error((await previewResp.json()).detail || previewResp.statusText);
        const preview = await previewResp.json();

        const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
        const okRename = await _lbConfirm({
            title:    _t('exif_editor.rename_q_title', null, 'Datei umbenennen?'),
            subtitle: img.file + '  →  ' + preview.final_name,
            body:     _t('exif_editor.rename_q_album_note', null, 'album.json wird automatisch aktualisiert.'),
            hint:     preview.collision ? _t('exif_editor.rename_q_collision', null, '⚠️ Name already taken — suffix automatically appended.') : undefined,
            hintType: preview.collision ? 'warn' : undefined,
            confirmLabel: _t('exif_editor.btn_rename', null, 'Umbenennen'),
        });
        if (!okRename) {
            statusEl.textContent = '';
            renameBtn.disabled = false;
            return;
        }

        statusEl.textContent = (window.MPD_I18N ? window.MPD_I18N.t('exif_editor.rename_status_running') : 'Umbenennen…');
        const renameResp = await fetch(
            base + '/api/photo/' + encSpace + '/' + encAlbum + '/' + encFile + '/rename',
            { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ new_stem: newStem }) }
        );
        if (!renameResp.ok) throw new Error((await renameResp.json()).detail || renameResp.statusText);
        const result = await renameResp.json();

        const _tEnd = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
        if (result.renamed) {
            img.file = result.renamed;
            statusEl.style.color = '#4caf50';
            statusEl.textContent = _tEnd('exif_editor.rename_status_success', { name: result.renamed }, '✓ Umbenannt zu: ' + result.renamed);
            if (stemInput) stemInput.value = result.renamed.replace(/\.[^.]+$/, '');
        } else {
            statusEl.style.color = '#888';
            statusEl.textContent = _tEnd('exif_editor.rename_status_no_change', null, 'No change.');
        }
    } catch(e) {
        statusEl.style.color = '#ff6b6b';
        statusEl.textContent = (window.MPD_I18N ? window.MPD_I18N.t('exif_editor.error_prefix', { msg: e.message }) : '⚠️ ' + e.message);
    } finally {
        renameBtn.disabled = false;
    }
};

/* ══════════════════════════════════════════
 *  EXIF KARTE
 * ══════════════════════════════════════════ */
Lightbox.prototype._toggleExifMap = function(exif) {
    const toggleBtn = document.getElementById('lb-exif-map-toggle');
    const mapDiv    = document.getElementById('lb-exif-map');
    const mapInfo   = document.getElementById('lb-exif-map-info');
    if (!mapDiv || typeof L === 'undefined') return;

    const visible = mapDiv.style.height !== '0px' && mapDiv.style.height !== '0';
    if (visible) {
        mapDiv.style.height = '0';
        mapDiv.style.overflow = 'hidden';
        if (mapInfo) mapInfo.style.display = 'none';
        const lbl = (window.MPD_I18N ? window.MPD_I18N.t('exif_editor.btn_pick_on_map') : 'Pick on map');
        toggleBtn.innerHTML = `${_exifIcon('pin')}<span>${lbl}</span>`;
        return;
    }

    // Reopen: ein Pin kann aus einem früheren Öffnen noch auf der Karte liegen.
    // Den nicht stillschweigend verwerfen, sonst meldet _saveExif "kein GPS"
    // obwohl der Marker sichtbar ist. Aus der aktuellen Marker-Position rekonstruieren.
    const _prevMarker = mapDiv._leafletMarker;
    if (_prevMarker && typeof _prevMarker.getLatLng === 'function') {
        const _ll = _prevMarker.getLatLng();
        window._exifPickedGps = { lat: _ll.lat, lon: _ll.lng };
        const _coordsEl = document.getElementById('lb-exif-coords');
        if (_coordsEl) _coordsEl.textContent = _ll.lat.toFixed(6) + ', ' + _ll.lng.toFixed(6);
    } else {
        window._exifPickedGps = null;
    }
    mapDiv.style.height = '200px';
    mapDiv.style.overflow = 'hidden';
    if (mapInfo) mapInfo.style.display = 'block';
    const closeLbl = (window.MPD_I18N ? window.MPD_I18N.t('exif_editor.btn_close_map') : 'Close map');
    toggleBtn.innerHTML = `${_exifIcon('x')}<span>${closeLbl}</span>`;

    if (mapDiv._leafletInstance) {
        setTimeout(() => mapDiv._leafletInstance.invalidateSize(), 10);
        return;
    }

    const initLat  = (exif.gps && exif.gps.lat) ? exif.gps.lat : 51.2;
    const initLon  = (exif.gps && exif.gps.lon) ? exif.gps.lon : 10.4;
    const initZoom = (exif.gps && exif.gps.lat) ? 13 : 6;

    const map = L.map('lb-exif-map').setView([initLat, initLon], initZoom);
    L.tileLayer(_LB_MT_URL, { attribution: _LB_MT_ATTR }).addTo(map);

    let marker = null;
    if (exif.gps && exif.gps.lat && exif.gps.lon) {
        marker = L.marker([exif.gps.lat, exif.gps.lon]).addTo(map);
        window._exifPickedGps = { lat: exif.gps.lat, lon: exif.gps.lon };
        const coordsEl = document.getElementById('lb-exif-coords');
        if (coordsEl) coordsEl.textContent = exif.gps.lat.toFixed(6) + ', ' + exif.gps.lon.toFixed(6);
    }

    map.on('click', (e) => {
        const { lat, lng } = e.latlng;
        if (marker) { marker.setLatLng([lat, lng]); }
        else        { marker = L.marker([lat, lng]).addTo(map); }
        mapDiv._leafletMarker = marker;   // Referenz aktuell halten — sonst geht der Pin beim Wiederöffnen verloren
        window._exifPickedGps = { lat: lat, lon: lng };
        const coordsEl = document.getElementById('lb-exif-coords');
        if (coordsEl) coordsEl.textContent = lat.toFixed(6) + ', ' + lng.toFixed(6);
    });

    mapDiv._leafletInstance = map;
    mapDiv._leafletMarker   = marker;

    const geoInput = document.getElementById('lb-exif-geo-search');
    const geoBtn   = document.getElementById('lb-exif-geo-btn');
    const doGeoSearch = async () => {
        const q = geoInput ? geoInput.value.trim() : '';
        if (!q) return;
        geoBtn.innerHTML = _exifIcon('loader');
        try {
            const res  = await fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(q));
            const data = await res.json();
            if (!data.length) {
                geoBtn.innerHTML = _exifIcon('xcircle');
                setTimeout(() => { geoBtn.innerHTML = _exifIcon('search'); }, 1500);
                return;
            }
            map.setView([parseFloat(data[0].lat), parseFloat(data[0].lon)], 14);
            geoBtn.innerHTML = _exifIcon('search');
        } catch(err) {
            geoBtn.innerHTML = _exifIcon('xcircle');
            setTimeout(() => { geoBtn.innerHTML = _exifIcon('search'); }, 1500);
        }
    };
    if (geoInput) geoInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); doGeoSearch(); } });
    if (geoBtn)   geoBtn.addEventListener('click', doGeoSearch);

    requestAnimationFrame(() => {
        map.invalidateSize();
        setTimeout(() => map.invalidateSize(), 100);
        setTimeout(() => map.invalidateSize(), 400);
    });
};

/* ══════════════════════════════════════════
 *  EXIF SPEICHERN
 * ══════════════════════════════════════════ */
Lightbox.prototype._saveExif = async function(img) {
    const statusEl  = document.getElementById('lb-exif-status');
    const saveBtn   = document.getElementById('lb-exif-save');
    const dtInput   = document.getElementById('lb-exif-dt');
    const altInput  = document.getElementById('lb-exif-alt');
    const stemInput = document.getElementById('lb-exif-stem');
    const picked    = window._exifPickedGps || null;

    const currentStem = img.file.replace(/\.[^.]+$/, '');
    const newStem     = stemInput ? stemInput.value.trim() : '';
    const origDt      = dtInput   ? (dtInput.dataset.original || '') : '';

    const dtChanged   = dtInput.value && dtInput.value !== origDt;
    const stemChanged = newStem && newStem !== currentStem;
    const gpsChanged  = !!picked;
    const willRename  = stemChanged;

    if (!dtChanged && !gpsChanged && !stemChanged) {
        statusEl.style.color = '#f0a500';
        statusEl.textContent = _exifT('lightbox.exif_no_changes', 'No changes.');
        return;
    }

    const body = {};
    if (dtChanged)   body.datetime = dtInput.value + ':00';
    if (gpsChanged) {
        body.gps = { lat: picked.lat, lon: picked.lon };
        if (altInput && altInput.value) body.gps.alt = parseFloat(altInput.value);
    }
    if (stemChanged) body.new_stem = newStem;

    const suffix     = img.file.replace(/.*(\.[^.]+)$/, '$1').toLowerCase();
    const backupBase = willRename ? newStem + suffix : img.file;
    const writesExif = dtChanged || gpsChanged;
    const _details = [];
    if (body.datetime) _details.push(_exifT('lightbox.exif_confirm_label_datetime', 'Datum/Zeit: ') + dtInput.value.replace('T', '  '));
    if (body.gps)      _details.push(_exifT('lightbox.exif_confirm_label_gps',      'GPS: ')        + picked.lat.toFixed(6) + ', ' + picked.lon.toFixed(6) + (altInput && altInput.value ? ', ' + altInput.value + ' m' : ''));
    const okSave = await _lbConfirm({
        title:    writesExif ? _exifT('lightbox.exif_confirm_overwrite', 'Overwrite EXIF?')
                             : _exifT('lightbox.exif_confirm_rename',    'Umbenennen?'),
        subtitle: willRename ? (img.file + '  →  ' + backupBase) : undefined,
        body:     _details.join('  ·  ') || undefined,
        hint:     writesExif ? (_exifT('lightbox.exif_confirm_backup_note', 'Backup wird angelegt als:') + ' ' + backupBase + '.bak') : undefined,
        confirmLabel: _exifT('common.ok', 'OK'),
    });
    if (!okSave) return;
    // PDX v1.4: cross-album confirmation for referenced photos (once per session)
    if (img.source_album && !await this._confirmSourceEdit(img)) return;

    saveBtn.disabled = true;
    statusEl.style.color = '#888';
    statusEl.textContent = _exifT('lightbox.editor_status_saving', 'Speichern…');

    try {
        const url  = window.location.origin + '/api/photo/' + encodeURIComponent(this.albumSpace)
                   + '/' + encodeURIComponent(this._resolveAlbum(img)) + '/' + encodeURIComponent(img.file) + '/exif';
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || resp.statusText);

        let msg = '✓ Gespeichert.';
        if (data.renamed) {
            msg += ' Umbenannt → ' + data.renamed;
            img.file = data.renamed;
            if (stemInput) stemInput.value = data.renamed.replace(/\.[^.]+$/, '');
        }
        statusEl.style.color = '#4caf50';
        statusEl.textContent = msg;
        setTimeout(() => this.loadMetadata(img), 1500);

    } catch(e) {
        statusEl.style.color = '#ff6b6b';
        statusEl.textContent = '⚠️ ' + e.message;
        saveBtn.disabled = false;
    }
};
