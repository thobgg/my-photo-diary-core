/* MPD – Album rendering
 * renderAlbum, createPhotoItem, renderSinglePhoto, renderPhotoRow,
 * calcJustifiedSizes, applyJustifiedSizes, initLazyLoad,
 * lightbox wrapper, goBack, showError, ESC handler
 */

// Lock-icon SVG for the element toggle buttons. Same shape vocabulary as
// the header locked-toggle (album.html): outline-unlock = element is public,
// filled-lock = element is private. Fill-as-state, no emoji look.
function _lockIconSvg(isLocked) {
    const stroke = 'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    if (isLocked) {
        return `<svg viewBox="0 0 24 24" ${stroke}>
            <rect x="3" y="11" width="18" height="11" rx="2" fill="currentColor"/>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
        </svg>`;
    }
    return `<svg viewBox="0 0 24 24" ${stroke}>
        <rect x="3" y="11" width="18" height="11" rx="2"/>
        <path d="M7 11V7a5 5 0 0 1 9.9-1"/>
    </svg>`;
}

// Extract date from filename — only ISO order (YYYY-MM-DD / YYYYMMDD) at the
// start, optionally after a known camera prefix. Better "no badge" than "wrong badge":
// a DE-formatted foreign filename like "20-04-2026_..." would otherwise show "30.12.2026".
const _DATE_RE = /^(?:(?:IMG|VID|PXL|DSC|MVIMG|Screenshot|WhatsApp)[-_ ]?)?(\d{4})[-_]?(\d{2})[-_]?(\d{2})(?:[-_T ]?(\d{2})[-_:]?(\d{2}))?/;
function _dateFromFilename(fname) {
    if (!fname) return null;
    const m = _DATE_RE.exec(fname);
    if (!m) return null;
    const [, y, mo, d, h, mi] = m;
    const yn = +y, mon = +mo, dn = +d;
    if (yn < 1990 || yn > 2100) return null;
    if (mon < 1 || mon > 12 || dn < 1 || dn > 31) return null;
    const base = `${d}.${mo}.${y}`;
    return h && mi ? `${base} ${h}:${mi}` : base;
}

/* ── Externe Links ──────────────────────────────────────────────────
   Im Android-WebView-Wrapper oeffnet target="_blank" NICHTS: die APK setzt
   weder onCreateWindow noch setSupportMultipleWindows — per DEX-Strings der
   installierten APK geprueft, waehrend onPageFinished sehr wohl vorhanden
   ist. Der Klick verpufft dort still, genau wie bei nativen Dialogen.

   Im WebView laedt der Link deshalb in derselben Ansicht; die Zurueck-Taste
   fuehrt nach MPD zurueck (onBackPressed ist implementiert). Ueberall sonst
   bleibt es beim neuen Tab.

   Bekommt die APK spaeter shouldOverrideUrlLoading und reicht fremde Hosts
   per Intent an den Browser weiter, faengt sie den Aufruf ab — diese Loesung
   bleibt dann unveraendert gueltig. */
function mpdIsWebView() {
    return /;\s*wv\)/.test(navigator.userAgent || '');
}

function mpdSetExternalLink(a, url) {
    a.href = url;
    a.rel  = 'noopener noreferrer';
    if (!mpdIsWebView()) a.target = '_blank';
}

// PDX v1.5: Markdown-light — ausschließlich **fett** und *kursiv*, kein HTML.
// Bewusst ohne innerHTML: der Text wird tokenisiert und als Text-/<strong>/
// <em>-Knoten angehängt (kein XSS-Vektor); Zeilenumbrüche verhalten sich
// exakt wie bei textContent (white-space:pre-wrap greift auf Textknoten).
function mpdRenderInlineMarkup(container, text) {
    container.textContent = '';
    const parts = String(text || '').split(/(\*\*[^*]+\*\*|\*[^*\n]+\*)/g);
    for (const part of parts) {
        if (!part) continue;
        let node;
        if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) {
            node = document.createElement('strong');
            node.textContent = part.slice(2, -2);
        } else if (part.length > 2 && part.startsWith('*') && part.endsWith('*')) {
            node = document.createElement('em');
            node.textContent = part.slice(1, -1);
        } else {
            node = document.createTextNode(part);
        }
        container.appendChild(node);
    }
}

