/**
 * MPD Themes – Shared Theme Switcher
 * Stores selection in localStorage, sets data-theme on <body>
 */

/* ── Version ─────────────────────────────────────────────────────────────
   Beides kommt aus /api/version, also aus backend/version.txt, die der
   post-commit-Hook auf 2.0.<commit-count> setzt.

   Frueher stand hier ein festes '2.0.0'. Das war nicht nur veraltet (die
   echte Version war 2.0.135), sondern kollidierte auch: version-watch.js
   schreibt denselben Badge mit dem Wert aus /api/version, theme.js mit dem
   festen. Welcher zu sehen war, entschied die Reihenfolge — auf dem Tablet
   gewann das feste. Jetzt gibt es nur noch eine Quelle. */
let MPD_VERSION = '';     // volle Version <Produkt>.<Build>, aus /api/version
let MPD_PRODUCT = '';     // nur die Produktversion, z. B. "3.0"
let MPD_BUILD   = '';     // Build-DATUM (nicht die Build-Nummer)
/* ──────────────────────────────────────────────────────────────────────── */

// Theme icons as inline SVG, same Lucide-outline vocabulary as the rest of
// the app. Mood reference is preserved (moon / sun / book / snow / sunrise /
// layers / hexagon / film / grid), but rendering is consistently monochrome
// instead of relying on platform emoji.
const _T_SVG = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
const MPD_THEMES = [
    { id: 'dark',      label: 'Dark',        icon: `<svg viewBox="0 0 24 24" ${_T_SVG}><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>` },
    { id: 'light',     label: 'Light',       icon: `<svg viewBox="0 0 24 24" ${_T_SVG}><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>` },
    { id: 'sepia',     label: 'Sepia',       icon: `<svg viewBox="0 0 24 24" ${_T_SVG}><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>` },
    { id: 'nordic',    label: 'Nordic',      icon: `<svg viewBox="0 0 24 24" ${_T_SVG}><line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/><path d="M20 16l-4-4 4-4"/><path d="M4 8l4 4-4 4"/><path d="M16 4l-4 4-4-4"/><path d="M8 20l4-4 4 4"/></svg>` },
    { id: 'sunset',    label: 'Sunset',      icon: `<svg viewBox="0 0 24 24" ${_T_SVG}><path d="M17 18a5 5 0 0 0-10 0"/><line x1="12" y1="2" x2="12" y2="9"/><line x1="4.22" y1="10.22" x2="5.64" y2="11.64"/><line x1="1" y1="18" x2="3" y2="18"/><line x1="21" y1="18" x2="23" y2="18"/><line x1="18.36" y1="11.64" x2="19.78" y2="10.22"/><line x1="23" y1="22" x2="1" y2="22"/><polyline points="8 6 12 2 16 6"/></svg>` },
    { id: 'stack',     label: 'Stapel',      icon: `<svg viewBox="0 0 24 24" ${_T_SVG}><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>` },
    { id: 'honeycomb', label: 'Waben',       icon: `<svg viewBox="0 0 24 24" ${_T_SVG}><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>` },
    { id: 'filmstrip', label: 'Filmstreifen',icon: `<svg viewBox="0 0 24 24" ${_T_SVG}><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/></svg>` },
    { id: 'kachel',    label: 'Kacheln',     icon: `<svg viewBox="0 0 24 24" ${_T_SVG}><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>` },
];

const THEME_KEY = 'mpd-theme';

/* Browser-/WebView-Chrome (Statusbar, Overscroll-Fläche) folgt dem
   Theme-Hintergrund — sonst bleibt sie beim statischen Meta-Wert hängen. */
function syncThemeColor() {
    const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
    if (!bg) return;
    let meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
        meta = document.createElement('meta');
        meta.name = 'theme-color';
        document.head.appendChild(meta);
    }
    meta.content = bg;
}

function applyTheme(id) {
    document.documentElement.setAttribute('data-theme', id);
    localStorage.setItem(THEME_KEY, id);
    syncThemeColor();
    // Mark active button
    document.querySelectorAll('.theme-option').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.theme === id);
    });
}

