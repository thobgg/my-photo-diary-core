/* MPD – Drag & Drop (Silent Sorting)
 * initDragAndDrop, destroyDragAndDrop, addDragHandles, removeDragHandles,
 * applyJustifiedSizesFromDOM, handleDragEnd
 */

// ============================================
// Drag & drop – "silent sorting" (Google Photos style)
// SortableJS only delivers events, DOM moves happen
// only on drop → no reflow during drag → no flicker.
// ============================================

function initDragAndDrop() {
    const content = document.getElementById('content');
    if (sortableInstance) sortableInstance.destroy();

    // Drop indicator: absolutely positioned, causes zero reflow
    let indicator = document.getElementById('drop-indicator');
    if (!indicator) {
        indicator = document.createElement('div');
        indicator.id = 'drop-indicator';
        document.body.appendChild(indicator);
    }

    let lastTarget = null;
    let insertBefore = true;
    let draggedEl = null;
    let rafId = null;

    // ── Compute indicator position from cursor coordinates ──
    function updateIndicator(clientX, clientY) {
        if (!draggedEl) return;

        const elements = content.querySelectorAll('.draggable-element');
        let bestTarget = null;
        let bestBefore = true;
        let bestDist = Infinity;

        for (const el of elements) {
            if (el === draggedEl) continue;

            const rect = el.getBoundingClientRect();
            const isText = el.classList.contains('text-block');

            if (isText) {
                // Text: check vertical proximity, decide above/below
                if (clientY >= rect.top - 30 && clientY <= rect.bottom + 30) {
                    const midY = rect.top + rect.height / 2;
                    const dist = Math.abs(clientY - midY);
                    if (dist < bestDist) {
                        bestDist = dist;
                        bestTarget = el;
                        bestBefore = clientY < midY;
                    }
                }
            } else {
                // Photo: only if cursor is at row height
                if (clientY >= rect.top - 10 && clientY <= rect.bottom + 10) {
                    const midX = rect.left + rect.width / 2;
                    const dist = Math.abs(clientX - midX);
                    if (dist < bestDist) {
                        bestDist = dist;
                        bestTarget = el;
                        bestBefore = clientX < midX;
                    }
                }
            }
        }

        if (!bestTarget) {
            indicator.style.display = 'none';
            lastTarget = null;
            return;
        }

        lastTarget = bestTarget;
        insertBefore = bestBefore;

        const rect = bestTarget.getBoundingClientRect();
        const isText = bestTarget.classList.contains('text-block');

        if (isText) {
            // Horizontal bar above/below text block
            indicator.className = 'drop-indicator drop-indicator-h';
            indicator.style.width  = `${rect.width}px`;
            indicator.style.height = '';
            indicator.style.top    = `${(bestBefore ? rect.top - 2 : rect.bottom) + window.scrollY}px`;
            indicator.style.left   = `${rect.left + window.scrollX}px`;
        } else {
            // Vertical bar left/right of photo
            indicator.className = 'drop-indicator drop-indicator-v';
            indicator.style.width  = '';
            indicator.style.height = `${rect.height}px`;
            indicator.style.top    = `${rect.top + window.scrollY}px`;
            indicator.style.left   = `${(bestBefore ? rect.left - 2 : rect.right) + window.scrollX}px`;
        }
        indicator.style.display = 'block';
    }

    // ── Pointer tracking (throttled via rAF) ──
    function onPointerMove(e) {
        const clientX = e.clientX ?? (e.touches && e.touches[0] && e.touches[0].clientX);
        const clientY = e.clientY ?? (e.touches && e.touches[0] && e.touches[0].clientY);
        if (clientX == null) return;
        if (rafId) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(() => updateIndicator(clientX, clientY));
    }

    // ── SortableJS: only as event source ──
    sortableInstance = Sortable.create(content, {
        animation: 0,
        forceFallback: true,
        fallbackTolerance: 5,
        sort: false,
        delay: 500,
        delayOnTouchOnly: true,
        touchStartThreshold: 8,
        draggable: '.draggable-element',
        // .text-inner stays draggable — long-press on the text lifts the block.
        // Short tap on .text-inner is handled in album-edit.js via pointerdown/up
        // timing (sub-300ms, sub-8px → focus + cursor-end). The two windows do
        // not overlap, so SortableJS and the tap detector cannot both fire.
        filter: '.delete-text-btn, .style-select-btn, .locked-toggle-btn, .remove-photo-btn, .text-edit-toolbar, .text-toolbar-btn, .text-ok-btn, .emoji-picker-btn, .emoji-picker-popup, .emoji-pick',
        preventOnFilter: false,
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        dragClass: 'sortable-drag',
        scroll: true,
        scrollSensitivity: 80,
        scrollSpeed: 12,

        onStart(evt) {
            draggedEl = evt.item;
            lastTarget = null;
            indicator.style.display = 'none';
            // Haptic confirm on drag-lift, like Android's HapticFeedbackConstants.LONG_PRESS.
            // No-op on devices without Vibration API (iOS Safari, desktop).
            if (navigator.vibrate) navigator.vibrate(20);
            document.addEventListener('pointermove', onPointerMove);
            document.addEventListener('touchmove', onPointerMove, { passive: true });
        },

        onEnd(evt) {
            document.removeEventListener('pointermove', onPointerMove);
            document.removeEventListener('touchmove', onPointerMove);
            if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
            indicator.style.display = 'none';

            if (!lastTarget || lastTarget === evt.item) {
                draggedEl = null;
                lastTarget = null;
                return;
            }

            // Single DOM move → one reflow
            if (insertBefore) {
                content.insertBefore(evt.item, lastTarget);
            } else {
                content.insertBefore(evt.item, lastTarget.nextSibling);
            }

            draggedEl = null;
            lastTarget = null;

            // Reuse existing reorder logic
            handleDragEnd(evt);
        }
    });
}