function renderAlbum(editMode = false) {
    const titleEl = document.getElementById('album-title');
    if (titleEl) titleEl.textContent = albumData.meta.title;
    const metaEl = document.getElementById('album-meta');
    if (metaEl) metaEl.textContent = albumData.meta.year || '';

    // Count media elements (photo + video) and text blocks separately.
    // "Elements" for the media total, so the label also covers videos
    // (the previous text "photos" was misleading when a video was included).
    const photos = albumData.elements.filter(e => e.type === 'photo' || e.type === 'video').length;
    const texts  = albumData.elements.filter(e => e.type === 'text').length;
    const countEl = document.getElementById('album-count');
    if (countEl) {
        const _i18n = window.MPD_I18N;
        const el = photos === 1
            ? (_i18n ? _i18n.t('album.count_one_element') : '1 Element')
            : (_i18n ? _i18n.t('album.count_many_elements', { count: photos }) : `${photos} Elemente`);
        const tx = texts === 1
            ? (_i18n ? _i18n.t('album.count_one_text') : '1 Text')
            : (_i18n ? _i18n.t('album.count_many_texts', { count: texts }) : `${texts} Texte`);
        countEl.textContent = `${el} · ${tx}`;
    }

    // Sync hero on mobile with the same data (CSS decides whether it's
    // visible at all — we just maintain content here).
    // Share viewer doesn't load album.js → check defensively.
    if (typeof syncAlbumHero === 'function') syncAlbumHero(photos);

    // Keep the locked-toggle state in sync across both UI surfaces:
    // desktop header (#locked-toggle) and mobile kebab (#album-kebab-locked).
    // Both rely on .is-locked as the CSS hook for icon and label swap.
    const _isLocked = !(albumData.meta.show_locked ?? true);
    document.getElementById('locked-toggle')?.classList.toggle('is-locked', _isLocked);
    document.getElementById('album-kebab-locked')?.classList.toggle('is-locked', _isLocked);

    const content = document.getElementById('content');
    content.innerHTML = '';
    allImages = [];

    // In edit mode: #content is flex-wrap so individual photo elements
    // flow next to each other and still look exactly like the justified grid.
    if (editMode) {
        content.classList.add('edit-grid-mode');
        content.style.display = '';
        content.style.flexWrap = '';
        content.style.gap = '';
        content.style.alignContent = '';
    } else {
        content.classList.remove('edit-grid-mode');
        content.style.display = '';
        content.style.flexWrap = '';
        content.style.gap = '';
        content.style.alignContent = '';
    }

    // Measure actual width via a temporary probe element
    // – works at every browser zoom level
    const containerWidth = content.getBoundingClientRect().width || window.innerWidth;
    const isMobile = containerWidth <= 768;
    const targetRowHeight = isMobile ? 200 : 400;
    const gap = 4;
    const columnsMobile = 2;

    // Edit mode: compute target sizes before render → no layout shift
    const editSizeMap = editMode ? calcJustifiedSizes(albumData.elements, containerWidth) : null;

    let currentRow = [];
    let currentRowWidth = 0;
    const fragment = document.createDocumentFragment();

    // Edit mode shows the owner everything (incl. locked elements) — so they
    // can unlock/edit at any time. The header key remains as a preview switch
    // for view mode ("this is how the recipient sees it").
    const showLocked = editMode || (albumData.meta.show_locked ?? true);

    // Share viewer (alpha): only shows the three basic types. Audio, video,
    // document, map, tour are deliberately omitted — saves the Leaflet dep
    // and modal logic. Will be removed as the share-viewer feature grows.
    const _SHARE_ALLOWED = new Set(['photo', 'text', 'separator', 'link']);

    albumData.elements.forEach((elem, idx) => {

        if (window.MPD_SHARE_MODE && !_SHARE_ALLOWED.has(elem.type)) return;

        if (elem.type === 'text') {
            // Skip locked elements when show_locked: false
            if (elem.locked === true && !showLocked) return;

            if (currentRow.length > 0) {
                renderPhotoRow(fragment, currentRow, targetRowHeight, isMobile, columnsMobile, editMode, containerWidth, editSizeMap);
                currentRow = [];
                currentRowWidth = 0;
            }

            const textBlock = document.createElement('div');
            const isLocked = elem.locked === true;
            const styleClass = elem.style ? ` text-style-${elem.style}` : '';
            const lockedClass = isLocked ? ' is-locked' : '';
            textBlock.className = `text-block draggable-element${styleClass}${lockedClass}`;
            textBlock.id        = 'element-' + elem.id;   // MPD Search anchor
            textBlock.dataset.id = elem.id;
            textBlock.dataset.index = idx;
            textBlock.dataset.type = 'text';
            if (editMode) {
                textBlock.style.cssText = 'width:100%!important;flex:0 0 100%!important;';
            }

            const inner = document.createElement('div');
            inner.className = 'text-inner';
            if (editMode) {
                // Roh-Text mit Sternchen: der Inline-Editor ist contenteditable
                // und speichert inner.textContent — gerendertes Markup würde
                // die Auszeichnung bei jedem Web-Edit zerstören.
                inner.textContent = elem.text;
            } else {
                mpdRenderInlineMarkup(inner, elem.text);
            }
            textBlock.appendChild(inner);

            // PDX v1.5: Quellenangabe unterm Zitat (nur Ansicht)
            if (!editMode && elem.style === 'quote' && elem.source) {
                const src = document.createElement('div');
                src.className = 'text-source';
                src.textContent = '— ' + elem.source;
                textBlock.appendChild(src);
            }

            fragment.appendChild(textBlock);

        } else if (elem.type === 'photo' || elem.type === 'video') {
            if (elem.locked === true && !showLocked) return;
            if (elem.error) {
                const errDiv = document.createElement('div');
                errDiv.className = 'photo-item photo-item--missing draggable-element';
                errDiv.dataset.id   = elem.id;
                errDiv.dataset.type = elem.type || 'photo';
                // Sicherheits-Fix 02.09.2026: error/file stammen aus album.json
                // (Editor-Eingaben) — als Text setzen, nie per innerHTML.
                const missing = document.createElement('div');
                missing.className = 'photo-missing-placeholder';
                missing.title = elem.error || '';
                const missingName = document.createElement('span');
                missingName.textContent = elem.file || '';
                missing.appendChild(missingName);
                errDiv.appendChild(missing);
                if (editMode) {
                    const rb = document.createElement('button');
                    rb.className = 'remove-photo-btn';
                    rb.innerHTML = '×';
                    rb.title = 'Entfernen';
                    rb.onclick = (e) => { e.stopPropagation(); removePhotoFromAlbum(elem.id, elem.file); };
                    errDiv.appendChild(rb);
                }
                fragment.appendChild(errDiv);
                return;
            }

            const isVideo = elem.type === 'video';
            if (!isVideo) allImages.push(elem);
            const aspectRatio = elem.resolution
                ? elem.resolution.width / elem.resolution.height
                : 1.5;
            const imageWidth = targetRowHeight * aspectRatio;

            currentRow.push({
                elem,
                width: imageWidth,
                index: isVideo ? -1 : allImages.length - 1,
                dataIndex: idx
            });
            currentRowWidth += imageWidth + gap;

            const nextElem = albumData.elements[idx + 1];
            const isLastBeforeText = !nextElem || nextElem.type === 'text';
            const rowFull = isMobile
                ? currentRow.length >= columnsMobile
                : currentRowWidth >= containerWidth - 100;

            if (isLastBeforeText && currentRow.length === 1) {
                renderSinglePhoto(fragment, currentRow[0], editMode, containerWidth, editSizeMap, isMobile);
                currentRow = [];
                currentRowWidth = 0;
            } else if (rowFull) {
                renderPhotoRow(fragment, currentRow, targetRowHeight, isMobile, columnsMobile, editMode, containerWidth, editSizeMap);
                currentRow = [];
                currentRowWidth = 0;
            }
        } else if (elem.type === 'document') {
            // Flush pending row
            if (currentRow.length > 0) {
                if (currentRow.length === 1) renderSinglePhoto(fragment, currentRow[0], editMode, containerWidth, editSizeMap, isMobile);
                else renderPhotoRow(fragment, currentRow, targetRowHeight, isMobile, columnsMobile, editMode, containerWidth, editSizeMap);
                currentRow = [];
                currentRowWidth = 0;
            }
            if (elem.locked === true && !showLocked) return;
            if (elem.error) {
                const errDiv = document.createElement('div');
                errDiv.className = 'photo-item photo-item--missing draggable-element';
                errDiv.dataset.id   = elem.id;
                errDiv.dataset.type = 'document';
                // Sicherheits-Fix 02.09.2026: error/file stammen aus album.json
                // (Editor-Eingaben) — als Text setzen, nie per innerHTML.
                const missing = document.createElement('div');
                missing.className = 'photo-missing-placeholder';
                missing.title = elem.error || '';
                const missingName = document.createElement('span');
                missingName.textContent = elem.file || '';
                missing.appendChild(missingName);
                errDiv.appendChild(missing);
                if (editMode) {
                    const rb = document.createElement('button');
                    rb.className = 'remove-photo-btn';
                    rb.innerHTML = '×';
                    rb.title = 'Entfernen';
                    rb.onclick = (e) => { e.stopPropagation(); removePhotoFromAlbum(elem.id, elem.file); };
                    errDiv.appendChild(rb);
                }
                fragment.appendChild(errDiv);
            } else {
                const isPdf  = elem.file && elem.file.toLowerCase().endsWith('.pdf');
                const istText = elem.file && /\.(txt|md)$/i.test(elem.file);
                const docWrap = document.createElement('div');
                const docLockedClass = elem.locked === true ? ' is-locked' : '';
                docWrap.className = `document-element draggable-element${docLockedClass}`;
                docWrap.dataset.id   = elem.id;
                docWrap.dataset.type = 'document';
                docWrap.dataset.index = idx;
                if (editMode) docWrap.style.cssText = 'width:100%!important;flex:0 0 100%!important;';

                /* Titel statt Dateiname (15.09.2026).
                   Das Feld gibt es in PDX laengst — "title: Anzeigetitel
                   ueber dem Element" —, es wurde nur nie gesetzt und stand
                   zusaetzlich zum Dateinamen da. Jetzt wie beim Tour-Titel:
                   im Bearbeiten-Modus antippen und ueberschreiben.
                   Im Ansichtsmodus bleibt die Zeile weg, wenn kein Titel
                   gesetzt ist — sonst staende dort der Dateiname doppelt. */
                /* Nur bei Bildern: Dort gibt es im Element keine Textzeile,
                   die man beschriften koennte. PDF und Textdatei tragen ihren
                   Titel in der eigenen Karte bzw. Klappzeile (15.09.2026). */
                if (!istText && !isPdf && (elem.title || editMode)) {
                    const titleEl = document.createElement('div');
                    titleEl.className = 'document-title';
                    titleEl.textContent = elem.title || '';
                    if (editMode) {
                        titleEl.contentEditable = 'true';
                        titleEl.dataset.leer = (window.MPD_I18N
                            ? window.MPD_I18N.t('element_actions.doc_title_placeholder')
                            : 'Titel vergeben …');
                        titleEl.title = (window.MPD_I18N
                            ? window.MPD_I18N.t('element_actions.edit_title') : 'Titel bearbeiten');
                        titleEl.addEventListener('blur', () => {
                            const neuerTitel = titleEl.textContent.trim();
                            const el = albumData.elements.find(e => e.id === elem.id);
                            if (el && neuerTitel !== (el.title || '')) {
                                pushUndoState();
                                el.title = neuerTitel || undefined;
                                hasUnsavedChanges = true;
                                scheduleAutoSave();
                            }
                        });
                        titleEl.addEventListener('keydown', (e) => {
                            if (e.key === 'Enter') { e.preventDefault(); titleEl.blur(); }
                        });
                    }
                    docWrap.appendChild(titleEl);
                }

                if (istText) {
                    /* Textdateien im Albumordner sichtbar machen (15.09.2026).
                       Zusammengeklappt steht der Titel, aufgeklappt der Text.
                       <details> ist die eingebaute Loesung: kein Skript,
                       tastaturbedienbar, funktioniert auch im WebView.

                       Der Inhalt wird aus der DATEI gelesen, nicht in die
                       album.json kopiert — sonst gaebe es zwei Wahrheiten,
                       und wer die Datei am Rechner aendert, saehe es im Album
                       nicht. Entschieden 15.09.2026. */
                    const box = document.createElement('details');
                    box.className = 'document-text';
                    const kopf = document.createElement('summary');
                    if (editMode) box.classList.add('bearbeiten');

                    /* Der Pfeil ist im Bearbeiten-Modus ein eigener Knopf.
                       Grund: Die Zeile daneben ist dann beschriftbar, und ein
                       Klick auf ein <summary> klappt IMMER auf — man kaeme
                       nie zum Schreiben. Also: Klick auf die Zeile aufhalten
                       (preventDefault), Klappen nur ueber den Pfeil.
                       Im Ansichtsmodus bleibt alles beim Alten, dort zeichnet
                       das CSS den Pfeil als ::before. */
                    if (editMode) {
                        const pfeil = document.createElement('span');
                        pfeil.className = 'document-text-klapp';
                        pfeil.textContent = '\u25B8';
                        pfeil.setAttribute('role', 'button');
                        pfeil.tabIndex = 0;
                        pfeil.title = (window.MPD_I18N
                            ? window.MPD_I18N.t('album.document_toggle') : 'Auf-/zuklappen');
                        pfeil.addEventListener('click', (e) => {
                            e.preventDefault(); e.stopPropagation();
                            box.open = !box.open;
                        });
                        kopf.appendChild(pfeil);
                        kopf.addEventListener('click', (e) => {
                            // Klick auf die Beschriftung setzt den Cursor,
                            // er soll nicht zusaetzlich klappen.
                            e.preventDefault();
                        });
                    }

                    const schild = document.createElement('span');
                    schild.className = 'document-text-schild';
                    schild.textContent = elem.title || '';
                    const titelFeld = editMode
                        ? _machTitelBeschriftbar(schild, elem, elem.file)
                        : null;
                    if (!editMode && !elem.title) schild.textContent = elem.file;
                    kopf.appendChild(schild);
                    box.appendChild(kopf);

                    const textUrl = `${API_BASE}/api/document/${encodeURIComponent(albumSpace)}/`
                                  + `${encodeURIComponent(elem.source_album || albumName)}/`
                                  + `${encodeURIComponent(elem.file)}`;

                    /* Ohne eigenen Titel steht bis zur Antwort der Dateiname
                       da, danach die erste Zeile. Wer gerade selbst tippt,
                       dem wird nichts ueberschrieben. */
                    if (!elem.title) {
                        _ersteZeileHolen(textUrl).then(zeile => {
                            if (!zeile) return;
                            if (titelFeld) titelFeld.setzeAuto(zeile);
                            else if (document.activeElement !== schild) {
                                schild.textContent = zeile;
                            }
                        }).catch(() => { /* bleibt der Dateiname */ });
                    }
                    const inhalt = document.createElement('pre');
                    inhalt.className = 'document-text-body';
                    inhalt.textContent = (window.MPD_I18N
                        ? window.MPD_I18N.t('album.document_loading') : 'Wird geladen …');
                    box.appendChild(inhalt);

                    /* Fusszeile: Hinweis auf Kuerzung (links) und der Verweis
                       ins Modal. Aufgeklappt im Album ist die Datei 60vh hoch
                       und auf 100 KB gedeckelt — wer wirklich lesen will,
                       braucht den ganzen Schirm. */
                    const fuss = document.createElement('div');
                    fuss.className = 'document-text-fuss';
                    const gross = document.createElement('button');
                    gross.type = 'button';
                    gross.className = 'document-text-gross';
                    gross.textContent = (window.MPD_I18N
                        ? window.MPD_I18N.t('album.document_open_large') : 'Groß öffnen');
                    gross.onclick = (e) => { e.stopPropagation(); openDocumentModal(elem); };
                    fuss.appendChild(gross);
                    box.appendChild(fuss);
                    docWrap.appendChild(box);

                    // Erst beim Aufklappen holen — ein Album mit zehn
                    // Textdateien soll nicht zehn Abrufe beim Oeffnen machen.
                    let geholt = false;
                    box.addEventListener('toggle', async () => {
                        if (!box.open || geholt) return;
                        geholt = true;
                        try {
                            const r = await fetch(textUrl, { credentials: 'same-origin' });
                            if (!r.ok) throw new Error('HTTP ' + r.status);
                            let text = await r.text();
                            // Deckel: eine Textdatei kann ein ganzes Buch sein.
                            const MAX = 100 * 1024;
                            let gekuerzt = false;
                            if (text.length > MAX) { text = text.slice(0, MAX); gekuerzt = true; }
                            inhalt.textContent = text;
                            if (gekuerzt) {
                                const rest = document.createElement('div');
                                rest.className = 'document-text-more';
                                rest.textContent = (window.MPD_I18N
                                    ? window.MPD_I18N.t('album.document_truncated')
                                    : 'Gekürzt — die vollständige Datei liegt im Albumordner.');
                                fuss.prepend(rest);
                            }
                        } catch (err) {
                            geholt = false;   // beim naechsten Aufklappen neu versuchen
                            inhalt.textContent = '⚠️ ' + err.message;
                        }
                    });
                } else if (isPdf) {
                    // PDF: preview card + tap → PDF reader modal
                    const pdfCard = document.createElement('div');
                    pdfCard.className = 'document-pdf-card';
                    const _tapOpen = (window.MPD_I18N ? window.MPD_I18N.t('album.document_tap_open') : 'Tap to open');
                    /* Als Knoten gebaut, nicht als HTML-Text: Der Titel kommt
                       aus der album.json, also aus Nutzereingabe. Fuer den
                       Dateinamen war das am 02.09.2026 schon einmal ein
                       Sicherheitsfix — dieselbe Falle nicht neu aufstellen. */
                    const pdfIcon = document.createElement('span');
                    pdfIcon.className = 'document-pdf-icon';
                    pdfIcon.textContent = '\u{1F4C4}';
                    const pdfName = document.createElement('span');
                    pdfName.className = 'document-pdf-name';
                    pdfName.textContent = elem.title || elem.file || '';
                    const pdfHint = document.createElement('span');
                    pdfHint.className = 'document-pdf-hint';
                    pdfHint.textContent = _tapOpen;
                    pdfCard.replaceChildren(pdfIcon, pdfName, pdfHint);
                    if (editMode) {
                        // Der Hinweis stimmt hier nicht — im Bearbeiten-Modus
                        // oeffnet ein Tippen nichts.
                        pdfHint.remove();
                        _machTitelBeschriftbar(pdfName, elem, elem.file || '');
                    } else {
                        pdfCard.onclick = () => openDocumentModal(elem);
                        pdfCard.style.cursor = 'pointer';
                    }
                    docWrap.appendChild(pdfCard);
                } else {
                    // JPG/PNG: inline like a photo
                    const img = document.createElement('img');
                    img.className = 'document-image';
                    img.alt = elem.title || elem.file;
                    img.decoding = 'async';
                    if (elem.thumbnails) {
                        img.src = `${API_BASE}${mpdThumbUrls(elem).xl || mpdThumbUrls(elem).m}`;
                    }
                    if (!editMode) img.onclick = () => openDocumentModal(elem);
                    docWrap.appendChild(img);
                }

                if (editMode) {
                    const removeBtn = document.createElement('button');
                    removeBtn.className = 'remove-photo-btn';
                    removeBtn.innerHTML = '×';
                    removeBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.remove_document') : 'Dokument entfernen');
                    removeBtn.onclick = (e) => { e.stopPropagation(); removePhotoFromAlbum(elem.id, null); };
                    docWrap.appendChild(removeBtn);

                    const lockedBtn = document.createElement('button');
                    const isLocked = elem.locked === true;
                    lockedBtn.className = 'locked-toggle-btn';
                    lockedBtn.title = isLocked ? 'Private' : 'Public';
                    lockedBtn.innerHTML = _lockIconSvg(isLocked);
                    lockedBtn.onclick = (e) => { e.stopPropagation(); toggleLocked(docWrap, lockedBtn); };
                    docWrap.appendChild(lockedBtn);
                }

                fragment.appendChild(docWrap);
            }

        } else if (elem.type === 'link') {
            // PDX v1.4.1: Web-Link als Chip — kompakt, ohne Kasten im
            // Fotofluss. Bewusst ohne Abruf der Zielseite: die NAS spricht
            // nie mit fremden Servern, und in geteilten Alben verraet
            // nichts den Besuch.
            if (currentRow.length > 0) {
                if (currentRow.length === 1) renderSinglePhoto(fragment, currentRow[0], editMode, containerWidth, editSizeMap, isMobile);
                else renderPhotoRow(fragment, currentRow, targetRowHeight, isMobile, columnsMobile, editMode, containerWidth, editSizeMap);
                currentRow = [];
                currentRowWidth = 0;
            }
            if (elem.locked === true && !showLocked) return;

            const linkLockedClass = elem.locked === true ? ' is-locked' : '';
            const wrap = document.createElement('div');
            wrap.className = `link-element draggable-element${linkLockedClass}`;
            wrap.dataset.id    = elem.id;
            wrap.dataset.type  = 'link';
            wrap.dataset.index = idx;
            if (editMode) wrap.style.cssText = 'width:100%!important;flex:0 0 100%!important;';

            // Domain als Herkunftsangabe — zeigt vor dem Tippen, wohin es geht.
            let host = '';
            try { host = new URL(elem.url).hostname.replace(/^www\./, ''); }
            catch (e) { host = elem.url || ''; }

            // Im Edit-Modus sitzen ×, Schloss und Stift in einer eigenen
            // Zeile ueber dem Chip. Ein 46px hoher Chip mit drei
            // ueberlagerten 28px-Knoepfen waere nicht mehr treffsicher.
            if (editMode) {
                const tools = document.createElement('div');
                tools.className = 'link-tools';

                const removeBtn = document.createElement('button');
                removeBtn.className = 'remove-photo-btn';
                removeBtn.innerHTML = '×';
                removeBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.remove_link') : 'Link entfernen');
                removeBtn.onclick = (e) => { e.stopPropagation(); removePhotoFromAlbum(elem.id, null); };
                tools.appendChild(removeBtn);

                const lockedBtn = document.createElement('button');
                const isLocked = elem.locked === true;
                lockedBtn.className = 'locked-toggle-btn';
                lockedBtn.title = isLocked ? 'Private' : 'Public';
                lockedBtn.innerHTML = _lockIconSvg(isLocked);
                lockedBtn.onclick = (e) => { e.stopPropagation(); toggleLocked(wrap, lockedBtn); };
                tools.appendChild(lockedBtn);

                const editBtn = document.createElement('button');
                editBtn.className = 'link-edit-btn';
                editBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.edit_link') : 'Link bearbeiten');
                editBtn.textContent = '✎';
                editBtn.onclick = (e) => { e.stopPropagation(); openAddLinkDialog(elem); };
                tools.appendChild(editBtn);

                wrap.appendChild(tools);
            }

            // Im Lesemodus ist der Chip ein <a>, im Editiermodus ein <div> —
            // sonst oeffnet jeder Griff zum Verschieben die Zielseite.
            const chip = document.createElement(editMode ? 'div' : 'a');
            chip.className = 'link-chip';
            if (!editMode) mpdSetExternalLink(chip, elem.url);

            const icon = document.createElement('span');
            icon.className = 'link-chip-icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';
            chip.appendChild(icon);

            // Titel und Domain in einer Spalte: bleibt bei langen Titeln
            // lesbar, statt die Domain in eine eigene Zeile zu druecken.
            const label = document.createElement('span');
            label.className = 'link-chip-label';

            const titleEl = document.createElement('span');
            titleEl.className = 'link-chip-title';
            titleEl.textContent = elem.title || host;
            label.appendChild(titleEl);

            if (elem.title) {
                const hostEl = document.createElement('span');
                hostEl.className = 'link-chip-host';
                hostEl.textContent = host;
                label.appendChild(hostEl);
            }

            chip.appendChild(label);
            wrap.appendChild(chip);

            // Die Notiz haette im Chip keinen Platz — sie steht darunter,
            // damit der Chip kompakt bleibt und das Feld trotzdem nutzbar ist.
            if (elem.note) {
                const noteEl = document.createElement('div');
                noteEl.className = 'link-note';
                noteEl.textContent = elem.note;
                wrap.appendChild(noteEl);
            }

            fragment.appendChild(wrap);

        } else if (elem.type === 'separator') {
            if (currentRow.length > 0) {
                if (currentRow.length === 1) renderSinglePhoto(fragment, currentRow[0], editMode, containerWidth, editSizeMap, isMobile);
                else renderPhotoRow(fragment, currentRow, targetRowHeight, isMobile, columnsMobile, editMode, containerWidth, editSizeMap);
                currentRow = [];
                currentRowWidth = 0;
            }
            if (elem.locked === true && !showLocked) return;
            const sep = document.createElement('div');
            const sepLockedClass = elem.locked === true ? ' is-locked' : '';
            const sepStyleClass  = elem.style  ? ` sep-style-${elem.style}` : '';
            const sepHasLabel    = elem.label  ? ' has-label' : '';
            sep.className = `separator-element draggable-element${sepLockedClass}${sepStyleClass}${sepHasLabel}`;
            sep.dataset.id    = elem.id;
            sep.dataset.type  = 'separator';
            sep.dataset.index = idx;
            if (editMode) sep.style.cssText = 'width:100%!important;flex:0 0 100%!important;';
            const lineL = document.createElement('div');
            lineL.className = 'separator-line';
            sep.appendChild(lineL);
            if (editMode) {
                // Editable label input (always visible in edit mode)
                const labelInput = document.createElement('input');
                labelInput.type = 'text';
                labelInput.className = 'separator-label-edit';
                labelInput.value = elem.label || '';
                labelInput.placeholder = 'Label…';
                labelInput.maxLength = 80;
                labelInput.addEventListener('mousedown', (e) => e.stopPropagation());
                labelInput.addEventListener('touchstart', (e) => e.stopPropagation(), { passive: true });
                labelInput.addEventListener('change', () => {
                    pushUndoState();
                    const idx2 = albumData.elements.findIndex(e => e.id === elem.id);
                    if (idx2 !== -1) {
                        const val = labelInput.value.trim();
                        if (val) albumData.elements[idx2].label = val;
                        else delete albumData.elements[idx2].label;
                        hasUnsavedChanges = true;
                        scheduleAutoSave();
                    }
                });
                sep.appendChild(labelInput);
                if (elem.label) {
                    const lineR = document.createElement('div');
                    lineR.className = 'separator-line';
                    sep.appendChild(lineR);
                }
            } else if (elem.label) {
                const lbl = document.createElement('span');
                lbl.className = 'separator-label';
                lbl.textContent = elem.label;
                sep.appendChild(lbl);
                const lineR = document.createElement('div');
                lineR.className = 'separator-line';
                sep.appendChild(lineR);
            }
            if (editMode) {
                const removeBtn = document.createElement('button');
                removeBtn.className = 'remove-photo-btn';
                removeBtn.innerHTML = '×';
                removeBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.remove_separator') : 'Trenner entfernen');
                removeBtn.onclick = (e) => { e.stopPropagation(); removePhotoFromAlbum(elem.id, null); };
                sep.appendChild(removeBtn);

                const lockedBtn = document.createElement('button');
                const isLocked = elem.locked === true;
                lockedBtn.className = 'locked-toggle-btn';
                lockedBtn.title = isLocked ? 'Private' : 'Public';
                lockedBtn.innerHTML = _lockIconSvg(isLocked);
                lockedBtn.onclick = (e) => { e.stopPropagation(); toggleLocked(sep, lockedBtn); };
                sep.appendChild(lockedBtn);

                const sepStyleBtn = document.createElement('button');
                sepStyleBtn.className = 'sep-style-btn';
                sepStyleBtn.title = 'Separator-Stil';
                sepStyleBtn.textContent = _sepStyleChar(elem.style || 'line');
                sepStyleBtn.onclick = (e) => { e.stopPropagation(); openSepStyleDropdown(elem, sepStyleBtn, sep); };
                sepStyleBtn.addEventListener('touchstart', (e) => e.stopPropagation(), { passive: false });
                sep.appendChild(sepStyleBtn);
            }

            fragment.appendChild(sep);

        } else if (elem.type === 'audio') {
            if (currentRow.length > 0) {
                if (currentRow.length === 1) renderSinglePhoto(fragment, currentRow[0], editMode, containerWidth, editSizeMap, isMobile);
                else renderPhotoRow(fragment, currentRow, targetRowHeight, isMobile, columnsMobile, editMode, containerWidth, editSizeMap);
                currentRow = [];
                currentRowWidth = 0;
            }
            if (elem.locked === true && !showLocked) return;
            const audioWrap = document.createElement('div');
            const audioLockedClass = elem.locked === true ? ' is-locked' : '';
            audioWrap.className = `audio-element draggable-element${audioLockedClass}`;
            audioWrap.dataset.id    = elem.id;
            audioWrap.dataset.type  = 'audio';
            audioWrap.dataset.index = idx;
            if (editMode) audioWrap.style.cssText = 'width:100%!important;flex:0 0 100%!important;';

            if (elem.title) {
                const titleEl = document.createElement('div');
                titleEl.className = 'audio-title';
                titleEl.textContent = elem.title;
                audioWrap.appendChild(titleEl);
            }

            const player = document.createElement('audio');
            player.controls = true;
            player.preload = 'none';
            player.className = 'audio-player';
            player.src = `${API_BASE}/api/audio/${encodeURIComponent(albumSpace)}/${encodeURIComponent(elem.source_album || albumName)}/${encodeURIComponent(elem.file)}`;
            audioWrap.appendChild(player);

            if (editMode) {
                const removeBtn = document.createElement('button');
                removeBtn.className = 'remove-photo-btn';
                removeBtn.innerHTML = '×';
                removeBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.remove_audio') : 'Audio entfernen');
                removeBtn.onclick = (e) => { e.stopPropagation(); removePhotoFromAlbum(elem.id, null); };
                audioWrap.appendChild(removeBtn);

                const lockedBtn = document.createElement('button');
                const isLocked = elem.locked === true;
                lockedBtn.className = 'locked-toggle-btn';
                lockedBtn.title = isLocked ? 'Private' : 'Public';
                lockedBtn.innerHTML = _lockIconSvg(isLocked);
                lockedBtn.onclick = (e) => { e.stopPropagation(); toggleLocked(audioWrap, lockedBtn); };
                audioWrap.appendChild(lockedBtn);
            }

            fragment.appendChild(audioWrap);

        } else if (elem.type === 'map') {
            if (currentRow.length > 0) {
                if (currentRow.length === 1) renderSinglePhoto(fragment, currentRow[0], editMode, containerWidth, editSizeMap, isMobile);
                else renderPhotoRow(fragment, currentRow, targetRowHeight, isMobile, columnsMobile, editMode, containerWidth, editSizeMap);
                currentRow = [];
                currentRowWidth = 0;
            }
            if (elem.locked === true && !showLocked) return;
            const mapWrap = document.createElement('div');
            const mapLockedClass = elem.locked === true ? ' is-locked' : '';
            mapWrap.className = `map-element draggable-element${mapLockedClass}`;
            mapWrap.dataset.id    = elem.id;
            mapWrap.dataset.type  = 'map';
            mapWrap.dataset.index = idx;
            if (editMode) mapWrap.style.cssText = 'width:100%!important;flex:0 0 100%!important;';

            if (elem.title) {
                const titleEl = document.createElement('div');
                titleEl.className = 'map-element-title';
                titleEl.textContent = elem.title;
                mapWrap.appendChild(titleEl);
            }

            const mapDiv = document.createElement('div');
            mapDiv.className = 'map-element-leaflet';
            mapDiv.id = 'map-' + elem.id;
            mapWrap.appendChild(mapDiv);

            if (editMode) {
                const removeBtn = document.createElement('button');
                removeBtn.className = 'remove-photo-btn map-remove-overlay';
                removeBtn.innerHTML = '×';
                removeBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.remove_map') : 'Karte entfernen');
                removeBtn.onclick = (e) => { e.stopPropagation(); removePhotoFromAlbum(elem.id, null); };
                mapDiv.appendChild(removeBtn);

                const lockedBtn = document.createElement('button');
                const isLocked = elem.locked === true;
                lockedBtn.className = 'locked-toggle-btn';
                lockedBtn.title = isLocked ? 'Private' : 'Public';
                lockedBtn.innerHTML = _lockIconSvg(isLocked);
                lockedBtn.onclick = (e) => { e.stopPropagation(); toggleLocked(mapWrap, lockedBtn); };
                mapWrap.appendChild(lockedBtn);
            }

            fragment.appendChild(mapWrap);

            requestAnimationFrame(() => {
                const pins = elem.pins || [];
                if (!pins.length) return;
                const div = document.getElementById('map-' + elem.id);
                if (!div || div._leaflet_id) return;

                const map = L.map(div, { zoomControl: true, scrollWheelZoom: false });

                // Zwei Kartengrundlagen wie beim Tour-Element: CARTO Voyager
                // (kleinmaßstäblich, Default) ↔ OpenTopoMap (großmaßstäblich,
                // topografisch). Umschaltbar — auch im Viewer. Wahl wird im
                // Edit-Mode in album.json (elem.layer) gemerkt.
                let _mapLayerKind = (elem.layer === 'topo') ? 'topo' : 'osm';
                const _tileFor = (kind) => kind === 'topo'
                    ? L.tileLayer(_TOPO_LAYER, { attribution: _ATTR_TOPO, maxZoom: 17 })
                    : L.tileLayer(_OSM_LAYER,  { attribution: _ATTR_OSM,  maxZoom: 19 });
                let _mapTile = _tileFor(_mapLayerKind).addTo(map);

                const layerBtn = document.createElement('button');
                layerBtn.type = 'button';
                layerBtn.className = 'tour-layer-btn map-layer-overlay';
                layerBtn.textContent = _mapLayerKind === 'topo' ? '🏔️' : '🗺️';
                layerBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.switch_layer') : 'Kartenlayer wechseln');
                layerBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    _mapLayerKind = _mapLayerKind === 'osm' ? 'topo' : 'osm';
                    map.removeLayer(_mapTile);
                    _mapTile = _tileFor(_mapLayerKind).addTo(map);
                    layerBtn.textContent = _mapLayerKind === 'topo' ? '🏔️' : '🗺️';
                    if (typeof isEditMode !== 'undefined' && isEditMode) {
                        elem.layer = _mapLayerKind;                       // Wahl persistieren
                        if (typeof hasUnsavedChanges !== 'undefined') hasUnsavedChanges = true;
                        if (typeof scheduleAutoSave === 'function') scheduleAutoSave();
                    }
                });
                div.appendChild(layerBtn);

                const latlngs = pins.map(p => [p.lat, p.lon]);

                // Numbered custom pins
                pins.forEach((p, i) => {
                    const icon = L.divIcon({
                        className: '',
                        html: `<div class="map-pin-icon">${i + 1}</div>`,
                        iconSize: [26, 26],
                        iconAnchor: [13, 26],
                        tooltipAnchor: [0, -28],
                    });
                    const marker = L.marker([p.lat, p.lon], { icon }).addTo(map);
                    if (p.label) marker.bindTooltip(p.label, {
                        permanent: true, direction: 'top',
                        className: 'map-label-tooltip'
                    });
                });

                // Geschwungene Verbindungslinie (Flugkarten-Bogen) statt gerader
                // Linie. Der Bogen wird im PIXEL-Raum gerechnet: Kontrollpunkt
                // senkrecht zur Sehne, Offset = Anteil der Pixel-Distanz (BOW).
                // Dadurch behält er bei jedem Zoom dieselbe optische Proportion;
                // alle Segmente wölben in dieselbe Richtung → harmonischer Pfad.
                let _arcLayer = null;
                const _buildArc = () => {
                    if (pins.length < 2) return;
                    const BOW = 0.18, SAMPLES = 24;
                    const pts = [];
                    for (let s = 0; s < latlngs.length - 1; s++) {
                        const p0 = map.latLngToContainerPoint(latlngs[s]);
                        const p1 = map.latLngToContainerPoint(latlngs[s + 1]);
                        const dx = p1.x - p0.x, dy = p1.y - p0.y;
                        const len = Math.hypot(dx, dy) || 1;
                        const nx = -dy / len, ny = dx / len;              // Normale zur Sehne
                        const mx = (p0.x + p1.x) / 2, my = (p0.y + p1.y) / 2;
                        const cx = mx + nx * len * BOW, cy = my + ny * len * BOW;  // Kontrollpunkt
                        for (let i = (s === 0 ? 0 : 1); i <= SAMPLES; i++) {       // Naht-Dopplung vermeiden
                            const t = i / SAMPLES, u = 1 - t;
                            pts.push(map.containerPointToLatLng([
                                u * u * p0.x + 2 * u * t * cx + t * t * p1.x,
                                u * u * p0.y + 2 * u * t * cy + t * t * p1.y,
                            ]));
                        }
                    }
                    if (_arcLayer) map.removeLayer(_arcLayer);
                    _arcLayer = L.polyline(pts, { weight: 3, color: '#2E75B6', opacity: 0.85, lineCap: 'round', lineJoin: 'round' }).addTo(map);
                };

                if (pins.length === 1) {
                    map.setView(latlngs[0], 12);
                } else {
                    map.fitBounds(L.latLngBounds(latlngs), { padding: [48, 48] });
                }
                _buildArc();                                  // initial nach fitBounds
                map.on('zoomend', _buildArc);                 // Pixel-Geometrie ändert sich nur beim Zoom
                setTimeout(() => { map.invalidateSize(); _buildArc(); }, 350);
            });


        } else if (elem.type === 'tour') {
            if (currentRow.length > 0) {
                if (currentRow.length === 1) renderSinglePhoto(fragment, currentRow[0], editMode, containerWidth, editSizeMap, isMobile);
                else renderPhotoRow(fragment, currentRow, targetRowHeight, isMobile, columnsMobile, editMode, containerWidth, editSizeMap);
                currentRow = [];
                currentRowWidth = 0;
            }
            if (elem.locked === true && !showLocked) return;

            const tourWrap = document.createElement('div');
            const tourLockedClass = elem.locked === true ? ' is-locked' : '';
            tourWrap.className = `tour-element draggable-element${tourLockedClass}`;
            tourWrap.dataset.id    = elem.id;
            tourWrap.dataset.type  = 'tour';
            tourWrap.dataset.index = idx;
            tourWrap.id = 'element-' + elem.id;
            if (editMode) tourWrap.style.cssText = 'width:100%!important;flex:0 0 100%!important;';

            /* Zwei Ebenen seit 15.09.2026, und zwar aus einem konkreten
               Fehler: Das Element bekommt im Bearbeiten-Modus
               `flex: 0 0 100%`, damit es eine Zeile fuer sich hat. Ein
               max-width darauf kappt genau diese Breite — fuer die
               Zeilenaufteilung zaehlt die gekappte Groesse, und ploetzlich
               passte ein Foto neben eine schmale hochkante Tour.

               Deshalb: aussen der Rahmen, der die Zeile belegt (und
               dataset, id und das Ziehen traegt), innen die Karte, die
               schmal werden darf. */
            const tourCard = document.createElement('div');
            tourCard.className = 'tour-card';
            tourWrap.appendChild(tourCard);

            if (elem.error) {
                // Sicherheits-Fix 02.09.2026: file als Text setzen, kein innerHTML.
                const tourErr = document.createElement('div');
                tourErr.className = 'tour-error';
                tourErr.textContent = '⚠️ GPX-Datei nicht gefunden: ';
                const tourErrName = document.createElement('em');
                tourErrName.textContent = elem.file || '';
                tourErr.appendChild(tourErrName);
                tourCard.appendChild(tourErr);
                if (editMode) {
                    const removeBtn = document.createElement('button');
                    removeBtn.className = 'remove-photo-btn map-remove-overlay';
                    removeBtn.innerHTML = '×';
                    removeBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.remove_tour') : 'Tour entfernen');
                    removeBtn.onclick = (e) => { e.stopPropagation(); removePhotoFromAlbum(elem.id, null); };
                    tourCard.appendChild(removeBtn);

                    const lockedBtn = document.createElement('button');
                    const isLocked = elem.locked === true;
                    lockedBtn.className = 'locked-toggle-btn';
                    lockedBtn.title = isLocked ? 'Private' : 'Public';
                    lockedBtn.innerHTML = _lockIconSvg(isLocked);
                    lockedBtn.onclick = (e) => { e.stopPropagation(); toggleLocked(tourWrap, lockedBtn); };
                    tourCard.appendChild(lockedBtn);
                }
                fragment.appendChild(tourWrap);
            } else {
                // Header
                const tourHeader = document.createElement('div');
                tourHeader.className = 'tour-header';

                const tourIcon = document.createElement('span');
                tourIcon.className = 'tour-icon';
                tourIcon.textContent = _tourIcon(elem.type_hint);
                tourHeader.appendChild(tourIcon);

                const tourTitle = document.createElement('span');
                tourTitle.className = 'tour-title';
                tourTitle.textContent = elem.title || elem.file || 'Tour';
                if (editMode) {
                    tourTitle.contentEditable = 'true';
                    tourTitle.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.edit_title') : 'Titel bearbeiten');
                    tourTitle.addEventListener('blur', () => {
                        const newTitle = tourTitle.textContent.trim();
                        const el = albumData.elements.find(e => e.id === elem.id);
                        if (el && newTitle !== (el.title || '')) {
                            pushUndoState();
                            el.title = newTitle || undefined;
                            hasUnsavedChanges = true;
                            scheduleAutoSave();
                        }
                    });
                    tourTitle.addEventListener('keydown', (e) => {
                        if (e.key === 'Enter') { e.preventDefault(); tourTitle.blur(); }
                    });
                }
                tourHeader.appendChild(tourTitle);

                // Layer toggle
                const layerBtn = document.createElement('button');
                layerBtn.className = 'tour-layer-btn';
                layerBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.switch_layer') : 'Kartenlayer wechseln');
                layerBtn.textContent = '🗺️';
                layerBtn.dataset.layer = 'osm';
                tourHeader.appendChild(layerBtn);

                /* Fotos der Tour (15.09.2026). Verborgen, bis feststeht, dass
                   die Spur Zeitstempel hat — ohne die gibt es nichts zu
                   filtern, und ein Knopf, der nichts tut, ist schlimmer als
                   keiner. Sichtbar macht ihn _initTourMap(). */
                const fotoBtn = document.createElement('button');
                fotoBtn.className = 'tour-layer-btn tour-foto-btn';
                fotoBtn.textContent = '🖼';
                fotoBtn.hidden = true;
                fotoBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.tour_photos') : 'Fotos dieser Tour');
                tourHeader.appendChild(fotoBtn);

                /* Vollbild (15.09.2026). Nur anbieten, wenn der Browser es
                   wirklich kann — im WebView ist es oft gesperrt, und ein
                   Knopf, der nichts tut, ist schlimmer als keiner.
                   Vollbild geht auf den ganzen Rahmen, nicht nur die Karte:
                   sonst waere der Knopf zum Beenden selbst nicht mehr zu
                   sehen und es bliebe nur Escape. */
                if (document.fullscreenEnabled) {
                    const fsBtn = document.createElement('button');
                    fsBtn.className = 'tour-layer-btn tour-fs-btn';
                    fsBtn.textContent = '⛶';
                    fsBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.fullscreen') : 'Vollbild');
                    fsBtn.onclick = (e) => {
                        e.stopPropagation();
                        if (document.fullscreenElement === tourWrap) document.exitFullscreen();
                        else tourWrap.requestFullscreen().catch(() => {
                            if (window.mpdToast) window.mpdToast(
                                (window.MPD_I18N ? window.MPD_I18N.t('element_actions.fullscreen_failed')
                                                 : 'Vollbild ist hier nicht möglich'), { duration: 2200 });
                        });
                    };
                    tourHeader.appendChild(fsBtn);
                }

                tourCard.appendChild(tourHeader);

                // Map
                const mapDiv = document.createElement('div');
                mapDiv.className = 'tour-map';
                mapDiv.id = 'tour-map-' + elem.id;
                tourCard.appendChild(mapDiv);

                // Stats bar (populated after GPX parse)
                const statsBar = document.createElement('div');
                statsBar.className = 'tour-stats';
                statsBar.id = 'tour-stats-' + elem.id;
                tourCard.appendChild(statsBar);

                // Elevation profile (populated after GPX parse, only if ele data is available)
                const elevDiv = document.createElement('div');
                elevDiv.className = 'tour-elevation hidden';
                elevDiv.id = 'tour-elev-' + elem.id;
                tourCard.appendChild(elevDiv);

                if (editMode) {
                    const removeBtn = document.createElement('button');
                    removeBtn.className = 'remove-photo-btn map-remove-overlay';
                    removeBtn.innerHTML = '×';
                    removeBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.remove_tour') : 'Tour entfernen');
                    removeBtn.onclick = (e) => { e.stopPropagation(); removePhotoFromAlbum(elem.id, null); };
                    mapDiv.appendChild(removeBtn);

                    const lockedBtn = document.createElement('button');
                    const isLocked = elem.locked === true;
                    lockedBtn.className = 'locked-toggle-btn';
                    lockedBtn.title = isLocked ? 'Private' : 'Public';
                    lockedBtn.innerHTML = _lockIconSvg(isLocked);
                    lockedBtn.onclick = (e) => { e.stopPropagation(); toggleLocked(tourWrap, lockedBtn); };
                    tourCard.appendChild(lockedBtn);
                }

                fragment.appendChild(tourWrap);

                // Load GPX + initialize map
                const gpxUrl = `${API_BASE}/api/gpx/${encodeURIComponent(albumSpace)}/${encodeURIComponent(elem.source_album || albumName)}/${encodeURIComponent(elem.file)}`;
                _initTourMap(elem.id, gpxUrl, mapDiv, statsBar, elevDiv, layerBtn,
                    fotoBtn, albumSpace, elem.source_album || albumName,
                    elem.map_layer || 'osm',
                    elem.show_elevation !== false);
            }

        // unknown types: skip gracefully (PDX forward compatibility)
        }
    });

    if (currentRow.length > 0) {
        if (currentRow.length === 1) renderSinglePhoto(fragment, currentRow[0], editMode, containerWidth, editSizeMap, isMobile);
        else renderPhotoRow(fragment, currentRow, targetRowHeight, isMobile, columnsMobile, editMode, containerWidth, editSizeMap);
    }

    content.appendChild(fragment);

    // View mode: start IntersectionObserver for controlled lazy-load.
    if (!editMode) {
        initLazyLoad();
    }
    // Quick-Jump-Bar in BEIDEN Modi — lange Alben brauchen den Schnell-Scroll
    // auch beim Bearbeiten. Der 28px-Streifen am rechten Rand ist frei: der
    // Edit-FAB sitzt bei right:28px direkt daneben (und z-index 1000 darüber).
    requestAnimationFrame(initScrollJumper);

    if (window.lightbox) {
        window.lightbox.init(allImages, albumName, albumSpace);
    }

    // MPD Search: jump to linked element (only on first render)
    if (!_anchorScrollDone) {
        const hash = window.location.hash;
        if (hash && hash.startsWith('#element-')) {
            _anchorScrollDone = true;
            requestAnimationFrame(() => {
                const target = document.querySelector(hash);
                if (target) {
                    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    target.classList.add('search-highlight');
                    setTimeout(() => target.classList.remove('search-highlight'), 2500);
                }
            });
        }
    }
}

