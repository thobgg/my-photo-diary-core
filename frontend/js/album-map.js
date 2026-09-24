/* MPD – Insert map (Leaflet insert dialog)
 * openAddMapDialog, closeMapInsertDialog, mapSearchPlace,
 * mapAddPin, mapRemovePin, mapUpdateGeodesic,
 * mapRenderPinList, mapRefreshMarkerLabel, insertMapFromDialog
 */

function openAddMapDialog() {
    const existing = document.getElementById('map-insert-dialog');
    if (existing) existing.remove();

    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    const dialog = document.createElement('div');
    dialog.id = 'map-insert-dialog';
    dialog.className = 'map-insert-overlay';
    dialog.innerHTML = `
        <div class="map-insert-box">
            <div class="map-insert-header">
                <h4 class="map-insert-title">${_t('map_modal.title', '🌎 Insert map')}</h4>
                <button class="map-insert-close" onclick="closeMapInsertDialog()">×</button>
            </div>
            <div class="map-insert-search">
                <input id="map-search-input" class="map-insert-search-input"
                       type="text" placeholder="${_t('map_modal.search_placeholder', 'Ort suchen…')}" autocomplete="off">
                <button class="map-insert-search-btn" onclick="mapSearchPlace()">&#8594;</button>
            </div>
            <div id="map-insert-leaflet" class="map-insert-leaflet"></div>
            <div id="map-insert-pins" class="map-insert-pins"></div>
            <div class="map-insert-footer">
                <input id="map-insert-title" class="map-insert-title-input"
                       type="text" placeholder="${_t('map_modal.map_title_placeholder', 'Kartentitel (optional)')}" maxlength="80">
                <div class="map-insert-actions">
                    <button class="separator-dialog-btn separator-dialog-cancel"
                            onclick="closeMapInsertDialog()">${_t('map_modal.btn_cancel', 'Abbrechen')}</button>
                    <button class="separator-dialog-btn separator-dialog-ok"
                            onclick="insertMapFromDialog()">${_t('map_modal.btn_insert', 'Insert')}</button>
                </div>
            </div>
        </div>`;

    dialog.addEventListener('click', (e) => { if (e.target === dialog) closeMapInsertDialog(); });
    document.body.appendChild(dialog);

    // Enter in search field
    document.getElementById('map-search-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') mapSearchPlace();
    });

    // Initialize Leaflet map
    setTimeout(() => {
        if (window._mapInsertMap) {
            window._mapInsertMap.remove();
            window._mapInsertMap = null;
        }
        window._mapInsertPins = [];
        window._mapInsertMarkers = [];

        const mapDiv = document.getElementById('map-insert-leaflet');
        const map = L.map(mapDiv, { zoomControl: true, scrollWheelZoom: true });
        L.tileLayer('https://tile.openstreetmap.de/{z}/{x}/{y}.png', {
            attribution: '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> · Kacheln: FOSSGIS e.V.', maxZoom: 19
        }).addTo(map);
        map.setView([48.0, 10.0], 5);
        window._mapInsertMap = map;
        window._mapInsertGeodesic = null;

        // Click on map = drop pin
        map.on('click', (e) => {
            mapAddPin(e.latlng.lat, e.latlng.lng, '');
        });

        map.invalidateSize();
    }, 100);
}

function closeMapInsertDialog() {
    if (window._mapInsertMap) { window._mapInsertMap.remove(); window._mapInsertMap = null; }
    window._mapInsertPins = [];
    window._mapInsertMarkers = [];
    document.getElementById('map-insert-dialog')?.remove();
}

