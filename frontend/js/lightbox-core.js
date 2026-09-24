/* ═══════════════════════════════════════════
 *  Lightbox JavaScript Module
 *  Navigation, Slideshow, Zoom, Pan, EXIF
 *  ═══════════════════════════════════════════ */

class Lightbox {
    constructor() {
        this.currentIndex    = 0;
        this.slideshowInterval = null;
        this.zoomLevel       = 1;
        this.panX            = 0;
        this.panY            = 0;
        this.allImages       = [];
        this.albumName       = '';
        this.albumSpace      = 'shared';
        this._fullFor        = null;   // bereits auf 'full' gehobene URL
        this._fullPending    = null;   // 'full', das gerade geladen wird

        /* ── Touch state ── */
        this._touch = {
            pointers:    {},   // pointerId → {x, y}
            lastDist:    null, // last pinch distance
            lastMidX:    null, // last pinch midpoint X (client)
            lastMidY:    null, // last pinch midpoint Y (client)
            panStartX:   0,
            panStartY:   0,
            panOriginX:  0,
            panOriginY:  0,
            tapTimer:    null,
            tapCount:    0,
            chromeTimer: null,
            lastChromeClick: 0,
            swipeStartX: 0,
            swipeStartY: 0,
            lastTapTime: 0,    // for double-tap detection
            lastTapX:    0,
            lastTapY:    0,
        };

        /* ── Mouse state ── */
        this._mouse = {
            dragging:   false,
            // Wurde zwischen Druecken und Loslassen nennenswert bewegt?
            // Entscheidet, ob der folgende click als Zoom-Schritt zaehlt.
            moved:      false,
            startX:     0,
            startY:     0,
            originX:    0,
            originY:    0,
        };

        /* ── Editor state ── */
        this._edit = {
            rotation:       0,
            flip:           false,   // horizontal
            flipV:          false,   // vertical
            straighten:     0.0,
            brightness:     0,
            contrast:       0,
            saturation:     0,
            straightenOpen: false,
        };
        this._editHistory = [];   // Undo-Stack
        this._editFuture  = [];   // Redo-Stack
    }

    /* ══════════════════════════════════════════
     *  INIT
     * ══════════════════════════════════════════ */
    init(images, albumName, albumSpace = 'shared') {
        this.allImages  = images;
        this.albumName  = albumName;
        this.albumSpace = albumSpace;
        this._buildNavigator();
        this.setupEventListeners();
    }

    /* ══════════════════════════════════════════
     *  NAVIGATOR  (desktop overlay)
     * ══════════════════════════════════════════ */
    _buildNavigator() {
        if (document.getElementById('lb-navigator')) return;

        /* ── Swipe-up hint (touch only, disappears after first use) ── */
        if (!document.getElementById('lb-swipe-hint')) {
            const hint = document.createElement('div');
            hint.id = 'lb-swipe-hint';
            hint.className = 'lb-swipe-hint';
            hint.textContent = (window.MPD_I18N ? window.MPD_I18N.t('lightbox.info_hint') : 'Infos');
            const main = document.querySelector('.lb-main');
            if (main) main.appendChild(hint);
        }

        const nav = document.createElement('div');
        nav.id = 'lb-navigator';
        nav.style.cssText = `
            position: absolute;
            top: 12px;
            right: 12px;
            width: 160px;
            background: rgba(0,0,0,0.7);
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 4px;
            display: none;
            z-index: 10;
        `;

        const canvas = document.createElement('canvas');
        canvas.id = 'lb-navigator-canvas';
        canvas.width  = 160;
        canvas.height = 110;
        canvas.style.cssText = 'display:block; pointer-events:none;';
        nav.appendChild(canvas);

        /* Zoom bar with − / + buttons */
        const bar = document.createElement('div');
        bar.style.cssText = `
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 5px 8px;
            border-top: 1px solid rgba(255,255,255,0.1);
        `;

        const btnStyle = `
            width: 22px; height: 22px;
            background: rgba(255,255,255,0.15);
            border: none; border-radius: 3px;
            color: #fff; font-size: 1rem;
            cursor: pointer; flex-shrink: 0;
            display: flex; align-items: center; justify-content: center;
            line-height: 1;
        `;

        const btnMinus = document.createElement('button');
        btnMinus.textContent = '−';
        btnMinus.style.cssText = btnStyle;
        btnMinus.addEventListener('click', (e) => { e.stopPropagation(); window.lightbox.zoomStep(-1); });

        const track = document.createElement('div');
        track.style.cssText = `
            flex: 1; height: 3px;
            background: rgba(255,255,255,0.2);
            border-radius: 2px; position: relative; overflow: hidden;
        `;
        const fill = document.createElement('div');
        fill.id = 'lb-nav-zoom-fill';
        fill.style.cssText = 'height:100%; background:rgba(255,255,255,0.85); border-radius:2px; width:0%; transition: width 0.1s;';
        track.appendChild(fill);

        const btnPlus = document.createElement('button');
        btnPlus.textContent = '+';
        btnPlus.style.cssText = btnStyle;
        btnPlus.addEventListener('click', (e) => { e.stopPropagation(); window.lightbox.zoomStep(1); });

        bar.appendChild(btnMinus);
        bar.appendChild(track);
        bar.appendChild(btnPlus);
        nav.appendChild(bar);

        /* Attach to .lb-main so position stays fixed regardless of zoom/pan on the image */
        const main = document.querySelector('.lb-main');
        if (main) main.appendChild(nav);
    }