// Thumbnail-Adressen fuer ein Element, das (noch) keine vom Server hat —
// frisch eingefuegt, vor dem naechsten Laden. Gleiches Schema wie
// media_urls() in deps.py, nur ohne ?v (Cache-Schluessel kennt nur der
// Server). Bis 08.09.2026 warf der Renderer hier einen TypeError und das
// Album stand nach „Foto einfuegen" leer.
function mpdThumbUrls(elem) {
    if (elem.thumbnails) return elem.thumbnails;
    const sp = encodeURIComponent(elem._space || albumSpace);
    const al = encodeURIComponent(elem.source_album || albumName);
    const f  = encodeURIComponent(elem.file || '');
    const base = `/api/thumbnail/${sp}/${al}/${f}?size=`;
    elem.thumbnails = { sm: base + 'sm', m: base + 'm', xl: base + 'xl', full: base + 'full' };
    if (!elem.original) elem.original = `/api/photo/${sp}/${al}/${f}/download`;
    return elem.thumbnails;
}

function createPhotoItem(item, isMobile, columnsMobile, targetHeight, lazy = false) {
    const photoItem = document.createElement('div');
    photoItem.className = 'photo-item' + (item.elem.locked === true ? ' is-locked' : '');
    photoItem.dataset.file = item.elem.file;
    photoItem.dataset.id = item.elem.id;
    photoItem.dataset.dataIndex = item.dataIndex;

    // Prepare BlurHash URL, applied below directly on the <img>
    const bhUrl = (item.elem.blurhash && window.MPDBlurHash)
        ? window.MPDBlurHash.toDataURL(item.elem.blurhash, 32, 32)
        : null;

    if (isMobile) {
        const ar = item.elem.resolution
            ? item.elem.resolution.width / item.elem.resolution.height
            : 1.5;
        photoItem.style.width = `calc(${100 / columnsMobile}% - 2px)`;
        photoItem.style.aspectRatio = ar;
        photoItem.style.flex = `0 1 calc(${100 / columnsMobile}% - 2px)`;
    } else if (targetHeight) {
        photoItem.style.width = `${item.width ?? targetHeight * 1.5}px`;
        photoItem.style.height = `${targetHeight}px`;
        photoItem.style.flex = `0 0 ${item.width ?? targetHeight * 1.5}px`;
    }

    photoItem.onclick = () => {
        if (!isEditMode) {
            if (item.elem.type === 'video') openVideoModal(item.elem);
            else openLightbox(item.index);
        }
    };

    const img = document.createElement('img');
    img.draggable = false;  // Prevents native browser drag that would block SortableJS
    const isRetina = window.devicePixelRatio > 1.5;
    const _th = mpdThumbUrls(item.elem);
    const size = isMobile
        ? (isRetina ? _th.m : _th.sm)
        : _th.m;
    const thumbUrl = `${API_BASE}${size}`;
    if (lazy) {
        // IntersectionObserver takes over loading – no src set
        img.dataset.lazySrc = thumbUrl;
        img.classList.add('lazy-pending');
    } else {
        img.src = thumbUrl;
        img.loading = 'lazy';   // Native browser lazy-load as fallback
    }
    img.alt = item.elem.file || 'Album image';
    img.decoding = 'async';

    // BlurHash as background DIRECTLY on the <img> — covers exactly the photo
    // area (no empty-space leak around object-fit: contain)
    if (bhUrl) {
        img.style.backgroundImage    = `url(${bhUrl})`;
        img.style.backgroundSize     = 'cover';
        img.style.backgroundPosition = 'center';
    }

    // Fade-in from BlurHash to real thumb (cross-fade via opacity).
    // After load the BlurHash background is removed so the BlurHash
    // doesn't bleed through any letterbox bars (object-fit: contain)
    // and look like a "wrong neighboring photo".
    const markLoaded = () => {
        img.classList.add('mpd-loaded');
        img.style.backgroundImage = '';
    };
    img.addEventListener('load', markLoaded);
    if (img.complete && img.naturalWidth) markLoaded();

    if (item.elem.type === 'video') {
        const fbUrl = `${API_BASE}/api/video-thumb/${encodeURIComponent(albumSpace)}/${encodeURIComponent(item.elem.source_album || albumName)}/${encodeURIComponent(item.elem.file)}`;
        img.onerror = () => {
            img.onerror = () => {
                img.onerror = null;
                img.style.display = 'none';
                if (!photoItem.querySelector('.video-thumb-ph')) {
                    const ph = document.createElement('div');
                    ph.className = 'video-thumb-ph';
                    ph.innerHTML = '<span>▶</span>';
                    photoItem.insertBefore(ph, photoItem.firstChild);
                }
            };
            img.src = fbUrl;
        };
    }

    photoItem.appendChild(img);

    // Play icon overlay for video elements
    if (item.elem.type === 'video') {
        const playIcon = document.createElement('div');
        playIcon.className = 'video-play-icon';
        playIcon.innerHTML = '▶';
        photoItem.appendChild(playIcon);
    }

    // Capture date from filename (only visible in edit mode via CSS)
    const capture = _dateFromFilename(item.elem.file);
    if (capture) {
        const badge = document.createElement('div');
        badge.className = 'photo-date-badge';
        badge.textContent = capture;
        photoItem.appendChild(badge);
    }

    // Remove button (only visible in edit mode via CSS)
    const removeBtn = document.createElement('button');
    removeBtn.className = 'remove-photo-btn';
    removeBtn.innerHTML = '×';
    removeBtn.title = `"${item.elem.file}" aus Album entfernen`;
    removeBtn.onclick = (e) => {
        e.stopPropagation();
        removePhotoFromAlbum(item.elem.id, item.elem.file);
    };
    photoItem.appendChild(removeBtn);

    // Lock toggle (only visible in edit mode via CSS)
    const lockedBtn = document.createElement('button');
    const isLocked = item.elem.locked === true;
    lockedBtn.className = 'locked-toggle-btn';
    lockedBtn.title = isLocked ? 'Private' : 'Public';
    lockedBtn.innerHTML = _lockIconSvg(isLocked);
    lockedBtn.onclick = (e) => { e.stopPropagation(); toggleLocked(photoItem, lockedBtn); };
    photoItem.appendChild(lockedBtn);

    // Physically delete file (only type=photo, not video)
    if (item.elem.type === 'photo') {
        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'delete-file-btn';
        deleteBtn.innerHTML = '🗑';
        deleteBtn.title = (window.MPD_I18N
            ? window.MPD_I18N.t('album.delete_file_tooltip', { file: item.elem.file })
            : `Delete "${item.elem.file}" permanently`);
        deleteBtn.onclick = (e) => {
            e.stopPropagation();
            deletePhotoFile(item.elem.id, item.elem.file);
        };
        photoItem.appendChild(deleteBtn);
    }

    return photoItem;
}

