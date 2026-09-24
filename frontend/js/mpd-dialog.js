/* MPD Mini-Dialog + Toast
 * Replaces native confirm()/alert() for cases where styling/severity
 * matters (e.g. destructive deletes).
 *
 * API:
 *   window.mpdConfirm({
 *     title, subtitle, body, hint, hintType: 'warn'|'info',
 *     icon, cancelLabel, confirmLabel, destructive: true|false,
 *   }) → Promise<boolean>
 *
 *   window.mpdToast(message, { action, onAction, duration })
 */
(function() {
    'use strict';

    const style = document.createElement('style');
    style.id = 'mpd-dialog-style';
    style.textContent = `
    /* Deliberately theme-independent: MPD themes set --text theme-specifically,
       inheriting it here would lead to illegible combinations. */
    .mpd-dialog {
        position: fixed;
        inset: 0;
        margin: auto;
        width: min(420px, calc(100% - 2rem));
        height: max-content;
        max-height: calc(100vh - 2rem);
        border: none;
        border-radius: 14px;
        padding: 0;
        box-shadow: 0 20px 60px rgba(0,0,0,0.4);
        background: #ffffff;
        color: #1a1a1a;
        font-family: inherit;
    }
    .mpd-dialog::backdrop { background: rgba(0,0,0,0.55); backdrop-filter: blur(2px); }
    .mpd-dialog__inner {
        padding: 1.5rem 1.6rem 1.2rem;
        display: flex; flex-direction: column; gap: 0.75rem;
    }
    .mpd-dialog__icon { font-size: 2.1rem; line-height: 1; }
    .mpd-dialog__icon svg { width: 36px; height: 36px; }
    .mpd-dialog__title {
        margin: 0;
        font-size: 1.12rem; font-weight: 700;
        color: #111111;
    }
    /* Filename: plain monospace text, no field look */
    .mpd-dialog__subtitle {
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.9rem;
        color: #333333;
        word-break: break-all;
    }
    .mpd-dialog__body {
        white-space: pre-line;
        font-size: 0.95rem; line-height: 1.5;
        color: #555555;
    }

    /* Hint box: fully tinted, strong color, no form look */
    .mpd-dialog__hint {
        font-size: 0.9rem; line-height: 1.4;
        padding: 0.65rem 0.8rem;
        border-radius: 8px;
        background: rgba(80, 140, 220, 0.16);
        color: #14365f;
        font-weight: 500;
    }
    .mpd-dialog__hint--warn {
        background: rgba(229,75,75,0.18);
        color: #861616;
    }

    .mpd-dialog__actions {
        display: flex; justify-content: flex-end; gap: 0.6rem;
        margin-top: 0.6rem;
    }
    .mpd-dialog__btn {
        border: none; cursor: pointer;
        padding: 0.6rem 1.2rem;
        border-radius: 7px;
        font-size: 0.95rem; font-weight: 600;
        font-family: inherit;
        transition: background .12s, transform .08s, box-shadow .12s;
    }
    .mpd-dialog__btn--cancel {
        background: #e4e7eb;
        color: #1a1a1a;
    }
    .mpd-dialog__btn--cancel:hover { background: #d1d6dc; }
    .mpd-dialog__btn--cancel:focus-visible { outline: 2px solid #2E75B6; outline-offset: 2px; }
    .mpd-dialog__btn--confirm { background: #2E75B6; color: #ffffff; }
    .mpd-dialog__btn--confirm:hover { background: #1a5c9a; }
    .mpd-dialog__btn--destructive { background: #c62828; color: #ffffff; }
    .mpd-dialog__btn--destructive:hover { background: #a51f1f; box-shadow: 0 2px 8px rgba(198,40,40,0.35); }
    .mpd-dialog__btn:active { transform: scale(0.97); }

    .mpd-toast {
        position: fixed; left: 50%; bottom: 24px;
        transform: translateX(-50%) translateY(20px);
        background: rgba(30,30,30,0.96); color: #fff; padding: 0.75rem 1rem;
        border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.35);
        font-size: 0.92rem; display: flex; align-items: center; gap: 0.9rem;
        z-index: 12500; opacity: 0; transition: opacity .18s, transform .18s; /* ueber dem Einstellungsdialog (11500) */
        pointer-events: none; max-width: calc(100% - 2rem);
    }
    .mpd-toast.visible { opacity: 1; transform: translateX(-50%) translateY(0); pointer-events: auto; }
    .mpd-toast__action {
        background: transparent; border: none; color: #85b6ff;
        font-weight: 600; cursor: pointer; padding: 2px 6px; font-size: 0.92rem;
    }
    .mpd-toast__action:hover { color: #b4d1ff; }
    `;
    document.head.appendChild(style);

    window.mpdConfirm = function(opts) {
        opts = opts || {};
        return new Promise(resolve => {
            const dlg = document.createElement('dialog');
            dlg.className = 'mpd-dialog';

            const inner = document.createElement('div');
            inner.className = 'mpd-dialog__inner';

            if (opts.icon) {
                const ic = document.createElement('div');
                ic.className = 'mpd-dialog__icon';
                // icon may be SVG markup or plain text/emoji — innerHTML covers
                // both since emojis are valid as text content.
                ic.innerHTML = opts.icon;
                inner.appendChild(ic);
            }
            if (opts.title) {
                const t = document.createElement('h2');
                t.className = 'mpd-dialog__title';
                t.textContent = opts.title;
                inner.appendChild(t);
            }
            if (opts.subtitle) {
                const s = document.createElement('div');
                s.className = 'mpd-dialog__subtitle';
                s.textContent = opts.subtitle;
                inner.appendChild(s);
            }
            if (opts.body) {
                const b = document.createElement('div');
                b.className = 'mpd-dialog__body';
                b.textContent = opts.body;
                inner.appendChild(b);
            }
            if (opts.hint) {
                const h = document.createElement('div');
                h.className = 'mpd-dialog__hint' +
                    (opts.hintType === 'warn' ? ' mpd-dialog__hint--warn' : '');
                h.textContent = opts.hint;
                inner.appendChild(h);
            }

            const actions = document.createElement('div');
            actions.className = 'mpd-dialog__actions';

            const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
            const btnCancel = document.createElement('button');
            btnCancel.className = 'mpd-dialog__btn mpd-dialog__btn--cancel';
            btnCancel.textContent = opts.cancelLabel || _t('common.cancel', 'Abbrechen');

            const btnConfirm = document.createElement('button');
            btnConfirm.className = 'mpd-dialog__btn mpd-dialog__btn--confirm' +
                (opts.destructive ? ' mpd-dialog__btn--destructive' : '');
            btnConfirm.textContent = opts.confirmLabel || _t('common.ok', 'OK');

            // single: nur ein Knopf — der Ersatz fuer ein natives alert().
            if (!opts.single) actions.appendChild(btnCancel);
            actions.appendChild(btnConfirm);
            inner.appendChild(actions);
            dlg.appendChild(inner);
            document.body.appendChild(dlg);

            let done = false;
            const finish = (val) => {
                if (done) return;
                done = true;
                try { dlg.close(); } catch(_) {}
                setTimeout(() => dlg.remove(), 160);
                resolve(val);
            };

            btnCancel.addEventListener('click', () => finish(false));
            btnConfirm.addEventListener('click', () => finish(true));
            // Backdrop click = cancel
            dlg.addEventListener('click', (e) => { if (e.target === dlg) finish(false); });
            // ESC = cancel (native <dialog> behavior)
            dlg.addEventListener('cancel', (e) => { e.preventDefault(); finish(false); });

            dlg.showModal();
            // For destructive actions focus Cancel — so Enter doesn't accidentally trigger deletion.
            (opts.destructive && !opts.single ? btnCancel : btnConfirm).focus();
        });
    };

    // Ersatz fuer natives alert(): dieselbe Optik wie mpdConfirm, ein Knopf.
    // Grund wie oben — alert() ist im WebView wirkungslos oder traegt die
    // Zeile „Auf der Seite … steht:" (CLAUDE.md). Loest immer mit true auf.
    window.mpdAlert = function(opts) {
        return window.mpdConfirm(Object.assign({}, opts || {}, { single: true }));
    };

    let _activeToast = null;
    window.mpdToast = function(message, opts) {
        opts = opts || {};
        if (_activeToast) {
            _activeToast.remove();
            _activeToast = null;
        }
        const el = document.createElement('div');
        el.className = 'mpd-toast';
        const text = document.createElement('span');
        text.textContent = message;
        el.appendChild(text);

        if (opts.action && typeof opts.onAction === 'function') {
            const actBtn = document.createElement('button');
            actBtn.className = 'mpd-toast__action';
            actBtn.textContent = opts.action;
            actBtn.addEventListener('click', () => {
                try { opts.onAction(); } catch(_) {}
                dismiss();
            });
            el.appendChild(actBtn);
        }
        document.body.appendChild(el);
        requestAnimationFrame(() => el.classList.add('visible'));
        _activeToast = el;

        let t = setTimeout(dismiss, opts.duration || 3000);
        function dismiss() {
            clearTimeout(t);
            el.classList.remove('visible');
            setTimeout(() => { el.remove(); if (_activeToast === el) _activeToast = null; }, 200);
        }
    };
})();
