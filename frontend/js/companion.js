/* MPD Write Companion — frontend panel
 * Calls /api/companion/* and displays up to 3 phrasing variants.
 * Global: window.Companion.{isEnabled, init, openFor, close}
 */
(function () {
    'use strict';

    const state = {
        enabled   : false,
        checked   : false,
        model     : '',
        provider  : '',
        panel     : null,
        targetElem: null,       // the .text-inner element
    };

    async function init() {
        if (state.checked) return state.enabled;
        state.checked = true;
        try {
            const r = await fetch('/api/companion/status', { credentials: 'same-origin' });
            if (!r.ok) {
                console.warn('[Companion] status HTTP', r.status);
                state.enabled = false;
                return false;
            }
            const d = await r.json();
            console.log('[Companion] status', d);
            state.enabled  = !!d.enabled;
            state.model    = d.model    || '';
            state.provider = d.provider || '';
        } catch(e) {
            console.error('[Companion] init error', e);
            state.enabled = false;
        }
        console.log('[Companion] enabled =', state.enabled);
        return state.enabled;
    }

    function isEnabled() { return state.enabled; }

    function close() {
        if (state.panel) {
            state.panel.remove();
            state.panel = null;
        }
        state.targetElem = null;
        document.removeEventListener('click', onOutsideClick, true);
        document.removeEventListener('keydown', onEscape, true);
    }

    function onOutsideClick(e) {
        if (!state.panel) return;
        if (state.panel.contains(e.target)) return;
        // Do NOT close on clicks on the associated companion button
        if (e.target.closest && e.target.closest('.companion-btn')) return;
        close();
    }

    function onEscape(e) {
        if (e.key === 'Escape') close();
    }

    function buildPanel(anchor) {
        close();

        const panel = document.createElement('div');
        panel.className = 'companion-panel';
        const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
        panel.innerHTML = `
            <div class="companion-panel-header">
                <span class="companion-panel-title">${_t('companion.panel_title', '✨ Formulierungshilfe')}</span>
                <button class="companion-panel-close" title="${_t('companion.close_title', 'Close')}">×</button>
            </div>
            <div class="companion-panel-body">
                <div class="companion-panel-loading">
                    <span class="companion-spinner"></span>
                    <span>${_t('companion.thinking', 'Denke nach…')}</span>
                </div>
            </div>
            <div class="companion-panel-footer">${state.model ? escapeHtml(state.model) + ' · ' : ''}${_t('companion.footer', 'nur Formulierung')}</div>
        `;
        panel.querySelector('.companion-panel-close').addEventListener('click', close);

        document.body.appendChild(panel);
        positionPanel(panel, anchor);
        state.panel = panel;

        setTimeout(() => {
            document.addEventListener('click', onOutsideClick, true);
            document.addEventListener('keydown', onEscape, true);
        }, 0);

        return panel;
    }

    function positionPanel(panel, anchor) {
        if (window.innerWidth <= 600) return; // mobile: fixed via CSS
        const r = anchor.getBoundingClientRect();
        const pw = Math.min(panel.offsetWidth || 360, 480);
        let left = window.scrollX + r.left;
        let top  = window.scrollY + r.bottom + 6;

        if (left + pw > window.scrollX + window.innerWidth - 12) {
            left = window.scrollX + window.innerWidth - pw - 12;
        }
        if (left < window.scrollX + 8) left = window.scrollX + 8;

        panel.style.left = left + 'px';
        panel.style.top  = top  + 'px';
    }

    function renderError(panel, msg) {
        const body = panel.querySelector('.companion-panel-body');
        body.innerHTML = `<div class="companion-panel-error">${escapeHtml(msg)}</div>`;
    }

    function renderSuggestions(panel, suggestions, textInner) {
        const body = panel.querySelector('.companion-panel-body');
        body.innerHTML = '';

        suggestions.forEach(s => {
            const div = document.createElement('div');
            div.className = 'companion-suggestion';
            div.innerHTML = `
                <div class="companion-suggestion-label"></div>
                <div class="companion-suggestion-text"></div>
            `;
            div.querySelector('.companion-suggestion-label').textContent = s.label;
            div.querySelector('.companion-suggestion-text').textContent = s.text;
            div.title = (window.MPD_I18N ? window.MPD_I18N.t('companion.apply_title') : 'Click to apply');
            div.addEventListener('click', () => applySuggestion(textInner, s.text));
            body.appendChild(div);
        });
    }

    function applySuggestion(textInner, newText) {
        if (!textInner) { close(); return; }
        textInner.textContent = newText;
        // Trigger input event so existing listeners (auto-save, undo) fire
        textInner.dispatchEvent(new Event('input', { bubbles: true }));
        close();
    }

    async function openFor(textInner, anchorBtn) {
        if (!state.enabled) return;
        if (!textInner) return;

        state.targetElem = textInner;
        const text = (textInner.textContent || '').trim();

        const panel = buildPanel(anchorBtn || textInner);

        const _t = (k, vars, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
        if (!text) {
            renderError(panel, _t('companion.err_empty', null, 'Bitte erst etwas schreiben, dann helfe ich beim Feinschliff.'));
            return;
        }

        anchorBtn?.classList.add('is-busy');

        try {
            const r = await fetch('/api/companion/suggest', {
                method     : 'POST',
                headers    : { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body       : JSON.stringify({ text }),
            });
            if (!r.ok) {
                const msg = r.status === 503 ? _t('companion.err_not_configured', null, 'Companion ist nicht konfiguriert.')
                          : r.status === 413 ? _t('companion.err_too_long', null, 'Text zu lang.')
                          : _t('companion.err_status', { status: r.status }, `Fehler ${r.status}`);
                renderError(panel, msg);
                return;
            }
            const d = await r.json();
            if (!d.suggestions?.length) {
                renderError(panel, _t('companion.err_no_suggestions', null, 'No suggestions received.'));
                return;
            }
            renderSuggestions(panel, d.suggestions, textInner);
        } catch {
            renderError(panel, _t('companion.err_connection', null, 'Verbindung zu Companion fehlgeschlagen.'));
        } finally {
            anchorBtn?.classList.remove('is-busy');
        }
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
        }[c]));
    }

    function reset() {
        state.checked  = false;
        state.enabled  = false;
        state.model    = '';
        state.provider = '';
    }

    window.Companion = { init, isEnabled, openFor, close, reset };
})();