function renderSinglePhoto(container, item, editMode, containerWidth, sizeMap, isMobile) {
    if (editMode) {
        const ar = item.elem.resolution
            ? item.elem.resolution.width / item.elem.resolution.height
            : 1.5;
        const size   = sizeMap?.get(item.elem.id);

        const wrapper = document.createElement('div');
        wrapper.className = 'photo-grid draggable-element';
        wrapper.dataset.type = 'photo';
        wrapper.dataset.id = item.elem.id;

        let photoItemHeight;
        if (isMobile || size?.mobile) {
            wrapper.style.width       = '100%';
            wrapper.style.aspectRatio = `${ar}`;
            wrapper.style.flex        = '0 0 100%';
            wrapper.style.marginLeft  = '';
            wrapper.style.marginRight = '';
            photoItemHeight           = null;
        } else {
            const h = size?.height ?? 500;
            const w = size?.width  ?? Math.min(Math.floor(h * ar), containerWidth);
            wrapper.style.width       = `${w}px`;
            wrapper.style.height      = `${h}px`;
            wrapper.style.flex        = `0 0 ${w}px`;
            wrapper.style.flexShrink  = '0';
            wrapper.style.marginLeft  = 'auto';
            wrapper.style.marginRight = 'auto';
            photoItemHeight           = h;
        }

        const photoItem = createPhotoItem(item, false, 1, photoItemHeight);
        photoItem.style.width  = '100%';
        photoItem.style.height = photoItemHeight ? `${photoItemHeight}px` : '100%';
        photoItem.style.flex   = 'none';
        photoItem.querySelector('img').src = `${API_BASE}${mpdThumbUrls(item.elem).xl}`;

        wrapper.appendChild(photoItem);
        container.appendChild(wrapper);
    } else {
        const photoGrid = document.createElement('div');
        photoGrid.className = 'photo-grid draggable-element';
        photoGrid.dataset.type = 'photo-row';
        photoGrid.dataset.ids = JSON.stringify([item.elem.id]);
        photoGrid.style.width = '100%';

        const photoRow = document.createElement('div');
        photoRow.className = 'photo-row single-photo-row';
        photoRow.style.width = '100%';
        photoRow.style.display = 'flex';

        const ar = item.elem.resolution
            ? item.elem.resolution.width / item.elem.resolution.height
            : 1.5;
        const targetHeight = isMobile
            ? Math.round(containerWidth / ar)  // exact height from width + AR → no letterboxing
            : 500;

        const photoItem = createPhotoItem(item, false, 1, targetHeight);
        photoItem.style.height = `${targetHeight}px`;
        photoItem.style.flex = '1 0 0';
        photoItem.style.width = '0';
        photoItem.querySelector('img').src = `${API_BASE}${mpdThumbUrls(item.elem).xl}`;

        photoRow.appendChild(photoItem);
        photoGrid.appendChild(photoRow);
        container.appendChild(photoGrid);
    }
}