function initTheme() {
    const saved = localStorage.getItem(THEME_KEY) || 'kachel';
    document.documentElement.setAttribute('data-theme', saved);
    syncThemeColor();
}

function toggleThemePicker() {
    let picker = document.getElementById('theme-picker');
    if (!picker) {
        picker = document.createElement('div');
        picker.id = 'theme-picker';
        picker.innerHTML = MPD_THEMES.map(t =>
            `<button class="theme-option${document.documentElement.getAttribute('data-theme') === t.id ? ' active' : ''}"
                     data-theme="${t.id}"
                     onclick="applyTheme('${t.id}');toggleThemePicker()">
                <span class="theme-icon">${t.icon}</span>
                <span class="theme-label">${t.label}</span>
             </button>`
        ).join('');
        document.body.appendChild(picker);
        // Click outside → close
        setTimeout(() => {
            document.addEventListener('click', function closePicker(e) {
                if (!picker.contains(e.target) && !e.target.closest('#theme-btn')) {
                    picker.classList.remove('open');
                    document.removeEventListener('click', closePicker);
                }
            });
        }, 0);
    }
    picker.classList.toggle('open');
}

// Apply immediately on load (prevents flash)
initTheme();

/* ── Version badge: shown automatically on all pages ──────
   Very discreet (low opacity, no pointer event), bottom-right.
   MPD_VERSION und MPD_BUILD kommen aus /api/version, siehe oben.        */
document.addEventListener('DOMContentLoaded', async () => {

    // Fetch build date from backend (once per page load, cached)
    const cached = sessionStorage.getItem('mpd-build-date');
    if (cached) {
        MPD_BUILD = cached;
    } else {
        try {
            const r = await fetch('/api/version', { credentials: 'same-origin' });
            if (r.ok) {
                const data = await r.json();
                MPD_VERSION = data.version || '';
                MPD_PRODUCT = data.product || '';
                MPD_BUILD   = data.build_date || '';
                if (MPD_VERSION) sessionStorage.setItem('mpd-version', MPD_VERSION);
                if (MPD_BUILD)   sessionStorage.setItem('mpd-build-date', MPD_BUILD);
            }
        } catch (_) { /* offline/down → badge shows only version */ }
    }

    // Ist /api/version nicht erreichbar, bleibt der Versionsteil weg —
    // besser als ein irrefuehrendes "v" ohne Nummer.
    // Im Badge steht die PRODUKTversion, nicht die volle Nummer: die
    // dritte Stelle ist ein Commit-Zaehler (dreistellig) und sagt niemandem
    // etwas. Die volle Nummer samt Build steht in der Fusszeile.
    const shown = MPD_PRODUCT || MPD_VERSION;
    const versionPart = shown
        ? (MPD_BUILD ? `MPD ${shown} · ${MPD_BUILD}` : `MPD ${shown}`)
        : '';
    const badgeText = versionPart
        ? `© 2026 Thomas Bugge · ${versionPart}`
        : '© 2026 Thomas Bugge';

    // Global fixed badge (all pages)
    if (!document.getElementById('mpd-version-badge')) {
        const badge = document.createElement('div');
        badge.id = 'mpd-version-badge';
        badge.textContent = badgeText;
        badge.title = 'My Photo Diary · thomas@bgg-mail.de';
        badge.style.cssText = [
            'position:fixed',
            'bottom:6px',
            'right:10px',
            'font-size:10px',
            'font-family:ui-monospace,SFMono-Regular,Menlo,monospace',
            'letter-spacing:0.3px',
            'color:currentColor',
            'opacity:0.35',
            'pointer-events:none',
            'user-select:none',
            'z-index:9000',
        ].join(';');
        document.body.appendChild(badge);
    } else {
        document.getElementById('mpd-version-badge').textContent = badgeText;
    }

    // Stats header sub-line (only on the stats page)
    const subVer = document.getElementById('mpd-stats-sub-ver');
    if (subVer && MPD_VERSION) {
        subVer.textContent = `v${MPD_VERSION}${MPD_BUILD ? ' · ' + MPD_BUILD : ''}`;
    }
});
