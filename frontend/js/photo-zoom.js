/* MPD photo-zoom view — standalone, picker-friendly
 *
 * Purpose: photo preview with pinch/pan/wheel zoom outside the lightbox.
 * No navigation, no slideshow, no sidebar — just zoom and close.
 *
 * Public API (window):
 *   openPhotoZoomView(src, opts)   → Promise<void>
 *     src  : image URL (typ. thumbnail xl)
 *     opts : { alt?, blurhash?, onClose? }
 *
 *   attachZoomTrigger(tileEl, srcOrGetter, opts)
 *     Attaches a loupe icon (hover, desktop) and a long-press handler
 *     (touch, >=400 ms) to a tile. On trigger opens the zoom view and
 *     suppresses the following click so the tile-select doesn't fire
 *     at the same time.
 *
 * UX reference: Google Photos / Apple Photos — hybrid trigger.
 */
(function() {
    'use strict';

    const LP_DURATION = 400;     // long-press ms
    const LP_MOVE_TOL = 10;      // px before long-press cancels
    const ZOOM_MIN    = 1;
    const ZOOM_MAX    = 6;
    const RB_GAIN     = 0.3;     // rubber-band resistance outside clamp
    const SNAP_DECAY  = 0.82;    // frame decay snap-back
    const FLING_DECAY = 0.93;    // frame decay momentum

    let view = null;   // active instance — only one at a time

    // ── Public: open view ─────────────────────────────────────────────
    window.openPhotoZoomView = function(src, opts) {
        opts = opts || {};
        if (view) return Promise.resolve();   // one instance is enough
        _injectStyles();
        return new Promise(resolve => {
            view = _buildView(src, opts, resolve);
        });
    };

    // ── Public: attach trigger to a tile ──────────────────────────────
    window.attachZoomTrigger = function(tileEl, srcOrGetter, opts) {
        if (!tileEl) return;
        _injectStyles();
        const getSrc = (typeof srcOrGetter === 'function')
            ? srcOrGetter
            : () => srcOrGetter;

        // Tile must be positioned — both for the loupe and any other
        // absolute children that may be added later.
        const cs = getComputedStyle(tileEl);
        if (cs.position === 'static') tileEl.style.position = 'relative';

        // Loupe icon optional (desktop hover-reveal via CSS @media hover).
        // Callers with their own trigger UX (e.g. hover-pan on landing cards) set opts.noLupe = true.
        if (!opts.noLupe) {
            const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
            const lupe = document.createElement('button');
            lupe.type = 'button';
            lupe.className = 'pz-lupe';
            lupe.title = _t('photo_zoom.btn_title', 'Vorschau');
            lupe.setAttribute('aria-label', _t('photo_zoom.btn_aria', 'Vorschau oeffnen'));
            lupe.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>';
            lupe.addEventListener('click', (e) => {
                e.stopPropagation();
                e.preventDefault();
                window.openPhotoZoomView(getSrc(), opts);
            });
            tileEl.appendChild(lupe);
        }

        // Long-Press (Touch/Pen) — unterdrueckt darauf folgenden click/dblclick
        let lpTimer = null;
        let sx = 0, sy = 0;

        const cancel = () => {
            if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
        };
        const fire = () => {
            lpTimer = null;
            try { navigator.vibrate && navigator.vibrate(15); } catch (_) {}
            _suppressTileClicks(tileEl);
            window.openPhotoZoomView(getSrc(), opts);
        };
        tileEl.addEventListener('pointerdown', (e) => {
            if (e.pointerType === 'mouse') return;
            sx = e.clientX; sy = e.clientY;
            cancel();
            lpTimer = setTimeout(fire, LP_DURATION);
        }, { passive: true });
        tileEl.addEventListener('pointermove', (e) => {
            if (!lpTimer) return;
            if (Math.hypot(e.clientX - sx, e.clientY - sy) > LP_MOVE_TOL) cancel();
        }, { passive: true });
        tileEl.addEventListener('pointerup',     cancel, { passive: true });
        tileEl.addEventListener('pointercancel', cancel, { passive: true });
        tileEl.addEventListener('pointerleave',  cancel, { passive: true });
    };

    // Suppress click/dblclick on tile + descendants for ~600ms. window-capture
    // runs guaranteed before target listeners, regardless of registration
    // order — the capture flag is not prioritized at the target (DOM spec).
    function _suppressTileClicks(tileEl) {
        const suppress = (e) => {
            if (tileEl.contains(e.target)) {
                e.stopImmediatePropagation();
                e.preventDefault();
            }
        };
        window.addEventListener('click',    suppress, true);
        window.addEventListener('dblclick', suppress, true);
        setTimeout(() => {
            window.removeEventListener('click',    suppress, true);
            window.removeEventListener('dblclick', suppress, true);
        }, 600);
    }

    // ── View build ──────────────────────────────────────────────────────
    function _buildView(src, opts, resolve) {
        const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
        const ov = document.createElement('div');
        ov.className = 'pz-overlay';
        ov.setAttribute('role', 'dialog');
        ov.setAttribute('aria-label', _t('photo_zoom.overlay_aria', 'Foto-Vorschau'));
        ov.tabIndex = -1;

        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'pz-close';
        closeBtn.setAttribute('aria-label', _t('common.close', 'Schliessen'));
        closeBtn.innerHTML = '&times;';
        ov.appendChild(closeBtn);

        const wrap = document.createElement('div');
        wrap.className = 'pz-wrap';
        ov.appendChild(wrap);

        const img = document.createElement('img');
        img.className = 'pz-img';
        img.alt = opts.alt || '';
        img.decoding = 'async';

        // BlurHash placeholder until the real image has loaded
        if (opts.blurhash && window.MPDBlurHash) {
            const bh = window.MPDBlurHash.toDataURL(opts.blurhash, 32, 32);
            if (bh) wrap.style.backgroundImage = `url("${bh}")`;
        }

        img.addEventListener('load', () => {
            wrap.style.backgroundImage = '';
            inst.ready = true;
            _applyTransform();
        });
        img.src = src;
        wrap.appendChild(img);

        // Progressive upgrade: xl is sharp up to ~fit immediately. For real
        // zoom-in (up to 6×) we need more pixels. We load 'full' (4096) in
        // the background and swap the img src once ready — zoom state stays
        // intact because only the pixel material changes.
        if (opts.srcHigh && opts.srcHigh !== src) {
            const high = new Image();
            high.decoding = 'async';
            high.addEventListener('load', () => {
                if (inst.closed) return;
                img.src = opts.srcHigh;   // browser uses the already decoded cache
                _applyTransform();
            });
            high.addEventListener('error', () => { /* silently stay on xl */ });
            high.src = opts.srcHigh;
        }

        document.body.appendChild(ov);
        ov.focus();
        document.body.style.overflow = 'hidden';

        const inst = {
            ov, wrap, img, closeBtn, resolve, opts,
            zoom: 1, panX: 0, panY: 0,
            pointers: {},
            lastDist: null, lastMidX: null, lastMidY: null,
            panStartX: 0, panStartY: 0, panOriginX: 0, panOriginY: 0,
            lastTapTime: 0, lastTapX: 0, lastTapY: 0,
            mouseDrag: false, mouseStartX: 0, mouseStartY: 0, mouseOrigX: 0, mouseOrigY: 0,
            swipeStartX: 0, swipeStartY: 0, swipeStartT: 0,
            velHistory: [],   // {x,y,t} for momentum estimation
            flingRAF: null,
            snapRAF: null,
            ready: false,
            closed: false,
        };

        // ── Listener ──
        const onKey = (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                _close();
            }
        };
        document.addEventListener('keydown', onKey, true);

        closeBtn.addEventListener('click', (e) => { e.stopPropagation(); _close(); });
        // Backdrop / letterbox click closes — but not while zoomed
        // (then it's usually the end of a pan-gesture click).
        ov.addEventListener('click', (e) => {
            if ((e.target === ov || e.target === wrap) && inst.zoom <= 1.02) _close();
        });

        wrap.style.touchAction = 'none';
        wrap.addEventListener('pointerdown',   _onPointerDown,   { passive: false });
        wrap.addEventListener('pointermove',   _onPointerMove,   { passive: false });
        wrap.addEventListener('pointerup',     _onPointerUp,     { passive: false });
        wrap.addEventListener('pointercancel', _onPointerCancel, { passive: false });
        wrap.addEventListener('wheel',         _onWheel,         { passive: false });
        wrap.addEventListener('dblclick',      _onDblClick);

        window.addEventListener('resize', _onResize);

        inst._cleanup = () => {
            document.removeEventListener('keydown', onKey, true);
            window.removeEventListener('resize', _onResize);
        };

        // ── Close ──
        function _close() {
            if (inst.closed) return;
            inst.closed = true;
            if (inst.flingRAF) cancelAnimationFrame(inst.flingRAF);
            if (inst.snapRAF)  cancelAnimationFrame(inst.snapRAF);
            inst._cleanup();
            ov.remove();
            document.body.style.overflow = '';
            view = null;
            if (typeof opts.onClose === 'function') { try { opts.onClose(); } catch (_) {} }
            resolve();
        }
        inst.close = _close;

        // ── Transform ──
        function _maxPan() {
            const wW = wrap.clientWidth,  wH = wrap.clientHeight;
            const nw = img.naturalWidth  || wW;
            const nh = img.naturalHeight || wH;
            const fit = Math.min(wW / nw, wH / nh);
            const fitW = nw * fit, fitH = nh * fit;
            return {
                x: Math.max(0, (fitW * inst.zoom - wW) / 2),
                y: Math.max(0, (fitH * inst.zoom - wH) / 2),
            };
        }
        function _softClamp(v, max) {
            if (v > max)  return max  + (v - max)  * RB_GAIN;
            if (v < -max) return -max + (v + max)  * RB_GAIN;
            return v;
        }
        function _applyTransform() {
            const m = _maxPan();
            // Hard clamp without rubber-band during fling/snap, otherwise with rubber-band
            const soft = !!inst.gesture;
            const dx = soft ? _softClamp(inst.panX, m.x) : Math.max(-m.x, Math.min(m.x, inst.panX));
            const dy = soft ? _softClamp(inst.panY, m.y) : Math.max(-m.y, Math.min(m.y, inst.panY));
            if (!soft) { inst.panX = dx; inst.panY = dy; }
            img.style.transform = `scale(${inst.zoom}) translate(${dx/inst.zoom}px, ${dy/inst.zoom}px)`;
            img.style.transformOrigin = 'center center';
            img.style.cursor = inst.zoom > 1 ? (inst.mouseDrag ? 'grabbing' : 'grab') : 'default';
        }
        function _resetZoom() {
            inst.zoom = 1; inst.panX = 0; inst.panY = 0;
            img.style.transform = '';
        }

        function _onResize() { _applyTransform(); }

        // ── Snap-back after rubber-band release ──
        function _snapBack() {
            if (inst.snapRAF) cancelAnimationFrame(inst.snapRAF);
            const step = () => {
                const m = _maxPan();
                const tx = Math.max(-m.x, Math.min(m.x, inst.panX));
                const ty = Math.max(-m.y, Math.min(m.y, inst.panY));
                const ddx = tx - inst.panX, ddy = ty - inst.panY;
                if (Math.hypot(ddx, ddy) < 0.5) {
                    inst.panX = tx; inst.panY = ty;
                    inst.snapRAF = null;
                    _applyTransform();
                    return;
                }
                inst.panX += ddx * (1 - SNAP_DECAY);
                inst.panY += ddy * (1 - SNAP_DECAY);
                _applyTransform();
                inst.snapRAF = requestAnimationFrame(step);
            };
            inst.snapRAF = requestAnimationFrame(step);
        }

        // ── Momentum (Fling) ──
        function _startFling(vx, vy) {
            if (inst.flingRAF) cancelAnimationFrame(inst.flingRAF);
            const step = () => {
                inst.panX += vx;
                inst.panY += vy;
                vx *= FLING_DECAY;
                vy *= FLING_DECAY;
                const m = _maxPan();
                const outside = (inst.panX > m.x || inst.panX < -m.x || inst.panY > m.y || inst.panY < -m.y);
                _applyTransform();
                if (Math.hypot(vx, vy) < 0.4 || outside) {
                    inst.flingRAF = null;
                    inst.gesture = false;
                    _snapBack();
                    return;
                }
                inst.flingRAF = requestAnimationFrame(step);
            };
            inst.gesture = true;  // softClamp during fling
            inst.flingRAF = requestAnimationFrame(step);
        }

        // ── Pointer Down ──
        function _onPointerDown(e) {
            // NO preventDefault here — otherwise Chromium swallows the
            // following click and the backdrop-close stops working.
            // Browser gestures (scroll/zoom) are already blocked by touch-action:none.
            try { wrap.setPointerCapture(e.pointerId); } catch (_) {}
            if (inst.flingRAF) { cancelAnimationFrame(inst.flingRAF); inst.flingRAF = null; }
            if (inst.snapRAF)  { cancelAnimationFrame(inst.snapRAF);  inst.snapRAF  = null; }

            inst.pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
            const count = Object.keys(inst.pointers).length;

            if (count === 1) {
                if (e.pointerType === 'mouse') {
                    inst.mouseDrag   = true;
                    inst.mouseStartX = e.clientX; inst.mouseStartY = e.clientY;
                    inst.mouseOrigX  = inst.panX; inst.mouseOrigY  = inst.panY;
                } else {
                    inst.panStartX  = e.clientX; inst.panStartY  = e.clientY;
                    inst.panOriginX = inst.panX; inst.panOriginY = inst.panY;
                    inst.swipeStartX = e.clientX; inst.swipeStartY = e.clientY;
                    inst.swipeStartT = Date.now();
                    inst.velHistory = [{ x: e.clientX, y: e.clientY, t: performance.now() }];
                }
                inst.gesture = true;
            } else if (count === 2) {
                const pts = Object.values(inst.pointers);
                inst.lastDist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
                inst.lastMidX = (pts[0].x + pts[1].x) / 2;
                inst.lastMidY = (pts[0].y + pts[1].y) / 2;
            }
        }

        // ── Pointer Move ──
        function _onPointerMove(e) {
            if (!inst.pointers[e.pointerId]) return;
            e.preventDefault();
            inst.pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
            const count = Object.keys(inst.pointers).length;

            if (e.pointerType === 'mouse') {
                if (!inst.mouseDrag || inst.zoom <= 1) return;
                inst.panX = inst.mouseOrigX + (e.clientX - inst.mouseStartX);
                inst.panY = inst.mouseOrigY + (e.clientY - inst.mouseStartY);
                _applyTransform();
                return;
            }

            if (count === 2) {
                const pts = Object.values(inst.pointers);
                const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
                const midX = (pts[0].x + pts[1].x) / 2;
                const midY = (pts[0].y + pts[1].y) / 2;
                if (inst.lastDist) {
                    const ratio = dist / inst.lastDist;
                    const newZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, inst.zoom * ratio));
                    inst.panX = inst.panX * (newZoom / inst.zoom) + (midX - inst.lastMidX);
                    inst.panY = inst.panY * (newZoom / inst.zoom) + (midY - inst.lastMidY);
                    inst.zoom = newZoom;
                    _applyTransform();
                }
                inst.lastDist = dist; inst.lastMidX = midX; inst.lastMidY = midY;
            } else if (count === 1 && inst.zoom > 1) {
                inst.panX = inst.panOriginX + (e.clientX - inst.panStartX);
                inst.panY = inst.panOriginY + (e.clientY - inst.panStartY);
                _applyTransform();
                // Velocity history (last 80ms) for momentum
                const now = performance.now();
                inst.velHistory.push({ x: e.clientX, y: e.clientY, t: now });
                inst.velHistory = inst.velHistory.filter(p => now - p.t < 80);
            }
        }

        // ── Pointer Up ──
        function _onPointerUp(e) {
            if (!inst.pointers[e.pointerId]) return;
            e.preventDefault();
            const wasTwo = (Object.keys(inst.pointers).length === 2);
            delete inst.pointers[e.pointerId];
            const remaining = Object.keys(inst.pointers).length;

            if (e.pointerType === 'mouse') {
                inst.mouseDrag = false;
                inst.gesture = false;
                _applyTransform();
                return;
            }

            if (wasTwo && remaining === 1) {
                inst.lastDist = null;
                if (inst.zoom < 1.05) { _resetZoom(); _applyTransform(); }
                const pt = Object.values(inst.pointers)[0];
                if (pt) {
                    inst.panStartX  = pt.x; inst.panStartY  = pt.y;
                    inst.panOriginX = inst.panX; inst.panOriginY = inst.panY;
                }
                return;
            }
            if (remaining > 0) return;

            inst.lastDist = null;

            // Tap and swipe detection
            const dx = e.clientX - inst.swipeStartX;
            const dy = e.clientY - inst.swipeStartY;
            const moved = Math.hypot(dx, dy);

            if (moved < 10) {
                // Tap: at zoom > 1 reset, otherwise check for double-tap
                if (inst.zoom > 1) {
                    _resetZoom();
                    inst.gesture = false;
                    _applyTransform();
                    return;
                }
                const now = Date.now();
                if (now - inst.lastTapTime < 300
                    && Math.hypot(e.clientX - inst.lastTapX, e.clientY - inst.lastTapY) < 50) {
                    inst.lastTapTime = 0;
                    _zoomTo(e.clientX, e.clientY, 2.5);
                    return;
                }
                inst.lastTapTime = now;
                inst.lastTapX = e.clientX; inst.lastTapY = e.clientY;
                return;
            }

            // Swipe-down on unzoomed image → close (iOS pattern)
            if (inst.zoom <= 1.02) {
                if (dy > 80 && Math.abs(dy) > Math.abs(dx)) {
                    _close();
                    return;
                }
                // Ignore swipe-up/sideways at zoom=1 — no navigation in this view
                inst.gesture = false;
                return;
            }

            // Pan finished: momentum + snap-back
            const hist = inst.velHistory;
            if (hist.length >= 2) {
                const a = hist[0], b = hist[hist.length - 1];
                const dt = Math.max(1, b.t - a.t);
                const vx = (b.x - a.x) / dt * 16;  // px per frame (~16ms)
                const vy = (b.y - a.y) / dt * 16;
                if (Math.hypot(vx, vy) > 2) {
                    _startFling(vx, vy);
                    return;
                }
            }
            inst.gesture = false;
            _snapBack();
        }

        function _onPointerCancel(e) {
            delete inst.pointers[e.pointerId];
            inst.lastDist = null;
            inst.mouseDrag = false;
            inst.gesture = false;
            _snapBack();
        }

        // ── Mouse wheel ──
        function _onWheel(e) {
            e.preventDefault();
            /* Multiplikativ wie die Pinch-Geste weiter oben — additiv
             * wuerde jede Raste bei hohem Zoom kaum noch etwas bewirken. */
            const STEP = 1.15;
            const factor = e.deltaY > 0 ? 1 / STEP : STEP;
            const newZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, inst.zoom * factor));
            if (newZoom === ZOOM_MIN) { _resetZoom(); _applyTransform(); return; }
            _zoomTowardCursor(e.clientX, e.clientY, newZoom);
        }

        function _zoomTowardCursor(cx, cy, newZoom) {
            const r = wrap.getBoundingClientRect();
            const mx = cx - r.left - r.width  / 2;
            const my = cy - r.top  - r.height / 2;
            const ratio = newZoom / inst.zoom;
            inst.panX = mx + (inst.panX - mx) * ratio;
            inst.panY = my + (inst.panY - my) * ratio;
            inst.zoom = newZoom;
            _applyTransform();
        }

        function _zoomTo(cx, cy, targetZoom) {
            const r = wrap.getBoundingClientRect();
            const px = cx - r.left - r.width  / 2;
            const py = cy - r.top  - r.height / 2;
            inst.zoom = targetZoom;
            inst.panX = px * (1 - targetZoom);
            inst.panY = py * (1 - targetZoom);
            _applyTransform();
        }

        // ── Double click (desktop) ──
        function _onDblClick(e) {
            e.preventDefault();
            if (inst.zoom > 1) {
                _resetZoom();
                _applyTransform();
            } else {
                _zoomTo(e.clientX, e.clientY, 2.5);
            }
        }

        return inst;
    }

    // ── Styles, injected once ──
    function _injectStyles() {
        if (document.getElementById('pz-styles')) return;
        const st = document.createElement('style');
        st.id = 'pz-styles';
        st.textContent = `
            .pz-overlay {
                position: fixed; inset: 0;
                background: rgba(0, 0, 0, 0.94);
                z-index: 10500;
                display: flex; align-items: center; justify-content: center;
                -webkit-tap-highlight-color: transparent;
            }
            .pz-wrap {
                position: relative;
                width: 100%; height: 100%;
                display: flex; align-items: center; justify-content: center;
                overflow: hidden;
                background-repeat: no-repeat;
                background-position: center;
                background-size: contain;
            }
            .pz-img {
                max-width: 100%; max-height: 100%;
                width: auto; height: auto;
                object-fit: contain;
                user-select: none;
                -webkit-user-drag: none;
                will-change: transform;
            }
            .pz-close {
                position: fixed;
                top: calc(10px + env(safe-area-inset-top, 0px));
                right: calc(10px + env(safe-area-inset-right, 0px));
                width: 40px; height: 40px;
                border-radius: 50%;
                border: none;
                background: rgba(0, 0, 0, 0.5);
                color: #fff;
                font-size: 1.6rem; line-height: 1;
                cursor: pointer;
                z-index: 2;
                display: flex; align-items: center; justify-content: center;
                transition: background .15s;
            }
            .pz-close:hover { background: rgba(0, 0, 0, 0.75); }
            .pz-close:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }

            /* ── Tile loupe: only on hover for hover-capable devices ── */
            .pz-lupe {
                position: absolute;
                top: 4px; right: 4px;
                width: 26px; height: 26px;
                border-radius: 50%;
                border: none;
                background: rgba(0, 0, 0, 0.55);
                color: #fff;
                cursor: pointer;
                padding: 0;
                display: flex; align-items: center; justify-content: center;
                opacity: 0;
                pointer-events: none;
                transition: opacity .12s, background .12s;
                z-index: 2;
            }
            @media (hover: hover) {
                .pz-lupe:hover { background: rgba(0, 0, 0, 0.8); }
                *:hover > .pz-lupe { opacity: 1; pointer-events: auto; }
            }
            .pz-lupe:focus-visible {
                opacity: 1; pointer-events: auto;
                outline: 2px solid #fff; outline-offset: 1px;
            }
        `;
        document.head.appendChild(st);
    }
})();