function renderPhotoRow(container, row, targetHeight, isMobile, columnsMobile, editMode, containerWidth, sizeMap) {
    if (editMode) {
        row.forEach(item => {
            const wrapper = document.createElement('div');
            wrapper.className = 'photo-grid draggable-element';
            wrapper.dataset.type = 'photo';
            wrapper.dataset.id = item.elem.id;

            const size = sizeMap?.get(item.elem.id);

            if (size?.width && size?.height) {
                /* Justified layout (desktop multi + mobile multi): exact dimensions,
                   guaranteed identical height within a row. */
                wrapper.style.width  = `${size.width}px`;
                wrapper.style.height = `${size.height}px`;
                wrapper.style.flex   = `0 0 ${size.width}px`;
            } else if (isMobile || size?.mobile) {
                /* Fallback — should only trigger for size=undefined. */
                const ar = item.elem.resolution
                    ? item.elem.resolution.width / item.elem.resolution.height
                    : 1.5;
                wrapper.style.width       = `calc(${100 / columnsMobile}% - 2px)`;
                wrapper.style.aspectRatio = `${ar}`;
                wrapper.style.flex        = `0 1 calc(${100 / columnsMobile}% - 2px)`;
            } else {
                wrapper.style.width  = `${item.width}px`;
                wrapper.style.height = `${targetHeight}px`;
                wrapper.style.flex   = `0 0 ${item.width}px`;
            }

            const itemHeight = size?.height ?? (isMobile ? null : targetHeight);
            const photoItem = createPhotoItem(item, isMobile, columnsMobile, itemHeight);
            if (itemHeight) {
                photoItem.style.width  = '100%';
                photoItem.style.height = `${itemHeight}px`;
                photoItem.style.flex   = 'none';
                photoItem.style.aspectRatio = '';
            } else if (isMobile) {
                /* Fallback for mobile without size: photo-item fills the wrapper */
                photoItem.style.width  = '100%';
                photoItem.style.height = '100%';
                photoItem.style.flex   = 'none';
                photoItem.style.aspectRatio = '';
            }

            wrapper.appendChild(photoItem);
            container.appendChild(wrapper);
        });
    } else {
        const photoGrid = document.createElement('div');
        photoGrid.className = 'photo-grid draggable-element';
        photoGrid.dataset.type = 'photo-row';
        photoGrid.dataset.ids = JSON.stringify(row.map(item => item.elem.id));

        const photoRow = document.createElement('div');
        photoRow.className = 'photo-row';

        if (containerWidth) {
            // Dynamic row height: no crop, exactly flush — desktop and mobile
            const sumAspectRatios = row.reduce((sum, item) => {
                const ar = item.elem.resolution
                    ? item.elem.resolution.width / item.elem.resolution.height
                    : 1.5;
                return sum + ar;
            }, 0);
            const totalGap = (row.length - 1) * 4;
            let adjustedHeight = (containerWidth - totalGap) / sumAspectRatios;

            // Row too empty: default height
            if (adjustedHeight > targetHeight * 1.5) adjustedHeight = targetHeight;

            row.forEach(item => {
                const ar = item.elem.resolution
                    ? item.elem.resolution.width / item.elem.resolution.height
                    : 1.5;
                const photoItem = createPhotoItem(item, false, 1, adjustedHeight, true);
                const scaledWidth = adjustedHeight * ar;
                photoItem.style.height = `${adjustedHeight}px`;
                photoItem.style.width = `${scaledWidth}px`;
                photoItem.style.flex = '0 0 auto';
                photoRow.appendChild(photoItem);
            });
        } else {
            row.forEach(item => {
                const photoItem = createPhotoItem(item, isMobile, columnsMobile, isMobile ? null : targetHeight, true);
                photoRow.appendChild(photoItem);
            });
        }

        photoGrid.appendChild(photoRow);
        container.appendChild(photoGrid);
    }
}

// ============================================
// Justified grid for edit mode
// Computes exactly the same row heights as view mode,
// returns a Map id → {width, height} (or {mobile, columns}).
// ============================================

function calcJustifiedSizes(elements, containerWidth) {
    const gap           = 8;   /* Edit mode: airier than view (4px),
                                  signals "in editing" */
    const targetHeight  = 400;
    const isMobile      = containerWidth <= 768;
    const colsMobile    = 2;
    const sizeMap       = new Map();

    let row = [], rowWidth = 0;

    function flushRow() {
        if (!row.length) return;
        if (isMobile) {
            if (row.length === 1) {
                /* Single-photo row on phone: full width, aspect-ratio
                   decides the height (renderSinglePhoto uses this). */
                sizeMap.set(row[0].id, { mobile: true, single: true, columns: colsMobile });
            } else {
                /* Multi-photo row on phone: true justified layout
                   like in view mode. All items in the same row share
                   the common height from sumAR → consistent rows. */
                const sumAR         = row.reduce((s, i) => s + i.ar, 0);
                const totalGap      = (row.length - 1) * gap;
                const mobileTargetH = 200;
                let   adjH          = (containerWidth - totalGap) / sumAR;
                if (adjH > mobileTargetH * 1.5) adjH = mobileTargetH;
                row.forEach(item => sizeMap.set(item.id, {
                    width:  Math.floor(adjH * item.ar),
                    height: Math.floor(adjH),
                    mobile: true
                }));
            }
        } else if (row.length === 1) {
            const h = 500;
            sizeMap.set(row[0].id, {
                width:  Math.min(Math.floor(h * row[0].ar), containerWidth - 20),
                height: h,
                single: true
            });
        } else {
            const sumAR    = row.reduce((s, i) => s + i.ar, 0);
            const totalGap = (row.length - 1) * gap;
            let   adjH     = (containerWidth - totalGap) / sumAR;
            if (adjH > targetHeight * 1.5) adjH = targetHeight;
            row.forEach(item => sizeMap.set(item.id, {
                width:  Math.floor(adjH * item.ar),
                height: Math.floor(adjH)
            }));
        }
        row = []; rowWidth = 0;
    }

    elements.forEach((elem, idx) => {
        if ((elem.type !== 'photo' && elem.type !== 'video') || elem.error) { flushRow(); return; }
        const ar = elem.resolution
            ? elem.resolution.width / elem.resolution.height : 1.5;
        row.push({ id: elem.id, ar });
        rowWidth += targetHeight * ar + gap;

        const nextElem       = elements[idx + 1];
        const isLastBefore   = !nextElem || nextElem.type === 'text';
        const rowFull        = isMobile
            ? row.length >= colsMobile
            : rowWidth >= containerWidth - 100;

        if (isLastBefore && row.length === 1 && !isMobile) flushRow();
        else if (rowFull) flushRow();
    });
    flushRow();
    return sizeMap;
}

function applyJustifiedSizes() {
    const content = document.getElementById('content');
    if (!content || !content.classList.contains('edit-grid-mode')) return;
    if (!albumData) return;

    const containerWidth = content.getBoundingClientRect().width || window.innerWidth;
    const sizeMap = calcJustifiedSizes(albumData.elements, containerWidth);

    content.querySelectorAll('.photo-grid.draggable-element[data-type="photo"]').forEach(el => {
        const size = sizeMap.get(el.dataset.id);
        if (!size) return;

        if (size.width && size.height) {
            /* Justified layout (desktop multi + single, mobile multi):
               exact dimensions, consistent across the row. */
            el.style.width       = `${size.width}px`;
            el.style.height      = `${size.height}px`;
            el.style.flex        = `0 0 ${size.width}px`;
            el.style.aspectRatio = '';
            if (size.single) {
                el.style.marginLeft  = 'auto';
                el.style.marginRight = 'auto';
            } else {
                el.style.marginLeft  = '';
                el.style.marginRight = '';
            }
            const photoItem = el.querySelector('.photo-item');
            if (photoItem) {
                photoItem.style.width       = '100%';
                photoItem.style.height      = `${size.height}px`;
                photoItem.style.flex        = 'none';
                photoItem.style.aspectRatio = '';
            }
        } else {
            /* Mobile single: full width, aspect-ratio determines height */
            const elem = albumData.elements.find(e => e.id === el.dataset.id);
            const ar   = (elem?.resolution)
                ? elem.resolution.width / elem.resolution.height
                : 1.5;
            el.style.width       = '100%';
            el.style.flex        = '0 0 100%';
            el.style.height      = '';
            el.style.aspectRatio = `${ar}`;
            el.style.marginLeft  = '';
            el.style.marginRight = '';
            delete el.dataset.single;
            const photoItem = el.querySelector('.photo-item');
            if (photoItem) {
                photoItem.style.width       = '100%';
                photoItem.style.height      = '100%';
                photoItem.style.flex        = 'none';
                photoItem.style.aspectRatio = '';
            }
        }
    });
}