    _updateNavigator() {
        const nav    = document.getElementById('lb-navigator');
        const canvas = document.getElementById('lb-navigator-canvas');
        const img    = document.getElementById('lb-img');

        if (!nav || !canvas || !img) return;

        /* Only show navigator on desktop when zoomed */
        const isMobile = window.matchMedia('(pointer: coarse)').matches;
        if (isMobile || this.zoomLevel <= 1) {
            nav.style.display = 'none';
            return;
        }
        nav.style.display = 'block';

        /* Update zoom bar fill (range 1–6) */
        const fill = document.getElementById('lb-nav-zoom-fill');
        if (fill) fill.style.width = ((this.zoomLevel - 1) / 5 * 100) + '%';

        const ctx  = canvas.getContext('2d');
        const W    = canvas.width;
        const H    = canvas.height;

        ctx.clearRect(0, 0, W, H);

        /* Draw the thumbnail as background */
        try {
            ctx.drawImage(img, 0, 0, W, H);
        } catch (e) {
            /* image may not be fully decoded yet – skip frame */
            return;
        }

        /* Calculate the visible viewport rectangle in image-space.
         *
         * The img is rendered at its natural fitted size inside wrapper,
         * then we apply  scale(zoomLevel) + translate(panX, panY).
         *
         * Wrapper dimensions:
         */
        const wrapRect = document.getElementById('lb-image-wrapper').getBoundingClientRect();
        const wW = wrapRect.width;
        const wH = wrapRect.height;

        /* Fitted image size (object-fit: contain) */
        const imgNatW  = img.naturalWidth  || img.width;
        const imgNatH  = img.naturalHeight || img.height;
        const scale    = Math.min(wW / imgNatW, wH / imgNatH);
        const fitW     = imgNatW * scale;   // fitted px width
        const fitH     = imgNatH * scale;   // fitted px height

        /* After zoom the image appears fitW*zoomLevel wide.
         * The visible window is wW/zoomLevel wide in original fitted coords.
         * panX/panY is the translation applied to the image center. */
        const visW = fitW / this.zoomLevel;
        const visH = fitH / this.zoomLevel;

        /* Image is centered in wrapper. Offset of fitted image top-left: */
        const imgOffX = (wW - fitW) / 2;
        const imgOffY = (wH - fitH) / 2;

        /* Viewport top-left in fitted image coords:
         * Center of wrapper minus half the visible window, minus the pan offset. */
        const vpX = (wW / 2 - visW / 2) - this.panX / this.zoomLevel - imgOffX;
        const vpY = (wH / 2 - visH / 2) - this.panY / this.zoomLevel - imgOffY;

        /* Map to navigator canvas coordinates */
        const nx = (vpX / fitW) * W;
        const ny = (vpY / fitH) * H;
        const nw = (visW / fitW) * W;
        const nh = (visH / fitH) * H;

        /* Draw viewport rectangle */
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.9)';
        ctx.lineWidth   = 1;
        ctx.strokeRect(nx, ny, nw, nh);
        ctx.fillStyle   = 'rgba(255, 255, 255, 0.08)';
        ctx.fillRect(nx, ny, nw, nh);
    }

    /* ══════════════════════════════════════════
     *  APPLY TRANSFORM  (single source of truth)
     * ══════════════════════════════════════════ */
    _applyTransform() {
        const img = document.getElementById('lb-img');
        if (!img) return;

        // Wer wirklich hineinzoomt, braucht mehr Pixel als xl (2048) hat.
        if (this.zoomLevel > 1) this._upgradeToFull();

        /* Clamp pan so image never moves completely out of view */
        const wrapper  = document.getElementById('lb-image-wrapper');
        const wW = wrapper ? wrapper.clientWidth  : window.innerWidth;
        const wH = wrapper ? wrapper.clientHeight : window.innerHeight;

        const imgNatW = img.naturalWidth  || img.width  || wW;
        const imgNatH = img.naturalHeight || img.height || wH;
        const fitScale = Math.min(wW / imgNatW, wH / imgNatH);
        const fitW = imgNatW * fitScale;
        const fitH = imgNatH * fitScale;

        const maxPanX = Math.max(0, (fitW  * this.zoomLevel - wW)  / 2);
        const maxPanY = Math.max(0, (fitH  * this.zoomLevel - wH)  / 2);

        this.panX = Math.max(-maxPanX, Math.min(maxPanX, this.panX));
        this.panY = Math.max(-maxPanY, Math.min(maxPanY, this.panY));

        img.style.transform       = `scale(${this.zoomLevel}) translate(${this.panX / this.zoomLevel}px, ${this.panY / this.zoomLevel}px)`;
        img.style.transformOrigin = 'center center';
        img.style.transition      = 'none';
        img.style.cursor          = this.zoomLevel > 1 ? 'grab' : 'default';

        this._updateNavigator();
    }

    /* ══════════════════════════════════════════
     *  EVENT LISTENERS
     * ══════════════════════════════════════════ */
    setupEventListeners() {
        if (this._listenersAttached) return;
        this._listenersAttached = true;
        /* ── Keyboard ── */
        document.addEventListener('keydown', (e) => {
            const lb = document.getElementById('lightbox');
            if (!lb.classList.contains('active')) return;
            const tag = (document.activeElement || {}).tagName;
            const isInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';

            const editorActive = document.getElementById('lb-editor')?.classList.contains('active');

            /* ── Escape: Help → Crop-Mode → Editor → Mobile-Kebab → Info-Sidebar → Lightbox ──
               Escape muss IMMER greifen, auch wenn Slider/Input den Fokus hat — sonst
               steckt der User nach Slider-Drag fest. */
            if (e.key === 'Escape') {
                if (this._isHelpOpen())                                              { this._closeHelp(); return; }
                if (editorActive && this._edit?.cropActive)                          { this.closeCropMode(false); return; }
                if (editorActive)                                                    { this.closeEditor(); return; }
                if (this._isMobileKebabOpen())                                       { this.closeMobileKebab(); return; }
                if (!document.getElementById('lb-sidebar').classList.contains('hidden')) { this.toggleInfo(); return; }
                this.close(); return;
            }

            /* Andere Tasten: in Inputs/Slidern keine globalen Shortcuts. */
            if (isInput) return;

            /* ── Editor shortcuts (only when editor is open) ── */
            if (editorActive) {
                const ctrl = e.ctrlKey || e.metaKey;
                const key  = e.key.toLowerCase();

                if (e.key === 'r')     this.editorRotate(90);
                if (e.key === 'l')     this.editorRotate(270);
                if (e.key === 'Enter') this.saveEdit();

                // Undo / Redo: Ctrl+Z=undo, Ctrl+Shift+Z=redo, Ctrl+Y=redo
                if      (ctrl && e.shiftKey && key === 'z') this.editorRedo();
                else if (ctrl && key === 'y')               this.editorRedo();
                else if (ctrl && key === 'z')               this.editorUndo();

                return; // no global shortcuts in the editor
            }

            /* ── Global lightbox shortcuts ── */
            if (e.key === 'ArrowLeft')          { this.resetZoom(); this.navigate(-1); }
            if (e.key === 'ArrowRight')         { this.resetZoom(); this.navigate(1); }
            if (e.key === ' ')                  { e.preventDefault(); this.toggleSlideshow(); }
            if (e.key === 'i')                  this.toggleInfo();
            if (e.key === 'f')                  this.toggleFullscreen();
            if (e.key === 'e')                  { if (!window.MPD_READ_ONLY) this.openEditor(); }
            if (e.key === 'Delete' || e.key === 'Backspace') { if (!window.MPD_READ_ONLY) this.deletePhoto(); }
            if (e.key === '+' || e.key === '=') this.zoomStep(1);
            if (e.key === '-')                  this.zoomStep(-1);
            if (e.key === '0')                  this.resetZoom();
            if (e.key === '?' || e.key === 'h') this._toggleHelp();
        });

        const wrapper = document.getElementById('lb-image-wrapper');

        /* ── Gestenflaeche ──
         * Die Listener haengen an .lb-main, nicht am Wrapper. Der Wrapper ist
         * nur so gross wie das eingepasste Bild (max-width 90% / max-height
         * 90vh) und waechst beim Zoomen NICHT mit, weil transform:scale() den
         * Layout-Kasten unveraendert laesst. Weit gespreizte Finger — also
         * genau die Geste zum Rauszoomen — landeten dadurch neben der
         * empfindlichen Flaeche und wurden gar nicht erkannt.
         * .lb-main ist bildschirmfuellend und hat touch-action:none (CSS). */
        const gestureArea = document.querySelector('.lb-main') || wrapper;

        gestureArea.addEventListener('pointerdown',   (e) => this._onPointerDown(e),   { passive: false });
        gestureArea.addEventListener('pointermove',   (e) => this._onPointerMove(e),   { passive: false });
        gestureArea.addEventListener('pointerup',     (e) => this._onPointerUp(e),     { passive: false });
        gestureArea.addEventListener('pointercancel', (e) => this._onPointerCancel(e), { passive: false });

        /* ── Mouse Wheel (desktop zoom) ── */
        gestureArea.addEventListener('wheel', (e) => this._onWheel(e), { passive: false });

        /* ── touch-action: none blocks native browser pinch/scroll ── */
        gestureArea.style.touchAction = 'none';
        wrapper.style.touchAction     = 'none';


        /* ── Sidebar: swipe-down to close (mobile) ──
         * Touch events instead of pointer events: a pull-down that the
         * browser interprets as scroll dispatches pointercancel, not
         * pointerup, so the close gesture would silently swallow. */
        const sidebar = document.getElementById('lb-sidebar');
        if (sidebar) {
            let _sbStartY = 0;
            let _sbStartScroll = 0;
            let _sbFromMap = false;
            let _sbFired = false;
            sidebar.addEventListener('touchstart', (e) => {
                if (e.touches.length !== 1) return;
                _sbStartY = e.touches[0].clientY;
                _sbStartScroll = sidebar.scrollTop;
                _sbFromMap = !!e.target.closest('.lb-map, #lb-exif-map, .leaflet-container');
                _sbFired = false;
            }, { passive: true });
            sidebar.addEventListener('touchmove', (e) => {
                if (_sbFired || _sbFromMap) return;
                if (_sbStartScroll > 0 || sidebar.scrollTop > 0) return;
                if (sidebar.classList.contains('hidden')) return;
                const dy = e.touches[0].clientY - _sbStartY;
                if (dy > 80) {
                    _sbFired = true;
                    this.toggleInfo();
                }
            }, { passive: true });
        }
    }

    /* ══════════════════════════════════════════
     *  POINTER DOWN
     * ══════════════════════════════════════════ */
    _onPointerDown(e) {
        /* Bedienelemente nicht abfangen — die Gestenflaeche deckt jetzt den
         * ganzen Bildschirm ab, also auch Buttons, Sidebar und Editor. */
        if (e.target.closest(
            'button, input, select, textarea, a[href], ' +
            '.lb-mobile-meta, .lb-sidebar, .lb-editor, .lb-mobile-kebab-menu'
        )) return;

        e.preventDefault();
        wrapper_capture(e);

        const t = this._touch;
        const m = this._mouse;
        t.pointers[e.pointerId] = { x: e.clientX, y: e.clientY };

        const count = Object.keys(t.pointers).length;

        if (count === 1) {
            /* Single finger / mouse down */
            const isMouse = (e.pointerType === 'mouse');

            if (isMouse) {
                /* Mouse drag start */
                m.dragging = true;
                m.moved    = false;
                m.startX   = e.clientX;
                m.startY   = e.clientY;
                m.originX  = this.panX;
                m.originY  = this.panY;
            } else {
                /* Touch pan start */
                t.panStartX  = e.clientX;
                t.panStartY  = e.clientY;
                t.panOriginX = this.panX;
                t.panOriginY = this.panY;
                /* Swipe start (used when zoom = 1) */
                t.swipeStartX    = e.clientX;
                t.swipeStartY    = e.clientY;
                t.swipeStartTime = Date.now();
                /* Tap detection */
                t.tapCount++;
                clearTimeout(t.tapTimer);
                t.tapTimer = setTimeout(() => { t.tapCount = 0; }, 300);
            }
        } else if (count === 2) {
            /* Second finger – init pinch */
            const pts = Object.values(t.pointers);
            t.lastDist = this._dist(pts[0], pts[1]);
            t.lastMidX = (pts[0].x + pts[1].x) / 2;
            t.lastMidY = (pts[0].y + pts[1].y) / 2;
            /* Cancel any pending tap */
            clearTimeout(t.tapTimer);
            t.tapCount = 0;
        }
    }

    /* ══════════════════════════════════════════
     *  POINTER MOVE
     * ══════════════════════════════════════════ */
    _onPointerMove(e) {
        const t = this._touch;
        const m = this._mouse;

        /* Pointer, die auf einem Bedienelement gestartet sind, wurden in
         * _onPointerDown verworfen und duerfen hier nichts bewegen. */
        if (!(e.pointerId in t.pointers)) return;
        e.preventDefault();

        /* Update stored position */
        if (t.pointers[e.pointerId]) {
            t.pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
        }

        const count = Object.keys(t.pointers).length;

        if (e.pointerType === 'mouse') {
            // Bewegung immer festhalten, auch ohne Zoom: der Nutzer koennte
            // ziehen, bevor gezoomt ist, und der click danach soll trotzdem
            // nicht als Zoom-Schritt zaehlen.
            if (m.dragging &&
                (Math.abs(e.clientX - m.startX) > 4 ||
                 Math.abs(e.clientY - m.startY) > 4)) {
                m.moved = true;
            }
            if (!m.dragging || this.zoomLevel <= 1) return;
            this.panX = m.originX + (e.clientX - m.startX);
            this.panY = m.originY + (e.clientY - m.startY);
            document.getElementById('lb-img').style.cursor = 'grabbing';
            this._applyTransform();
            return;
        }

        /* Touch */
        if (count === 2) {
            /* ── Pinch ── */
            const pts  = Object.values(t.pointers);
            const dist = this._dist(pts[0], pts[1]);
            const midX = (pts[0].x + pts[1].x) / 2;
            const midY = (pts[0].y + pts[1].y) / 2;

            if (t.lastDist !== null) {
                const ratio      = dist / t.lastDist;
                const newZoom    = Math.max(1, Math.min(6, this.zoomLevel * ratio));

                /* Pan to keep pinch midpoint stable */
                const dMidX = midX - t.lastMidX;
                const dMidY = midY - t.lastMidY;
                this.panX = this.panX * (newZoom / this.zoomLevel) + dMidX;
                this.panY = this.panY * (newZoom / this.zoomLevel) + dMidY;

                this.zoomLevel = newZoom;
                this._applyTransform();
            }

            t.lastDist = dist;
            t.lastMidX = midX;
            t.lastMidY = midY;

        } else if (count === 1 && this.zoomLevel > 1) {
            /* ── Single-finger pan (only when zoomed) ── */
            const dx = e.clientX - t.panStartX;
            const dy = e.clientY - t.panStartY;
            this.panX = t.panOriginX + dx;
            this.panY = t.panOriginY + dy;
            this._applyTransform();
        }
        /* count === 1 && zoomLevel === 1  →  handled as swipe on pointerup */
    }

    /* ══════════════════════════════════════════
     *  POINTER UP / CANCEL
     * ══════════════════════════════════════════ */
    _onPointerUp(e) {
        e.preventDefault();
        const t = this._touch;
        const m = this._mouse;
        const isMouse = (e.pointerType === 'mouse');

        if (isMouse) {
            if (m.dragging) {
                m.dragging = false;
                const img = document.getElementById('lb-img');
                if (img) img.style.cursor = this.zoomLevel > 1 ? 'grab' : 'default';
            }
            delete t.pointers[e.pointerId];
            return;
        }

        /* Touch */
        if (!(e.pointerId in t.pointers)) return;
        const wasTwo = (Object.keys(t.pointers).length === 2);
        delete t.pointers[e.pointerId];
        const nowOne = (Object.keys(t.pointers).length === 1);

        /* Reset pinch state when fingers lift */
        if (wasTwo && nowOne) {
            t.lastDist = null;
            /* Snap zoom to 1 if very close */
            if (this.zoomLevel < 1.15) this.resetZoom();
            /* Re-anchor single-finger pan */
            const remaining = Object.values(t.pointers)[0];
            if (remaining) {
                t.panStartX  = remaining.x;
                t.panStartY  = remaining.y;
                t.panOriginX = this.panX;
                t.panOriginY = this.panY;
            }
            return;
        }

        if (Object.keys(t.pointers).length > 0) return;

        /* All fingers lifted */
        t.lastDist = null;

        /* Snap zoom to 1 if very close — but still allow swipe detection */
        if (this.zoomLevel < 1.15) {
            this.resetZoom();
            /* fall through — swipe/tap detection still needed */
        }

        const moveDx = Math.abs(e.clientX - t.swipeStartX);
        const moveDy = Math.abs(e.clientY - t.swipeStartY);
        const isTap  = (moveDx < 10 && moveDy < 10);

        if (isTap) {
            if (this.zoomLevel > 1) {
                /* Any tap on zoomed image → reset zoom */
                t.tapCount = 0;
                clearTimeout(t.tapTimer);
                clearTimeout(t.chromeTimer);
                t.lastTapTime = 0;
                this.resetZoom();
                return;
            }

            /* Tap while info sidebar is open → close it */
            const _sb = document.getElementById('lb-sidebar');
            if (_sb && !_sb.classList.contains('hidden')) {
                this.toggleInfo();
                t.tapCount    = 0;
                t.lastTapTime = 0;
                clearTimeout(t.tapTimer);
                clearTimeout(t.chromeTimer);
                return;
            }

            /* zoom = 1: check for double-tap */
            const now     = Date.now();
            const tapDist = Math.hypot(e.clientX - t.lastTapX, e.clientY - t.lastTapY);
            if (now - t.lastTapTime < 300 && tapDist < 50) {
                /* Double-tap → zoom to 2.5× centered at tap position */
                t.lastTapTime = 0;
                t.tapCount = 0;
                clearTimeout(t.tapTimer);
                clearTimeout(t.chromeTimer);
                const wrapper  = document.getElementById('lb-image-wrapper');
                const rect     = wrapper.getBoundingClientRect();
                const cx = e.clientX - rect.left - rect.width  / 2;
                const cy = e.clientY - rect.top  - rect.height / 2;
                const newZoom  = 2.5;
                this.zoomLevel = newZoom;
                this.panX      = cx * (1 - newZoom);
                this.panY      = cy * (1 - newZoom);
                this._applyTransform();
                return;
            }
            t.lastTapTime = now;
            t.lastTapX    = e.clientX;
            t.lastTapY    = e.clientY;
        }

        /* Swipe gestures only when zoom = 1 */
        if (this.zoomLevel <= 1.05) {
            const swipeDx  = t.swipeStartX - e.clientX;   // + = left
            const swipeDy  = t.swipeStartY - e.clientY;   // + = up
            const absSwDx  = Math.abs(swipeDx);
            const absSwDy  = Math.abs(swipeDy);

            const elapsedY = Date.now() - (t.swipeStartTime || 0);
            const velY     = elapsedY > 0 ? absSwDy / elapsedY : 999;

            if (absSwDy > 80 && absSwDy > absSwDx && velY >= 0.3) {
                /* Wischen nach oben → Metadaten.
                   Frueher genuegten 40px ohne jede Geschwindigkeitspruefung,
                   waehrend das Blaettern schon immer 0,3 px/ms verlangte. Ein
                   Hochziehen um 50px ueber 1,2s — siebenmal langsamer als die
                   Blaetter-Schwelle — oeffnete die Metadaten also zuverlaessig.
                   Auf einem grossen Tablet reicht dafuer ein Nachrutschen beim
                   Umgreifen, zumal touch-action:none dem Browser jede eigene
                   Deutung nimmt. Jetzt dieselbe Geschwindigkeit wie beim
                   Blaettern, dazu der doppelte Weg: ein bewusstes Hochwischen
                   erfuellt beides muehelos, ein Abrutschen nicht mehr. */
                const sidebar = document.getElementById('lb-sidebar');
                if (swipeDy > 0 && sidebar && sidebar.classList.contains('hidden')) {
                    this.toggleInfo();
                }
                /* Swipe down while open is handled via sidebar listener */
            } else if (absSwDx > 40 && absSwDx > absSwDy) {
                /* Horizontal swipe → navigate (velocity check: ≥ 0.3 px/ms) */
                const elapsed  = Date.now() - (t.swipeStartTime || 0);
                const velocity = elapsed > 0 ? absSwDx / elapsed : 999;
                if (velocity >= 0.3) {
                    if (swipeDx > 0) this.navigate(1);
                    else             this.navigate(-1);
                }
            }
        }

        t.tapCount = 0;
        clearTimeout(t.tapTimer);
    }

    _onPointerCancel(e) {
        delete this._touch.pointers[e.pointerId];
        this._touch.lastDist = null;
        this._mouse.dragging = false;
    }

    /* ══════════════════════════════════════════
     *  MOUSE WHEEL  (desktop zoom)
     * ══════════════════════════════════════════ */
    _onWheel(e) {
        e.preventDefault();
        /* Multiplikativ, nicht additiv: eine feste Schrittweite waere bei
         * 1x eine 15%-Aenderung, bei 6x nur noch 2,5% — das Zoomen wuerde
         * mit steigender Stufe gefuehlt einschlafen. */
        const STEP      = 1.15;
        const factor    = e.deltaY > 0 ? 1 / STEP : STEP;
        const newZoom   = Math.max(1, Math.min(6, this.zoomLevel * factor));

        if (newZoom === 1) {
            this.resetZoom();
            return;
        }

        /* Zoom toward mouse cursor position */
        const wrapper  = document.getElementById('lb-image-wrapper');
        const rect     = wrapper.getBoundingClientRect();
        const mx       = e.clientX - rect.left - rect.width  / 2;
        const my       = e.clientY - rect.top  - rect.height / 2;

        const ratio    = newZoom / this.zoomLevel;
        this.panX      = mx + (this.panX - mx) * ratio;
        this.panY      = my + (this.panY - my) * ratio;
        this.zoomLevel = newZoom;
        this._applyTransform();
    }

    /* ══════════════════════════════════════════
     *  ZOOM HELPERS
     * ══════════════════════════════════════════ */
    zoomStep(dir) {
        /* Faktor 1.2 ergibt wie zuvor rund 10 Schritte von 1x auf 6x,
         * aber gleichmaessig ueber den ganzen Bereich. */
        const STEP    = 1.2;
        const newZoom = Math.max(1, Math.min(6, dir > 0 ? this.zoomLevel * STEP
                                                        : this.zoomLevel / STEP));
        if (newZoom === 1) { this.resetZoom(); return; }
        this.zoomLevel = newZoom;
        this._applyTransform();
    }

    resetZoom() {
        this.zoomLevel = 1;
        this.panX      = 0;
        this.panY      = 0;
        const img = document.getElementById('lb-img');
        if (img) {
            img.style.transform  = '';
            img.style.transition = '';
            img.style.cursor     = 'default';
        }
        const nav = document.getElementById('lb-navigator');
        if (nav) nav.style.display = 'none';
    }

    /* ══════════════════════════════════════════
     *  OPEN / CLOSE / SHOW / NAVIGATE
     * ══════════════════════════════════════════ */
    /* Tipp aufs Bild auf Beruehrungsgeraeten.
       Der Bild-Container traegt seit jeher onclick="toggleZoom()" — auf dem
       Desktop ist das die gewollte Klick-zoomt-Geste. Am Finger kommt genau
       dieses click-Ereignis an, waehrend die Pointer-Gestenschicht den Tipp
       nicht als solchen sieht. Deshalb haengt der Umschalter hier und nicht
       in _onPointerUp.

       Kommt innerhalb von 300ms ein zweiter Klick, ist es ein Doppeltipp:
       dann gewinnt der Zoom der Gestenschicht und es wird nichts umgeschaltet. */
    scheduleChromeToggle() {
        const t = this._touch;
        clearTimeout(t.chromeTimer);

        // Reihenfolge ist hier entscheidend: click feuert IMMER nach
        // pointerup. Beim Doppeltipp hat _onPointerUp den Zoom also schon
        // auf 2,5 gesetzt, wenn dieser zweite click eintrifft. Stuende die
        // Zoom-Abfrage vorn, saehe sie genau diesen frischen Zoom und wuerde
        // ihn sofort wieder zuruecksetzen — der Doppeltipp zoomte dann nur
        // fuer wenige Millisekunden. Deshalb zuerst pruefen, ob dies der
        // zweite Klick einer Doppelgeste ist, und dann aussteigen.
        const now      = Date.now();
        const isSecond = (now - (t.lastChromeClick || 0)) < 300;
        t.lastChromeClick = isSecond ? 0 : now;
        if (isSecond) return;              // Doppeltipp — die Gestenschicht zoomt

        if (this.zoomLevel > 1) {          // Einzeltipp im Zoom: herauszoomen
            this.resetZoom();
            return;
        }
        t.chromeTimer = setTimeout(() => this.toggleChrome(), 300);
    }

    /* Bedienelemente aus- und wieder einblenden (Einzeltipp aufs Bild).
       Die Klasse sitzt auf der Lightbox-Wurzel, das CSS entscheidet, was
       verschwindet — so bleibt die Logik hier frei von Elementlisten. */
    toggleChrome(force) {
        const lb = document.getElementById('lightbox');
        if (!lb) return;
        const hide = (force === undefined) ? !lb.classList.contains('chrome-hidden') : !!force;
        lb.classList.toggle('chrome-hidden', hide);
    }

    open(index) {
        this.currentIndex = index;
        this.toggleChrome(false);   // jedes Oeffnen startet mit sichtbarer Bedienung
        document.getElementById('lightbox').classList.add('active');
        document.getElementById('lb-sidebar').classList.add('hidden');
        document.body.style.overflow = 'hidden';
        this.resetZoom();
        this.show();
        this._updateEditButtons();
    }

    close() {
        clearTimeout(this._touch.chromeTimer);
        this.toggleChrome(false);
        document.getElementById('lightbox').classList.remove('active');
        document.body.style.overflow = '';
        this.stopSlideshow();
        this.resetZoom();
    }

    async show() {
        const img = this.allImages[this.currentIndex];
        const API_BASE = window.location.origin;

        document.getElementById('lb-img').src = `${API_BASE}${img.thumbnails.xl}`;
        document.getElementById('lb-counter').textContent = `${this.currentIndex + 1} / ${this.allImages.length}`;

        // Schaerfe nachziehen (siehe _upgradeToFull): xl steht sofort, 'full'
        // kommt hinterher — auf hochaufloesenden Schirmen sofort, sonst erst
        // beim Hineinzoomen.
        this._fullFor = null;
        this._fullPending = null;
        if (this._screenWantsFull()) this._upgradeToFull();

        // Mobile topbar: show filename immediately, gets replaced by date once EXIF is in
        this._resetMobileTopbar();
        this.closeMobileKebab();

        await this.loadMetadata(img);
        this.resetZoom();
        this.prefetchNeighborImages();
        this._updateEditButtons();
    }

    _updateEditButtons() {
        const img       = this.allImages[this.currentIndex];
        const canEdit   = !window.MPD_READ_ONLY && img && img.file.match(/\.jpe?g$/i);
        // PDX v1.4: delete remains forbidden for referenced photos — the user
        // shouldn't unintentionally delete from the source album while looking
        // at a curated album. Deletion must be done explicitly where the
        // photo lives. Edit on the other hand is OK — guarded by _confirmSourceEdit.
        const canDelete = canEdit && !(img && img.source_album);
        const editBtn   = document.getElementById('lb-edit-btn');
        const delBtn    = document.getElementById('lb-delete-btn');
        if (editBtn) editBtn.style.display = canEdit   ? '' : 'none';
        if (delBtn)  delBtn.style.display  = canDelete ? '' : 'none';
    }

    /* Vollbild-Schaerfe: xl (2048 px) zuerst, 'full' (4096 px) danach.
       Warum nicht gleich 'full': Es ist ein Mehrfaches an Bytes, und das
       erste Bild soll schnell stehen. Nachgeladen wird deshalb nur, wenn es
       etwas bringt — auf einem hochaufloesenden Schirm (dort ist xl schon
       bei Zoom 1 zu wenig) oder sobald tatsaechlich hineingezoomt wird.
       Die Nachbarbilder bleiben bewusst bei xl.

       Fehlt 'full' in der Antwort, bleibt es bei xl: Die Share-Ansicht
       liefert bewusst weder 'full' noch 'original' (Begruendung im Kopf von
       routers/deps.py), und lightbox-core.js laeuft auch dort. */
    _screenWantsFull() {
        const dpr  = window.devicePixelRatio || 1;
        const side = Math.max(window.innerWidth, window.innerHeight);
        return dpr * side > 2048;
    }

    _fullUrlFor(img) {
        return (img && img.thumbnails && img.thumbnails.full)
            ? `${window.location.origin}${img.thumbnails.full}`
            : null;
    }

    _upgradeToFull() {
        const url = this._fullUrlFor(this.allImages[this.currentIndex]);
        if (!url || this._fullFor === url || this._fullPending === url) return;

        this._fullPending = url;
        const pre = new Image();
        pre.decoding = 'async';
        pre.addEventListener('load', () => {
            this._fullPending = null;
            // Zwischenzeitlich weitergeblaettert? Dann gehoert das Bild nicht
            // mehr hierher.
            if (this._fullUrlFor(this.allImages[this.currentIndex]) !== url) return;
            const el = document.getElementById('lb-img');
            if (!el) return;
            el.src = url;          // liegt dekodiert im Cache — kein Flackern
            this._fullFor = url;
            // naturalWidth verdoppelt sich; fitScale halbiert sich, die
            // angezeigte Groesse bleibt gleich. Einmal nachrechnen, damit
            // Pan-Grenzen und Navigator zur neuen Naturgroesse passen.
            this._applyTransform();
        });
        pre.addEventListener('error', () => { this._fullPending = null; });
        pre.src = url;
    }

    prefetchNeighborImages() {
        const API_BASE = window.location.origin;
        if (this.currentIndex < this.allImages.length - 1) {
            const nextImg = new Image();
            nextImg.src = `${API_BASE}${this.allImages[this.currentIndex + 1].thumbnails.xl}`;
        }
        if (this.currentIndex > 0) {
            const prevImg = new Image();
            prevImg.src = `${API_BASE}${this.allImages[this.currentIndex - 1].thumbnails.xl}`;
        }
    }

    navigate(direction) {
        this.currentIndex += direction;
        if (this.currentIndex < 0)                    this.currentIndex = this.allImages.length - 1;
        if (this.currentIndex >= this.allImages.length) this.currentIndex = 0;
        this.show();
    }

    /* ══════════════════════════════════════════
     *  SLIDESHOW
     * ══════════════════════════════════════════ */
    toggleSlideshow() {
        if (this.slideshowInterval) {
            this.stopSlideshow();
        } else {
            this.slideshowInterval = setInterval(() => this.navigate(1), 3000);
            document.getElementById('slideshow-btn').textContent = '⏸ Pause';
            document.getElementById('slideshow-btn').classList.add('active');
        }
    }

    stopSlideshow() {
        if (this.slideshowInterval) {
            clearInterval(this.slideshowInterval);
            this.slideshowInterval = null;
            document.getElementById('slideshow-btn').textContent = '▶ Slideshow';
            document.getElementById('slideshow-btn').classList.remove('active');
        }
    }

    /* ══════════════════════════════════════════
     *  SIDEBAR / FULLSCREEN / INFO
     * ══════════════════════════════════════════ */
    toggleInfo() {
        const sidebar = document.getElementById('lb-sidebar');
        sidebar.classList.toggle('hidden');
        /* Hide swipe hint permanently after first use */
        const hint = document.getElementById('lb-swipe-hint');
        if (hint) hint.classList.add('hidden');
        if (!sidebar.classList.contains('hidden')) {
            setTimeout(() => {
                document.querySelectorAll('.lb-map').forEach(el => {
                    if (el._leafletInstance) el._leafletInstance.invalidateSize();
                });
            }, 350);
        }
    }

    toggleFullscreen() {
        if (!document.fullscreenElement) {
            document.getElementById('lightbox').requestFullscreen();
        } else {
            document.exitFullscreen();
        }
    }

    /* Legacy toggle for keyboard shortcut compatibility */
    toggleZoom() { this.zoomStep(1); }

    /* ══════════════════════════════════════════
     *  HELP PANEL
     * ══════════════════════════════════════════ */
    _toggleHelp() {
        document.getElementById('help').classList.toggle('show');
    }

    _closeHelp() {
        document.getElementById('help').classList.remove('show');
    }

    _isHelpOpen() {
        return document.getElementById('help').classList.contains('show');
    }

    /* ══════════════════════════════════════════
     *  EDIT BUTTONS (Desktop bottom-bar + Mobile FAB/Kebab)
     * ══════════════════════════════════════════ */
    _updateEditButtons() {
        const img       = this.allImages[this.currentIndex];
        const canEdit   = !window.MPD_READ_ONLY && img && img.file.match(/\.jpe?g$/i);
        // PDX v1.4: delete remains forbidden for referenced photos — the user
        // shouldn't unintentionally delete from the source album while looking
        // at a curated album. Deletion must be done explicitly where the
        // photo lives. Edit on the other hand is OK — guarded by _confirmSourceEdit.
        const canDelete = canEdit && !(img && img.source_album);

        const editBtn = document.getElementById('lb-edit-btn');
        const delBtn  = document.getElementById('lb-delete-btn');
        if (editBtn) editBtn.style.display = canEdit   ? '' : 'none';
        if (delBtn)  delBtn.style.display  = canDelete ? '' : 'none';

        // Teilen v3: Einzelfoto-Link — nur für Fotos und nur mit
        // Kuratier-Rechten (im Share-Viewer ist MPD_READ_ONLY = true).
        const canShare  = !window.MPD_READ_ONLY && img
            && /\.(jpe?g|jfif|png|gif|webp|heic|tiff?|bmp)$/i.test(img.file || '')
            && typeof window._openPhotoShareModal === 'function';
        const shareBtn  = document.getElementById('lb-share-btn');
        const shareItem = document.getElementById('lb-mobile-share');
        if (shareBtn)  shareBtn.style.display = canShare ? '' : 'none';
        if (shareItem) shareItem.hidden = !canShare;

        // Mobile FAB (edit) + Kebab trash item
        const fab     = document.getElementById('lb-mobile-edit-fab');
        const trash   = document.getElementById('lb-mobile-trash');
        if (fab)   fab.hidden   = !canEdit;
        if (trash) trash.hidden = !canDelete;
    }

    /* ══════════════════════════════════════════
     *  MOBILE KEBAB MENU
     * ══════════════════════════════════════════ */
    toggleMobileKebab(ev) {
        if (ev) ev.stopPropagation();
        const menu = document.getElementById('lb-mobile-kebab-menu');
        if (!menu) return;
        if (menu.hidden) this.openMobileKebab();
        else             this.closeMobileKebab();
    }

    openMobileKebab() {
        const menu = document.getElementById('lb-mobile-kebab-menu');
        if (!menu) return;
        menu.hidden = false;
        // Close on outside click — bind once per open
        this._kebabOutsideHandler = (e) => {
            if (!menu.contains(e.target) && e.target.id !== 'lb-mobile-kebab-btn') {
                this.closeMobileKebab();
            }
        };
        // Defer so the opening click doesn't immediately close
        setTimeout(() => document.addEventListener('click', this._kebabOutsideHandler), 0);
    }

    closeMobileKebab() {
        const menu = document.getElementById('lb-mobile-kebab-menu');
        if (!menu) return;
        menu.hidden = true;
        if (this._kebabOutsideHandler) {
            document.removeEventListener('click', this._kebabOutsideHandler);
            this._kebabOutsideHandler = null;
        }
    }

    _isMobileKebabOpen() {
        const menu = document.getElementById('lb-mobile-kebab-menu');
        return menu && !menu.hidden;
    }

    kebabDownload() {
        this.closeMobileKebab();
        downloadImage();
    }

    kebabDelete() {
        this.closeMobileKebab();
        this.deletePhoto();
    }

    kebabShare() {
        this.closeMobileKebab();
        this.sharePhoto();
    }

    /* Teilen v3: Einzelfoto-Link. Referenzierte Fotos (source_album)
     * liegen physisch im Quellalbum — der Share zeigt auf die Datei dort. */
    sharePhoto() {
        const img = this.allImages[this.currentIndex];
        if (!img || typeof window._openPhotoShareModal !== 'function') return;
        window._openPhotoShareModal({
            space: this.albumSpace,
            album: img.source_album || this.albumName,
            file : img.file,
        });
    }

    /* ══════════════════════════════════════════
     *  MOBILE TOPBAR — Date / Time · Location
     * ══════════════════════════════════════════ */
    _updateMobileTopbar(exif) {
        const dateEl = document.getElementById('lb-mobile-date');
        const subEl  = document.getElementById('lb-mobile-subline');
        if (!dateEl || !subEl) return;

        const raw = exif && (exif.datetime || exif.DateTimeOriginal || exif.CreateDate || exif.DateTime);
        let dateStr = '';
        let timeStr = '';
        if (raw) {
            // EXIF datetime usually "YYYY:MM:DD HH:MM:SS" or "YYYY-MM-DD HH:MM:SS"
            const m = String(raw).match(/^(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2})/);
            if (m) {
                const [_, y, mo, d, h, mi] = m;
                const lang = (window.MPD_I18N && window.MPD_I18N.lang) || navigator.language || 'de';
                try {
                    const dt = new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi));
                    dateStr = dt.toLocaleDateString(lang, { day: '2-digit', month: 'long', year: 'numeric' });
                    timeStr = `${h}:${mi}`;
                } catch (_) {
                    dateStr = `${d}.${mo}.${y}`;
                    timeStr = `${h}:${mi}`;
                }
            }
        }

        const img = this.allImages[this.currentIndex];
        dateEl.textContent = dateStr || (img && img.file) || '';

        // Step 1: render coordinates immediately (or empty if no GPS)
        const renderSub = (locStr) => {
            const parts = [];
            if (timeStr) parts.push(timeStr);
            if (locStr)  parts.push(locStr);
            subEl.textContent = parts.join(' · ');
        };

        const hasGps = exif && exif.gps && exif.gps.lat && exif.gps.lon;
        if (!hasGps) {
            renderSub('');
            return;
        }

        const lat = Number(exif.gps.lat);
        const lon = Number(exif.gps.lon);
        const coordStr = `${lat.toFixed(2)}, ${lon.toFixed(2)}`;
        renderSub(coordStr);

        // Step 2: async upgrade to city name (cached + abortable)
        this._fetchCityName(lat, lon, this.currentIndex).then((name) => {
            // Only apply if still on the same photo (race-safe via index check inside _fetchCityName)
            if (name) renderSub(name);
        }).catch(() => { /* abort or network — keep coordinates */ });
    }

    /**
     * Reverse-geo lookup with session cache + AbortController.
     * Cancels any in-flight request when user navigates to another photo.
     */
    async _fetchCityName(lat, lon, capturedIndex) {
        if (!this._geoCache) this._geoCache = new Map();
        const key = `${lat.toFixed(3)},${lon.toFixed(3)}`;

        // Session cache hit
        if (this._geoCache.has(key)) return this._geoCache.get(key);

        // Cancel previous in-flight request
        if (this._geoAbort) this._geoAbort.abort();
        this._geoAbort = new AbortController();

        const url = `${window.location.origin}/api/geo/reverse?lat=${lat}&lon=${lon}`;
        const resp = await fetch(url, { signal: this._geoAbort.signal });
        if (!resp.ok) return null;
        const data = await resp.json();

        // Stale-check: skip if user has navigated away
        if (this.currentIndex !== capturedIndex) return null;

        const name = data && data.name;
        this._geoCache.set(key, name || null);
        return name || null;
    }

    _resetMobileTopbar() {
        const dateEl = document.getElementById('lb-mobile-date');
        const subEl  = document.getElementById('lb-mobile-subline');
        const img    = this.allImages[this.currentIndex];
        if (dateEl) dateEl.textContent = (img && img.file) || '';
        if (subEl)  subEl.textContent  = '';
    }

    /* ══════════════════════════════════════════
     *  UTILITIES
     * ══════════════════════════════════════════ */
    _dist(a, b) {
        return Math.hypot(b.x - a.x, b.y - a.y);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}


/* ── Pointer capture helper (keeps events on wrapper during drag) ── */
function wrapper_capture(e) {
    try {
        const wrapper = document.getElementById('lb-image-wrapper');
        if (wrapper && wrapper.setPointerCapture) {
            wrapper.setPointerCapture(e.pointerId);
        }
    } catch (_) { /* ignore */ }
}

/* ── Download (global) ── */
function downloadImage() {
    const img = window.lightbox.allImages[window.lightbox.currentIndex];
    if (!img || !img.original) return;
    const link = document.createElement('a');
    link.href     = `${window.location.origin}${img.original}`;
    link.download = img.file;
    link.click();
}

/* ── Global instance ── */
window.lightbox = new Lightbox();

if (typeof module !== 'undefined' && module.exports) {
    module.exports = Lightbox;
}