function destroyDragAndDrop() {
    if (sortableInstance) {
        sortableInstance.destroy();
        sortableInstance = null;
    }
    const indicator = document.getElementById('drop-indicator');
    if (indicator) indicator.style.display = 'none';
}

function addDragHandles() {
    document.querySelectorAll('.draggable-element').forEach(item => {
        if (!item.querySelector('.drag-handle')) {
            const handle = document.createElement('div');
            handle.className = 'drag-handle';
            handle.innerHTML = '⋮⋮';
            handle.title = (window.MPD_I18N ? window.MPD_I18N.t('album.drag_handle') : 'Ziehen zum Verschieben');
            item.insertBefore(handle, item.firstChild);
        }
    });
}

function removeDragHandles() {
    document.querySelectorAll('.drag-handle').forEach(h => h.remove());
}

// Computes justified sizes from the current DOM order instead of albumData.elements.
function applyJustifiedSizesFromDOM(isDragging = false) {
    const content = document.getElementById('content');
    if (!content || !content.classList.contains('edit-grid-mode')) return;

    const containerWidth = content.getBoundingClientRect().width || window.innerWidth;

    // DOM order → temporary element list for calcJustifiedSizes
    const domElements = [];
    content.querySelectorAll('.draggable-element').forEach(el => {
        const id = el.dataset.id;
        const found = albumData.elements.find(e => e.id === id);
        if (found) domElements.push(found);
    });

    const sizeMap = calcJustifiedSizes(domElements, containerWidth);

    content.querySelectorAll('.photo-grid.draggable-element[data-type="photo"]').forEach(el => {
        if (isDragging && el.classList.contains('sortable-ghost')) return;

        const size = sizeMap.get(el.dataset.id);
        if (!size) return;

        if (size.width && size.height) {
            /* Justified layout: exact dimensions (desktop multi,
               desktop single, mobile multi per new scheme). */
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
            /* Mobile single: full width, aspect-ratio determines height. */
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

function handleDragEnd(evt) {
    const newOrder = [];
    const foundIds = new Set();

    document.querySelectorAll('#content .draggable-element').forEach(domElem => {
        if (domElem.dataset.type === 'photo-row') {
            const ids = JSON.parse(domElem.dataset.ids || '[]');
            ids.forEach(id => {
                const found = albumData.elements.find(e => e.id === id);
                if (found) { newOrder.push(found); foundIds.add(id); }
            });
        } else if (domElem.dataset.id) {
            const found = albumData.elements.find(e => e.id === domElem.dataset.id);
            if (found) { newOrder.push(found); foundIds.add(found.id); }
        }
    });

    // Preserve elements not in DOM (error, tour, etc.) at original position
    albumData.elements.forEach((elem, origIdx) => {
        if (!foundIds.has(elem.id)) {
            let insertAt = newOrder.length;
            for (let i = origIdx - 1; i >= 0; i--) {
                const pos = newOrder.findIndex(e => e.id === albumData.elements[i].id);
                if (pos !== -1) { insertAt = pos + 1; break; }
            }
            newOrder.splice(insertAt, 0, elem);
        }
    });

    pushUndoState();
    albumData.elements = newOrder;
    hasUnsavedChanges = true;
    updateSaveIndicator('unsaved');
    applyJustifiedSizesFromDOM(false);
    scheduleAutoSave();
}