// ============================================
// Lazy-load via IntersectionObserver (view mode)
// Active only for row photos; single photos and edit mode
// load immediately (lazy=false in createPhotoItem).
// ============================================

function initLazyLoad() {
    // Cleanly restart observer
    if (_lazyObserver) {
        _lazyObserver.disconnect();
        _lazyObserver = null;
    }

    // Inject CSS once: BlurHash placeholder stays visible until the real
    // thumb has loaded (opacity fade). Neutral background as a fallback
    // for photos without BlurHash.
    if (!document.getElementById('mpd-lazy-style')) {
        const s = document.createElement('style');
        s.id = 'mpd-lazy-style';
        s.textContent = [
            '.photo-item img{opacity:0;transition:opacity 350ms ease-in-out;}',
            '.photo-item img.mpd-loaded{opacity:1;}',
            'img.lazy-pending{background:var(--card-bg,#d8d8d8);}',
        ].join('');
        document.head.appendChild(s);
    }

    const imgs = document.querySelectorAll('#content img[data-lazy-src]');
    if (!imgs.length) return;

    _lazyObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            const img = entry.target;
            img.src = img.dataset.lazySrc;
            img.classList.remove('lazy-pending');
            _lazyObserver.unobserve(img);
        });
    }, {
        rootMargin: '1200px 0px',  // load 1200 px before viewport entry → appears instantly when scrolling
        threshold : 0,
    });

    imgs.forEach(img => _lazyObserver.observe(img));
}

// ============================================
// Lightbox wrapper (delegate to window.lightbox)
// ============================================

// Lightbox gets its own history entry so the back gesture pops it first
// before reaching the album sentinel. Without this, one back gesture in
// the lightbox consumed the only sentinel and left the history unprotected.
let _lbInHistory = false;

function openLightbox(index) {
    if (!_lbInHistory) {
        history.pushState({ mpdAlbum: 'lightbox' }, '');
        _lbInHistory = true;
    }
    window.lightbox.open(index);
}

function closeLightbox() {
    window.lightbox.close();
    if (_lbInHistory) {
        _lbInHistory = false;
        history.replaceState({ mpdAlbum: 'sentinel' }, '');
    }
}

function navigate(direction)  { window.lightbox.navigate(direction); }
function toggleSlideshow()    { window.lightbox.toggleSlideshow(); }
function toggleInfo()         { window.lightbox.toggleInfo(); }
function toggleZoom() {
    const lb = window.lightbox;
    if (!lb) return;
    /* Desktop mit Maus: Klick zoomt eine Stufe, wie bisher.
       Beruehrungsgeraete: der Tipp blendet die Bedienung um; gezoomt wird
       per Doppeltipp und Pinch. Sonst zoomt jedes versehentliche Antippen. */
    if (window.matchMedia('(hover: none) and (pointer: coarse)').matches) {
        lb.scheduleChromeToggle();
        return;
    }
    // Desktop: Nach dem Ziehen folgt ein click. Ohne diese Abfrage bekaeme
    // man bei jedem Verschieben im Zoom eine weitere Zoomstufe, statt sich
    // einfach durchs Bild zu bewegen. Das Flag setzt _onPointerMove, das
    // naechste Druecken raeumt es wieder ab.
    if (lb._mouse && lb._mouse.moved) {
        lb._mouse.moved = false;
        return;
    }
    lb.toggleZoom();
}

// ── Back-navigation guard ────────────────────────────────────────────────
// Layer 1 – pointer-events:none (iOS phantom clicks on DOM elements)
// Layer 2 – history sentinels + popstate (Android WebView system back gesture)
//   Album sentinel  : pushed on page load
//   Lightbox sentinel: pushed when lightbox opens (extra buffer)
//   popstate always re-pushes so repeated gestures are also caught.

let _orientNavTimer   = null;
let _lastOrientWidthR = window.innerWidth;

function _onOrientationChange() {
    document.querySelectorAll('#album-back-btn, .header-brand').forEach(el => {
        el.style.pointerEvents = 'none';
    });
    clearTimeout(_orientNavTimer);
    _orientNavTimer = setTimeout(() => {
        document.querySelectorAll('#album-back-btn, .header-brand').forEach(el => {
            el.style.pointerEvents = '';
        });
    }, 1200);
}

window.addEventListener('orientationchange', _onOrientationChange, { passive: true });
window.addEventListener('resize', () => {
    if (window.innerWidth !== _lastOrientWidthR) {
        _lastOrientWidthR = window.innerWidth;
        _onOrientationChange();
    }
}, { passive: true });

history.pushState({ mpdAlbum: 'sentinel' }, '');

window.addEventListener('popstate', () => {
    history.pushState({ mpdAlbum: 'sentinel' }, '');
    if (_lbInHistory || document.getElementById('lightbox')?.classList.contains('active')) {
        _lbInHistory = false;
        window.lightbox.close();
    }
});

function goBack() {
    try { localStorage.removeItem('mpd-resume'); } catch(e) {}

    if (isEditMode && hasUnsavedChanges) {
        const _msg = (window.MPD_I18N
            ? window.MPD_I18N.t('album.confirm_unsaved_leave')
            : 'Unsaved changes. Really leave?');
        if (!confirm(_msg)) return;
    }
    window.location.replace('/');
}

function _sepStyleChar(style) {
    return { line: '—', bold: '━', dashed: '╌', dots: '···', space: '↕' }[style] || '—';
}

// ============================================
// Quick Jump Bar — fast scroll for long albums (mobile/tablet)
// ============================================

let _jumpBarScrollCleanup = null;

function initScrollJumper() {
    // Remove previous scroll listener to avoid accumulation across re-renders
    if (_jumpBarScrollCleanup) { _jumpBarScrollCleanup(); _jumpBarScrollCleanup = null; }
    document.getElementById('mpd-jump-bar')?.remove();

    // Only on mobile/tablet and only when content is long enough
    if (window.innerWidth > 1024) return;
    if (document.documentElement.scrollHeight < window.innerHeight * 2.5) return;

    const bar   = document.createElement('div');
    bar.id      = 'mpd-jump-bar';
    const track = document.createElement('div');
    track.className = 'mpd-jb-track';
    const thumb = document.createElement('div');
    thumb.className = 'mpd-jb-thumb';
    bar.appendChild(track);
    bar.appendChild(thumb);
    document.body.appendChild(bar);

    const TRACK_TOP    = 56;   // px from top (clears the album header)
    const TRACK_BOTTOM = 24;   // px reserved at bottom

    function updateThumb() {
        const scrollableH = document.documentElement.scrollHeight - window.innerHeight;
        if (scrollableH <= 0) return;
        const progress = Math.max(0, Math.min(1, window.scrollY / scrollableH));
        const trackH   = window.innerHeight - TRACK_TOP - TRACK_BOTTOM;
        const thumbH   = Math.max(36, trackH * 0.08);
        thumb.style.height    = thumbH + 'px';
        thumb.style.transform = `translateY(${progress * (trackH - thumbH)}px)`;
    }

    let hideTimer = null;
    const showBar = () => {
        bar.style.opacity = '1';
        bar.style.pointerEvents = 'auto';
        clearTimeout(hideTimer);
        hideTimer = setTimeout(() => {
            bar.style.opacity = '0';
            bar.style.pointerEvents = 'none';
        }, 2500);
    };

    const _onScroll = () => { updateThumb(); showBar(); };
    window.addEventListener('scroll', _onScroll, { passive: true });
    _jumpBarScrollCleanup = () => window.removeEventListener('scroll', _onScroll);

    updateThumb();
    showBar();  // reveal briefly on load so the user discovers it

    // Drag
    let dragging = false, startY = 0, startProg = 0;

    thumb.addEventListener('pointerdown', (e) => {
        e.stopPropagation();
        dragging  = true;
        startY    = e.clientY;
        const scrollableH = document.documentElement.scrollHeight - window.innerHeight;
        startProg = scrollableH > 0 ? window.scrollY / scrollableH : 0;
        thumb.setPointerCapture(e.pointerId);
        clearTimeout(hideTimer);
        bar.style.opacity      = '1';
        bar.style.pointerEvents = 'auto';
    }, { passive: false });

    thumb.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        e.stopPropagation();
        const trackH   = window.innerHeight - TRACK_TOP - TRACK_BOTTOM;
        const thumbH   = thumb.offsetHeight;
        const maxY     = trackH - thumbH;
        if (maxY <= 0) return;
        const dy       = e.clientY - startY;
        const newProg  = Math.max(0, Math.min(1, startProg + dy / maxY));
        const scrollableH = document.documentElement.scrollHeight - window.innerHeight;
        window.scrollTo({ top: newProg * scrollableH, behavior: 'instant' });
    }, { passive: false });

    thumb.addEventListener('pointerup',     () => { dragging = false; showBar(); });
    thumb.addEventListener('pointercancel', () => { dragging = false; });
}

function showError(message) {
    const warnSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="width:1em;height:1em;vertical-align:-0.12em;margin-right:.4em"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
    document.getElementById('content').innerHTML =
        `<div class="error"><h2>${warnSvg}Fehler</h2><p>${message}</p></div>`;
}

// ============================================
// ESC handler
// ============================================

document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (document.getElementById('lightbox').classList.contains('active')) return;

    // PDX v1.4 mixed picker: check first — it's usually the most recent
    // modal. handleMixedPickerEsc handles stage transitions itself.
    if (document.getElementById('mpd-mixed-overlay') && typeof handleMixedPickerEsc === 'function') {
        if (handleMixedPickerEsc()) return;
    }

    if (document.getElementById('thumbnail-modal'))                                                    { closeThumbnailModal();    return; }
    if (document.getElementById('video-modal')?.classList.contains('active'))                          { closeVideoModal();        return; }
    if (document.getElementById('document-modal')?.classList.contains('active'))                       { closeDocumentModal();     return; }
    if (document.getElementById('add-photo-modal') &&
        !document.getElementById('add-photo-modal').classList.contains('hidden'))                      { closeAddPhotoModal();     return; }
    if (document.getElementById('add-document-modal') &&
        !document.getElementById('add-document-modal').classList.contains('hidden'))                   { closeAddDocumentModal();  return; }
    if (document.getElementById('add-audio-modal') &&
        !document.getElementById('add-audio-modal').classList.contains('hidden'))                      { closeAddAudioModal();     return; }
    if (document.getElementById('map-insert-dialog'))                                                  { closeMapInsertDialog();   return; }
    if (document.getElementById('separator-dialog'))                                                   { document.getElementById('separator-dialog').remove(); return; }

    if (isEditMode) {
        isEditMode = false;
        updateEditModeUI();
    } else {
        goBack();
    }
});

// ============================================
// Tour element helpers
// ============================================

function _tourIcon(typeHint) {
    const icons = { hiking: '🥾', cycling: '🚴', running: '🏃', swimming: '🏊', walking: '🚶' };
    return icons[typeHint] || '📍';
}

function _parseDuration(startIso, endIso) {
    if (!startIso || !endIso) return null;
    const diff = (new Date(endIso) - new Date(startIso)) / 1000;
    if (diff <= 0) return null;
    const h = Math.floor(diff / 3600);
    const m = Math.floor((diff % 3600) / 60);
    return h > 0 ? `${h}:${String(m).padStart(2,'0')} h` : `${m} min`;
}

function _parseGpxDesc(desc) {
    if (!desc) return {};
    const stats = {};
    const m = (key, rx) => { const r = rx.exec(desc); return r ? parseFloat(r[1]) : null; };
    const dist = m('dist', /Distanz:\s*([\d.,]+)\s*km/);
    if (dist !== null) stats.distance = dist.toFixed(1) + ' km';
    const zeit = desc.match(/Zeit:\s*(\d+)h\s*(\d+)min/);
    if (zeit) stats.duration = `${zeit[1]}:${zeit[2].padStart(2,'0')} h`;
    const maxH = m('maxH', /Höhe max:\s*([\d.,]+)\s*m/);
    if (maxH !== null) stats.elevation = Math.round(maxH) + ' m';
    const kcal = m('kcal', /Kalorien:\s*([\d.,]+)\s*kcal/);
    if (kcal !== null) stats.calories = Math.round(kcal) + ' kcal';
    const hr = m('hr', /Ø HR:\s*([\d.,]+)\s*bpm/);
    if (hr !== null) stats.hr = Math.round(hr) + ' bpm';
    return stats;
}

/* Eine Beschriftung im Element selbst zum Titelfeld machen (15.09.2026).
   Vorher stand der Titel in einer eigenen Zeile UEBER dem Dokument, mittig
   ueber die volle Albumbreite. Das war aus zwei Gruenden schlecht: Bei PDF
   und Textdatei stand die Beschriftung dadurch doppelt da, und ein leeres
   contenteditable hat keinen echten Inhalt — der Platzhalter ist nur
   gezeichnet, der Schreibcursor sprang deshalb an den linken Zeilenanfang,
   weit weg vom sichtbaren "Titel vergeben …".

   Jetzt wird die Zeile beschriftet, die ohnehin schon da steht.

   `autoStart` ist die abgeleitete Beschriftung (Dateiname, spaeter die
   erste Textzeile). Sie darf NICHT durch blosses Antippen zum echten Titel
   werden — daher die Markierung `data-auto`: beim Hineingehen leeren, beim
   Verlassen ohne Eingabe wiederherstellen, gespeichert wird nur Getipptes. */
