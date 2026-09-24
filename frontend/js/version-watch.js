/* MPD – Frontend Version Watch
 * On every visibilitychange, re-checks the current backend frontend-version.
 * If a new deploy happened while the WebAPK / browser tab was suspended in
 * memory, this triggers a hard reload — so the user never sees a stale UI
 * after backend restarts or pure frontend edits.
 *
 * Combined with `Cache-Control: no-store` on HTML responses, this closes the
 * "stale WebAPK shows yesterday's version" gap once and for all.
 */
(function () {
    'use strict';

    let known = null;

    async function check() {
        try {
            const r = await fetch('/api/version', { cache: 'no-store' });
            if (!r.ok) return;
            const data = await r.json();
            // build_token covers both backend (.py) and frontend (.html/.css/.js)
            // mtimes — pure frontend deploys would not move the static `version`
            // field, so comparing the token catches them too.
            const token = data && (data.build_token ?? data.version);
            if (token == null) return;
            if (known === null) {
                known = token;
                if (data.version) {
                    // Nur die Fusszeile. Den Badge baut und fuellt theme.js —
                    // beide schrieben ihn frueher mit unterschiedlichem Text,
                    // und welcher zu sehen war, entschied die Reihenfolge.
                    // Fusszeile traegt die volle Nummer: Produktversion
                    // plus Build — das ist die Angabe, die im Supportfall
                    // zaehlt. Der Badge oben zeigt nur die Produktversion.
                    // "Core" nur im oeffentlichen Kern (19.09.2026) — so ist
                    // im Supportfall sofort klar, welche Ausgabe laeuft.
                    const ed = data.edition === 'core' ? ' · Core' : '';
                    const v = data.build
                        ? `v${data.product || data.version} (Build ${data.build})${ed}`
                        : `v${data.version}${ed}`;
                    document.querySelectorAll('.mpd-version-text').forEach(el => el.textContent = v);
                }
                return;
            }
            if (token !== known) {
                // Deliberately no confirmation prompt — surfacing a "new
                // version" dialog would itself be a UX gotcha (esp. mid-edit).
                // visibilitychange means the user just came back to the tab,
                // so this is the safe moment to refresh silently.
                location.reload();
            }
        } catch (_) { /* offline or transient: skip silently */ }
    }

    // Orientation-change guard: rotating the device fires visibilitychange on
    // some platforms. A reload() triggered by a version change at exactly that
    // moment causes some WebView apps to navigate to their start URL (index.html)
    // instead of reloading the current page. Delay the check by 2 s so the
    // rotation is fully settled before we risk a reload.
    let _orientAt = 0;
    window.addEventListener('orientationchange', () => { _orientAt = Date.now(); }, { passive: true });
    window.addEventListener('resize', () => {
        if (window.innerWidth !== (window._vwLastW ?? window.innerWidth)) _orientAt = Date.now();
        window._vwLastW = window.innerWidth;
    }, { passive: true });

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;
        const delay = (Date.now() - _orientAt < 1500) ? 2500 : 0;
        setTimeout(check, delay);
    });

    // Prime the known-version on first load so subsequent checks have a baseline.
    check();
})();