async function mapSearchPlace() {
    const query = document.getElementById('map-search-input').value.trim();
    if (!query) return;
    const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
    const lang = (window.MPD_I18N ? window.MPD_I18N.getLang() : 'de');
    try {
        const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(query)}&limit=1`, {
            headers: { 'Accept-Language': lang }
        });
        const data = await res.json();
        if (!data.length) { window.mpdToast(_t('map_modal.alert_not_found', null, 'Ort nicht gefunden.')); return; }
        const r = data[0];
        mapAddPin(parseFloat(r.lat), parseFloat(r.lon), r.display_name.split(',')[0]);
        window._mapInsertMap.setView([r.lat, r.lon], 10);
        document.getElementById('map-search-input').value = '';
    } catch (e) {
        window.mpdToast(_t('map_modal.alert_search_failed', { msg: e.message }, 'Suche fehlgeschlagen: ' + e.message), { duration: 4000 });
    }
}

function mapAddPin(lat, lon, label) {
    if (!window._mapInsertPins) window._mapInsertPins = [];
    if (!window._mapInsertMarkers) window._mapInsertMarkers = [];

    const pin = { lat: Math.round(lat * 1e6) / 1e6, lon: Math.round(lon * 1e6) / 1e6, label };
    window._mapInsertPins.push(pin);

    const marker = L.marker([lat, lon]).addTo(window._mapInsertMap);
    if (label) marker.bindTooltip(label, { permanent: true, direction: 'top', offset: [0, -10] });
    window._mapInsertMarkers.push(marker);

    mapUpdateGeodesic();
    mapRenderPinList();
}

function mapRemovePin(idx) {
    window._mapInsertPins.splice(idx, 1);
    const marker = window._mapInsertMarkers.splice(idx, 1)[0];
    if (marker) window._mapInsertMap.removeLayer(marker);
    mapUpdateGeodesic();
    mapRenderPinList();
}

function mapUpdateGeodesic() {
    const map = window._mapInsertMap;
    if (!map) return;
    if (window._mapInsertGeodesic) { map.removeLayer(window._mapInsertGeodesic); window._mapInsertGeodesic = null; }
    const pins = window._mapInsertPins;
    if (pins.length >= 2) {
        const latlngs = pins.map(p => [p.lat, p.lon]);
        if (typeof L.Geodesic !== 'undefined') {
            window._mapInsertGeodesic = new L.Geodesic(latlngs, { weight: 2, color: '#2E75B6', opacity: 0.7 }).addTo(map);
        } else {
            window._mapInsertGeodesic = L.polyline(latlngs, { weight: 2, color: '#2E75B6', opacity: 0.7 }).addTo(map);
        }
    }
}

function mapRenderPinList() {
    const container = document.getElementById('map-insert-pins');
    if (!container) return;
    container.innerHTML = '';
    (window._mapInsertPins || []).forEach((pin, i) => {
        const row = document.createElement('div');
        row.className = 'map-pin-row';
        row.innerHTML = `
            <span class="map-pin-num">${i + 1}</span>
            <input class="map-pin-label-input" type="text" placeholder="${(window.MPD_I18N ? window.MPD_I18N.t('map_modal.pin_label_placeholder') : 'Label (optional)')}"
                   value="${pin.label || ''}"
                   onchange="window._mapInsertPins[${i}].label = this.value; mapRefreshMarkerLabel(${i})">
            <span class="map-pin-coords">${pin.lat}, ${pin.lon}</span>
            <button class="map-pin-remove" onclick="mapRemovePin(${i})">×</button>`;
        container.appendChild(row);
    });
}

function mapRefreshMarkerLabel(idx) {
    const marker = window._mapInsertMarkers[idx];
    const pin    = window._mapInsertPins[idx];
    if (!marker) return;
    marker.unbindTooltip();
    if (pin.label) marker.bindTooltip(pin.label, { permanent: true, direction: 'top', offset: [0, -10] });
}

function insertMapFromDialog() {
    const pins = (window._mapInsertPins || []);
    if (!pins.length) { window.mpdToast(window.MPD_I18N ? window.MPD_I18N.t('map_modal.alert_no_pin') : 'Mindestens 1 Pin setzen.'); return; }

    const title = document.getElementById('map-insert-title')?.value.trim() || '';
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
    const newElem = { id: newId, type: 'map', pins: pins.map(p => ({
        lat: p.lat, lon: p.lon, ...(p.label ? { label: p.label } : {})
    })) };
    if (title) newElem.title = title;

    closeMapInsertDialog();
    pushUndoState();
    albumData.elements.splice(insertAfterIndex + 1, 0, newElem);
    hasUnsavedChanges = true;
    const _sy = window.scrollY;
    enableEditMode();
    requestAnimationFrame(() => {
        window.scrollTo(0, _sy);
        scheduleAutoSave();
        const el = document.querySelector(`.map-element[data-id="${newId}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
}