function _machTitelBeschriftbar(knoten, elem, autoStart) {
    let auto = autoStart || '';
    if (!elem.title) {
        knoten.textContent = auto;
        knoten.dataset.auto = '1';
    }
    knoten.contentEditable = 'true';
    knoten.dataset.leer = (window.MPD_I18N
        ? window.MPD_I18N.t('element_actions.doc_title_placeholder')
        : 'Titel vergeben …');
    knoten.title = (window.MPD_I18N
        ? window.MPD_I18N.t('element_actions.edit_title') : 'Titel bearbeiten');
    knoten.addEventListener('click', (e) => e.stopPropagation());
    knoten.addEventListener('focus', () => {
        if (knoten.dataset.auto === '1') knoten.textContent = '';
    });
    knoten.addEventListener('blur', () => {
        const neuerTitel = knoten.textContent.trim();
        const el = albumData.elements.find(e => e.id === elem.id);
        if (el && neuerTitel !== (el.title || '')) {
            pushUndoState();
            el.title = neuerTitel || undefined;
            hasUnsavedChanges = true;
            scheduleAutoSave();
        }
        if (neuerTitel) {
            delete knoten.dataset.auto;
        } else {
            knoten.textContent = auto;
            knoten.dataset.auto = '1';
        }
    });
    knoten.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); knoten.blur(); }
    });
    return {
        setzeAuto(text) {
            if (!text) return;
            auto = text;
            if (knoten.dataset.auto === '1' && document.activeElement !== knoten) {
                knoten.textContent = text;
            }
        }
    };
}

/* Erste Zeile einer Textdatei holen, ohne sie ganz zu uebertragen.
   Ein Range-Abruf ginge eleganter, aber Starlette 0.35.1 beantwortet
   "Range" bei FileResponse nicht — man bekaeme trotzdem alles. Also lesen
   wir den Datenstrom und brechen nach dem ersten Zeilenumbruch ab; der
   Server hoert dann auf zu senden. Benutzt fuer die Beschriftung der
   Klappzeile, wenn kein eigener Titel gesetzt ist (15.09.2026). */
