/* MPD – Share Viewer Bootstrap
 * Loads the shared album via /share/<token>/api/album, renders it read-only
 * and hides the edit/FAB/map stuff. Only photo + text + separator are
 * rendered (alpha scope). Audio/video/document/map/tour are skipped in
 * album-render.js via window.MPD_SHARE_MODE.
 */

window.MPD_SHARE_MODE = true;
window.MPD_READ_ONLY  = true;

// Silence EXIF fetch in share — the API route isn't public and would
// otherwise pick up a 401 on every image. The info button isn't wired up
// in share.html at all.
if (typeof Lightbox !== 'undefined') {
    Lightbox.prototype.loadMetadata = async function () {};
}

// Pull token from URL: /share/<token>
const _shareToken = window.location.pathname.split('/').filter(Boolean)[1] || '';

// Override globals from album-state.js (urlParams returned nothing useful)
albumName  = '';
albumSpace = '';

async function loadShare() {
    const _t = (k, fb) => (window.MPD_I18N ? window.MPD_I18N.t(k) : fb) || fb;
    if (!_shareToken) {
        showError(_t('share_viewer.err_invalid_link', 'Invalid share link'));
        return;
    }
    try {
        const res = await fetch(`/share/${encodeURIComponent(_shareToken)}/api/album`);
        if (!res.ok) {
            showError(res.status === 404
                ? _t('share_viewer.err_revoked', 'This share link is invalid or revoked.')
                : `HTTP ${res.status}`);
            return;
        }
        albumData  = await res.json();
        albumName  = albumData.share?.album_name || '';
        albumSpace = albumData.space || 'shared';

        // Force theme from the share if the owner set one — otherwise the
        // viewer's locally stored theme stays. Use setAttribute instead of
        // applyTheme() so localStorage isn't overwritten (the recipient
        // can open multiple shares).
        const shareTheme = albumData.share?.theme;
        if (shareTheme) {
            document.documentElement.setAttribute('data-theme', shareTheme);
        }

        document.title = `${albumName} · My Photo Diary`;

        // Footer stays generic — the technical username wouldn't help
        // recipients (and a display_name field isn't exposed by the
        // share API yet).

        renderAlbum(false);
    } catch (err) {
        showError(err.message || _t('share_viewer.err_load', 'Fehler beim Laden'));
    }
}

loadShare();

// album-render.js ESC handler calls goBack() at the end, which redirects
// to / — but for the share recipient that's nonsense (lands at /login).
// Override: ESC inside the lightbox closes there, otherwise no-op.
// eslint-disable-next-line no-func-assign
goBack = function () { /* no-op in share viewer */ };