async function _ersteZeileHolen(url) {
    const ctrl = new AbortController();
    const r = await fetch(url, { credentials: 'same-origin', signal: ctrl.signal });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    let roh = '';
    if (!r.body || !r.body.getReader) {
        roh = (await r.text()).slice(0, 4096);      // aeltere WebViews
    } else {
        const leser = r.body.getReader();
        const dec = new TextDecoder();
        try {
            while (roh.length < 4096) {
                const { done, value } = await leser.read();
                if (done) break;
                roh += dec.decode(value, { stream: true });
                if (roh.includes('\n')) break;
            }
        } finally {
            try { await leser.cancel(); } catch (_) { /* egal */ }
            ctrl.abort();
        }
    }
    // Erste Zeile mit Inhalt; Markdown-Auszeichnung vorne weg.
    for (const zeile of roh.split(/\r?\n/)) {
        const t = zeile.replace(/^[#=*\s>-]+/, '').trim();
        if (t) return t.length > 80 ? t.slice(0, 80) + '…' : t;
    }
    return '';
}

function _haversineKm(lat1, lon1, lat2, lon2) {
    const R = 6371, dLat = (lat2-lat1)*Math.PI/180, dLon = (lon2-lon1)*Math.PI/180;
    const a = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)**2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

function _parseGpxXml(xmlText) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(xmlText, 'application/xml');
    if (doc.querySelector('parsererror')) throw new Error('GPX Parse-Fehler');

    // trkpt = aufgezeichnete Spur, rtept = geplante Route. Beide sind
    // gueltiges GPX, und Dateien aus Routenplanern haben oft NUR eine
    // Route (15.09.2026, gemeldet vom App-Chat fuer eine Radtour von
    // 1992: 29 rtept, kein einziges trkpt — leere Karte, keine Meldung).
    //
    // Getrennt sammeln und die Spur bevorzugen, NICHT zusammenwerfen:
    // Eine Datei mit trk UND rte ergaebe sonst eine Zickzacklinie
    // zwischen gegangener und geplanter Strecke.
    const ns = 'http://www.topografix.com/GPX/1/1';
    const hole = (tag) => {
        const mit = [...doc.getElementsByTagNameNS(ns, tag)];
        return mit.length ? mit : [...doc.getElementsByTagName(tag)];
    };
    const punkte = hole('trkpt');
    if (punkte.length) return _extractPoints(punkte, doc);

    const route = hole('rtept');
    if (route.length) return _extractPoints(route, doc);

    throw new Error('Keine Track- oder Routenpunkte');
}

function _extractPoints(trkpts, doc) {
    const points = trkpts.map(pt => {
        const lat = parseFloat(pt.getAttribute('lat'));
        const lon = parseFloat(pt.getAttribute('lon'));
        const eleEl = pt.querySelector('ele') || pt.getElementsByTagName('ele')[0];
        const timeEl = pt.querySelector('time') || pt.getElementsByTagName('time')[0];
        return {
            lat, lon,
            ele: eleEl ? parseFloat(eleEl.textContent) : null,
            time: timeEl ? timeEl.textContent : null,
        };
    }).filter(p => !isNaN(p.lat) && !isNaN(p.lon));

    // Compute stats
    let distKm = 0;
    let ascent = 0, descent = 0;
    let hasEle = false;
    for (let i = 1; i < points.length; i++) {
        distKm += _haversineKm(points[i-1].lat, points[i-1].lon, points[i].lat, points[i].lon);
        if (points[i].ele !== null && points[i-1].ele !== null) {
            hasEle = true;
            const diff = points[i].ele - points[i-1].ele;
            if (diff > 0) ascent += diff;
            else descent += Math.abs(diff);
        }
    }

    // Desc from metadata
    const descEl = doc.querySelector('metadata desc') || doc.querySelector('desc');
    const desc = descEl ? descEl.textContent : '';

    // Duration from timestamps
    const times = points.map(p => p.time).filter(Boolean);
    const duration = times.length >= 2 ? _parseDuration(times[0], times[times.length-1]) : null;

    return { points, distKm, ascent, descent, hasEle, desc, duration };
}

function _renderTourStats(statsBar, parsed, descStats) {
    const chips = [];
    // Distance: computed preferred, desc as fallback
    if (parsed.distKm > 0.05) {
        chips.push({ icon: '📏', val: parsed.distKm.toFixed(1) + ' km' });
    } else if (descStats.distance) {
        chips.push({ icon: '📏', val: descStats.distance });
    }
    // Duration: from timestamps preferred, else desc
    if (parsed.duration) {
        chips.push({ icon: '⏱', val: parsed.duration });
    } else if (descStats.duration) {
        chips.push({ icon: '⏱', val: descStats.duration });
    }
    /* Hoehe: Auf- UND Abstieg. Der Abstieg wurde seit jeher mitgerechnet
       (_parseGpxXml), aber nie angezeigt — dabei ist er bei einer Tour die
       zweite Haelfte der Auskunft. Eine Runde mit 800 m auf und 800 m ab
       ist etwas anderes als eine Passfahrt mit 800 m auf und 100 m ab,
       und im Profil sieht man das nur, wenn man genau hinsieht.
       Gezeigt wird er nur, wenn er sich vom Aufstieg nennenswert
       unterscheidet — bei einer Rundtour stuenden sonst zwei fast gleiche
       Zahlen nebeneinander und sagten nichts (15.09.2026). */
    if (parsed.hasEle && parsed.ascent > 5) {
        chips.push({ icon: '⬆', val: Math.round(parsed.ascent) + ' m' });
        const unterschied = Math.abs(parsed.ascent - parsed.descent);
        if (parsed.descent > 5 && unterschied > Math.max(20, parsed.ascent * 0.1)) {
            chips.push({ icon: '⬇', val: Math.round(parsed.descent) + ' m' });
        }
    } else if (descStats.elevation) {
        chips.push({ icon: '⬆', val: descStats.elevation });
    }
    if (descStats.calories) chips.push({ icon: '🔥', val: descStats.calories });
    if (descStats.hr)       chips.push({ icon: '❤️', val: descStats.hr });

    statsBar.innerHTML = chips.map(c =>
        `<span class="tour-stat-chip"><span class="tour-stat-icon">${c.icon}</span>${c.val}</span>`
    ).join('');
}

function _renderElevationProfile(elevDiv, points) {
    /* Hoehenprofil ueber der ENTFERNUNG, nicht ueber dem Punktindex
       (15.09.2026). Vorher war die X-Achse der Index: `step = W / (n-1)`.
       Solange ein Geraet gleichmaessig aufzeichnet, faellt das nicht auf —
       bei ungleich dichten Abschnitten aber schon, und mit den jetzt
       ergaenzten Kilometermarken waere es schlicht gelogen: Gleiche
       Strecke im Bild muss gleiche Strecke im Gelaende heissen.

       Marken beantworten die Frage, die man beim Ansehen stellt: nicht
       "wie hoch", sondern "wo". Ein Berg ohne Kilometerangabe ist eine
       Form, keine Auskunft. */
    const mit = points.filter(pt => pt.ele !== null && !isNaN(pt.ele));
    if (mit.length < 2) return;
    elevDiv.classList.remove('hidden');

    // Weglaenge aufsummieren — dieselbe Rechnung wie in den Kennzahlen.
    const km = [0];
    for (let i = 1; i < mit.length; i++) {
        km.push(km[i-1] + _haversineKm(mit[i-1].lat, mit[i-1].lon, mit[i].lat, mit[i].lon));
    }
    const gesamt = km[km.length - 1];
    if (!(gesamt > 0)) return;

    const W = elevDiv.clientWidth || 600, H = 80;
    const eles = mit.map(pt => pt.ele);
    const minE = Math.min(...eles), maxE = Math.max(...eles);
    const spanne = maxE - minE || 1;

    const pts = mit.map((pt, i) => {
        const x = (km[i] / gesamt) * W;
        const y = H - ((pt.ele - minE) / spanne) * (H - 10) - 2;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    /* Zwei Fehler sassen hier frueher uebereinander (gefunden 15.09.2026,
       gemeldet als "unten viele schwarz"): Die Verlaufs-Kennung trug eine
       Zufallszahl, der Verweis darauf nicht — ein SVG-fill mit
       unaufloesbarer Kennung faellt auf Schwarz zurueck. Und
       `var(--accent)` ist in keiner CSS-Datei definiert. Jetzt EINE
       Kennung an beiden Stellen und das Magenta der Route: dieselbe Tour,
       dieselbe Farbe, in beiden Themes lesbar. */
    const grad = 'elev-grad-' + Math.random().toString(36).slice(2);

    // Runde Schrittweite waehlen, damit dort 5, 10, 25 km steht und nicht
    // 27,6 — und hoechstens so viele Marken, wie nebeneinander passen.
    const platz = Math.max(2, Math.floor(W / 90));
    const roh   = gesamt / platz;
    const stufe = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500]
                    .find(v => v >= roh) || Math.ceil(roh / 100) * 100;
    const marken = [];
    for (let d = stufe; d < gesamt - stufe * 0.35; d += stufe) marken.push(d);

    elevDiv.innerHTML = `
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="tour-elev-svg">
            <defs>
                <linearGradient id="${grad}" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#E6007E" stop-opacity="0.45"/>
                    <stop offset="100%" stop-color="#E6007E" stop-opacity="0.04"/>
                </linearGradient>
            </defs>
            ${marken.map(d => {
                const x = ((d / gesamt) * W).toFixed(1);
                return `<line x1="${x}" y1="0" x2="${x}" y2="${H}"
                              stroke="currentColor" stroke-opacity="0.18" stroke-width="1"/>`;
            }).join('')}
            <polygon points="${pts.join(' ')} ${W},${H} 0,${H}" fill="url(#${grad})"/>
            <polyline points="${pts.join(' ')}"
                      fill="none" stroke="#E6007E" stroke-width="1.5" stroke-linejoin="round"/>
        </svg>
        <div class="tour-elev-marken">
            ${marken.map(d => `<span style="left:${((d / gesamt) * 100).toFixed(2)}%">${
                gesamt >= 10 ? Math.round(d) : d.toFixed(1)} km</span>`).join('')}
        </div>
        <div class="tour-elev-labels">
            <span>${Math.round(minE)} m</span>
            <span>${Math.round(maxE)} m</span>
        </div>`;
}

// CartoDB Voyager as default tile source — fair-use friendly, no API key,
// avoids OSM-direct hazard tiles when traffic ramps up.
const _OSM_LAYER  = 'https://tile.openstreetmap.de/{z}/{x}/{y}.png';
const _TOPO_LAYER = 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png';
const _ATTR_OSM   = '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> · Kacheln: FOSSGIS e.V.';
const _ATTR_TOPO  = '© <a href="https://opentopomap.org">OpenTopoMap</a>';

/* Kartenformat nach dem Verlauf der Tour (15.09.2026).

   Erster Versuch war falsch: Ich habe nur die HOEHE angepasst. Eine
   Nord-Sued-Route stand danach weiter als schmales Band in der Mitte
   einer 1600px breiten Karte — durch die groessere Hoehe sogar mit mehr
   leerer Landschaft drumherum als vorher. Verschlimmbessert.

   Richtig ist die andere Achse: Bei einer hochkanten Tour wird die Karte
   SCHMALER und rueckt in die Mitte. Das Feld nimmt dann die Form der
   Route an, statt sie in einem Breitbild zu ertraenken.

   Laengengrade schrumpfen zu den Polen hin mit cos(Breite) — ohne diesen
   Faktor waere in unseren Breiten jede Tour scheinbar hochkant.

   Gedeckelt in beide Richtungen: Ohne Mindestbreite wuerde aus einer sehr
   geraden Nord-Sued-Tour ein Streifen, in dem man nichts mehr erkennt;
   ohne Hoechsthoehe eine Saeule, durch die man scrollt. */
function _tourMasse(points, maxBreite, mobil) {
    if (!points.length || !maxBreite) return null;
    let latMin = 90, latMax = -90, lonMin = 180, lonMax = -180;
    for (const pt of points) {
        if (pt.lat < latMin) latMin = pt.lat;
        if (pt.lat > latMax) latMax = pt.lat;
        if (pt.lon < lonMin) lonMin = pt.lon;
        if (pt.lon > lonMax) lonMax = pt.lon;
    }
    const hoch  = latMax - latMin;
    const breit = (lonMax - lonMin) * Math.cos(((latMax + latMin) / 2) * Math.PI / 180);
    if (!(hoch > 0) || !(breit > 0)) return null;      // Punkt oder gerade Linie

    const seiten = hoch / breit;                       // > 1 = hochkant
    const maxH = mobil ? 480 : 560;
    const minH = mobil ? 200 : 240;
    const minB = mobil ? 220 : 360;   // Kopfzeile braucht Platz fuer Titel + drei Knoepfe

    let breite = maxBreite;
    let hoehe  = Math.round(maxBreite * seiten);
    if (hoehe > maxH) {                                // hochkant: schmaler, nicht hoeher
        hoehe  = maxH;
        breite = Math.max(minB, Math.min(maxBreite, Math.round(hoehe / seiten)));
    } else if (hoehe < minH) {
        hoehe = minH;
    }
    return { breite, hoehe };
}

/* Fehlermeldung in eine Karte setzen, ohne die Bedienelemente mitzureissen.
   15.09.2026: Hier stand `mapDiv.innerHTML = …`, und das raeumte auch das
   Loeschkreuz weg — es liegt im selben Container (z-index ueber Leaflet).
   Ausgerechnet das Element, das NICHT laedt, liess sich danach nicht mehr
   entfernen; im Wrapper sah man gar kein Kreuz mehr. Gemeldet
   am 15.09.2026 fuer eine Tour, deren GPX nur eine Route enthaelt.
   An den Rahmen haengen geht nicht: .tour-element ist nicht positioniert
   und hat overflow:hidden. */
function _mapMessage(container, text) {
    for (const kind of [...container.children]) {
        if (!kind.classList.contains('remove-photo-btn') &&
            !kind.classList.contains('locked-toggle-btn') &&
            !kind.classList.contains('map-layer-overlay')) kind.remove();
    }
    const box = document.createElement('div');
    box.className = 'tour-error';
    box.textContent = text;
    container.prepend(box);
}

async function _initTourMap(elemId, gpxUrl, mapDiv, statsBar, elevDiv, layerBtn, fotoBtn, tourSpace, tourAlbum, mapLayer, showElevation) {
    try {
        const res = await fetch(gpxUrl);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const xml = await res.text();
        const parsed = _parseGpxXml(xml);
        const descStats = _parseGpxDesc(parsed.desc);

        if (!parsed.points.length) {
            _mapMessage(mapDiv, '⚠️ Keine GPS-Punkte in GPX-Datei');
            return;
        }

        // Populate stats
        _renderTourStats(statsBar, parsed, descStats);

        // Elevation profile

        // Initialize Leaflet
        if (mapDiv._leaflet_id) return;
        const latlngs = parsed.points.map(p => [p.lat, p.lon]);

        // Format an den Verlauf anpassen, BEVOR Leaflet die Groesse misst.
        const mobil = window.matchMedia('(max-width: 768px)').matches;
        const voll  = mapDiv.clientWidth || mapDiv.offsetWidth;
        const masse = _tourMasse(parsed.points, voll, mobil);
        const rahmen = mapDiv.closest('.tour-card');
        if (masse) {
            mapDiv.style.height = masse.hoehe + 'px';
            // Nicht nur die Karte schmaler machen, sondern das ganze Element
            // (entschieden 15.09.2026, Weg A): Sonst stand eine
            // schmale Karte unter einer breiten Kopfzeile und ueber einem
            // 1700px-Hoehenprofil — drei Dinge statt einer Tour. Eine Tour
            // ist ein Objekt, und wenn sie hochkant ist, ist sie ein
            // hochkantes Objekt.
            if (rahmen && masse.breite < voll) {
                rahmen.style.maxWidth = masse.breite + 'px';
                rahmen.style.margin   = '0 auto';   // der senkrechte Abstand sitzt aussen
            }
        }

        // Erst JETZT das Hoehenprofil zeichnen: Es nimmt seine Breite aus
        // dem Container, und der ist eine Zeile weiter oben schmaler
        // geworden. Vorher gezeichnet haette es die alte, volle Breite in
        // seinen viewBox geschrieben.
        if (parsed.hasEle && showElevation) _renderElevationProfile(elevDiv, parsed.points);

        let currentLayer = mapLayer;
        const map = L.map(mapDiv, { zoomControl: false, scrollWheelZoom: false, touchZoom: true });
        L.control.zoom({ position: 'topright' }).addTo(map);
        // Desktop: enable scroll-zoom only after click (no page-scroll trap)
        mapDiv.addEventListener('click', () => map.scrollWheelZoom.enable());
        mapDiv.addEventListener('mouseleave', () => map.scrollWheelZoom.disable());
        const initUrl  = mapLayer === 'topo' ? _TOPO_LAYER : _OSM_LAYER;
        const initAttr = mapLayer === 'topo' ? _ATTR_TOPO  : _ATTR_OSM;
        const initMax  = mapLayer === 'topo' ? 17 : 18;
        let tileLayer = L.tileLayer(initUrl, { attribution: initAttr, maxZoom: initMax }).addTo(map);
        // Label the layer-toggle button correctly initially
        layerBtn.textContent = mapLayer === 'topo' ? '🏔️' : '🗺️';
        layerBtn.title = (window.MPD_I18N
            ? window.MPD_I18N.t(mapLayer === 'topo' ? 'element_actions.switch_to_osm' : 'element_actions.switch_to_topo')
            : (mapLayer === 'topo' ? 'Zu OSM wechseln' : 'Zu Topo wechseln'));

        // Track — white casing underneath + magenta on top, so it stays legible
        // on any basemap (OSM water/roads, topo contour lines).
        L.polyline(latlngs, { color: '#ffffff', weight: 6, opacity: 0.9, lineJoin: 'round', lineCap: 'round' }).addTo(map);
        const polyline = L.polyline(latlngs, { color: '#E6007E', weight: 3, opacity: 1, lineJoin: 'round', lineCap: 'round' }).addTo(map);

        // Start/end markers
        const startIcon = L.divIcon({ className: '', html: '<div class="tour-marker tour-marker-start">S</div>', iconSize: [22,22], iconAnchor: [11,11] });
        const endIcon   = L.divIcon({ className: '', html: '<div class="tour-marker tour-marker-end">Z</div>',   iconSize: [22,22], iconAnchor: [11,11] });
        L.marker(latlngs[0], { icon: startIcon }).addTo(map);
        L.marker(latlngs[latlngs.length - 1], { icon: endIcon }).addTo(map);

        map.fitBounds(polyline.getBounds(), { padding: [24, 24] });
        setTimeout(() => map.invalidateSize(), 350);

        /* ── Fotos dieser Tour ──────────────────────────────────────────
           Gefiltert allein ueber die ZEIT, genau wie die Trails-Seite:
           erster und letzter Zeitstempel der Spur ergeben den Zeitraum.

           Warum nicht zusaetzlich ueber den Ort (bbox): "Fotos von dieser
           Wanderung" ist eine Regel, die man versteht — "Fotos in der
           Naehe der Strecke, verfeinert nach Zeit" ist eine, die man
           erklaeren muss, und bei der niemand weiss, warum ein bestimmtes
           Bild fehlt. Wer am selben Tag dort Fotos macht, war dort.
           Zwei Mechaniken fuer dieselbe Frage zerlegen sich ausserdem
           spaeter (entschieden 15.09.2026).

           Vorgabe AUS, nichts wird gemerkt — wie auf der Trails-Seite.
           Eine Tour mit dreissig Fotos waere sonst beim Oeffnen sofort
           zugepflastert. */
        const zeiten = parsed.points.map(pt => pt.time).filter(Boolean).sort();
        if (fotoBtn && zeiten.length) {
            const vonTag = zeiten[0].slice(0, 10);
            const bisTag = zeiten[zeiten.length - 1].slice(0, 10);
            const fotoLayer = L.layerGroup().addTo(map);
            let fotosAn = false, geladen = false;

            fotoBtn.hidden = false;
            fotoBtn.addEventListener('click', async (ev) => {
                ev.stopPropagation();
                fotosAn = !fotosAn;
                fotoBtn.classList.toggle('active', fotosAn);
                if (!fotosAn) { fotoLayer.clearLayers(); geladen = false; return; }
                if (geladen) return;
                try {
                    const url = `${API_BASE}/api/photos/geo?from=${vonTag}&to=${bisTag}`
                              + `&space=${encodeURIComponent(tourSpace)}&limit=2000`;
                    const res = await fetch(url, { credentials: 'same-origin' });
                    if (!res.ok) throw new Error('HTTP ' + res.status);
                    const daten = await res.json();
                    const fotos = daten.photos || [];
                    for (const ph of fotos) {
                        const bild = document.createElement('img');
                        bild.className = 'tour-foto-marker';
                        bild.loading = 'lazy';
                        bild.alt = ph.file || '';
                        bild.src = `${API_BASE}/api/thumbnail/${encodeURIComponent(ph.space)}/`
                                 + `${encodeURIComponent(ph.album)}/${encodeURIComponent(ph.file)}?size=sm`;
                        const halter = document.createElement('div');
                        halter.className = 'tour-foto-halter';
                        halter.appendChild(bild);
                        const mk = L.marker([ph.lat, ph.lon], {
                            icon: L.divIcon({ className: '', html: halter.outerHTML,
                                              iconSize: [40, 40], iconAnchor: [20, 20] })
                        }).addTo(fotoLayer);
                        // Popup als Knoten bauen, nicht als HTML-Text: Dateinamen
                        // und Albumnamen kommen aus dem Dateisystem.
                        const box = document.createElement('div');
                        const name = document.createElement('b');
                        name.textContent = ph.file || '';
                        const wo = document.createElement('div');
                        wo.textContent = ph.album || '';
                        box.appendChild(name); box.appendChild(wo);
                        mk.bindPopup(box);
                    }
                    geladen = true;

                    /* "Keine Fotos" und "noch nicht durchsucht" sahen bisher
                       gleich aus (gemeldet 15.09.2026: "bei
                       Fotos bei Touren habe ich festgestellt dass viele
                       fehlen woran kann es liegen?"). Die Route
                       /api/photos/geo scannt bewusst nie selbst und meldet
                       stattdessen mit `albums_unscanned`, wie vollstaendig
                       ihre Auskunft ist — angesehen hat das nur niemand.
                       Eine leere Karte ohne diesen Hinweis behauptet, es
                       gebe nichts. Sie weiss es aber gar nicht. */
                    const offen = daten.albums_unscanned || 0;
                    const _ft = (k, vars, fb) =>
                        (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
                    if (window.mpdToast) {
                        if (!fotos.length && offen > 0) {
                            window.mpdToast(_ft('element_actions.tour_photos_unscanned',
                                { count: offen },
                                `Noch keine Fotoorte bekannt — ${offen} Alben sind nicht durchsucht`),
                                { duration: 3600 });
                        } else if (!fotos.length) {
                            window.mpdToast(_ft('element_actions.tour_photos_none', null,
                                'Keine Fotos mit Aufnahmeort in diesem Zeitraum'),
                                { duration: 2200 });
                        } else if (offen > 0) {
                            window.mpdToast(_ft('element_actions.tour_photos_partial',
                                { count: offen },
                                `${offen} Alben noch nicht durchsucht — es koennen Orte fehlen`),
                                { duration: 3200 });
                        }
                    }
                } catch (err) {
                    fotosAn = false;
                    fotoBtn.classList.remove('active');
                    if (window.mpdToast) window.mpdToast('Fotoorte nicht verfügbar (' + err.message + ')',
                                                         { duration: 2600 });
                }
            });
        }

        // Vollbild wechselt die Groesse des Containers, Leaflet merkt das
        // von selbst nicht. Danach neu einpassen, damit die Tour wieder
        // formatfuellend liegt.
        document.addEventListener('fullscreenchange', () => {
            if (!rahmen) return;
            const drin = document.fullscreenElement === rahmen;
            if (drin) {
                mapDiv.style.height  = '';
                rahmen.style.maxWidth = '';
                rahmen.style.margin   = '';
            } else if (masse) {
                mapDiv.style.height = masse.hoehe + 'px';
                if (masse.breite < voll) {
                    rahmen.style.maxWidth = masse.breite + 'px';
                    rahmen.style.margin   = '0 auto';   // der senkrechte Abstand sitzt aussen
                }
            }
            setTimeout(() => {
                map.invalidateSize();
                map.fitBounds(polyline.getBounds(), { padding: [24, 24] });
            }, 120);
        });

        // Layer toggle
        layerBtn.addEventListener('click', () => {
            if (currentLayer === 'osm') {
                map.removeLayer(tileLayer);
                tileLayer = L.tileLayer(_TOPO_LAYER, { attribution: _ATTR_TOPO, maxZoom: 17 }).addTo(map);
                currentLayer = 'topo';
                layerBtn.textContent = '🏔️';
                layerBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.switch_to_osm') : 'Zu OSM wechseln');
            } else {
                map.removeLayer(tileLayer);
                tileLayer = L.tileLayer(_OSM_LAYER, { attribution: _ATTR_OSM, maxZoom: 18 }).addTo(map);
                currentLayer = 'osm';
                layerBtn.textContent = '🗺️';
                layerBtn.title = (window.MPD_I18N ? window.MPD_I18N.t('element_actions.switch_to_topo') : 'Zu Topo wechseln');
            }
        });

    } catch (err) {
        _mapMessage(mapDiv, `⚠️ GPX konnte nicht geladen werden: ${err.message}`);
    }
}
