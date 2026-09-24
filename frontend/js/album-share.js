/* MPD — Album Share / Send-Mail / Contacts modals
 * Extracted from index.html so album.html can reuse the same dialogs
 * without duplicating ~200 lines of inline HTML/JS per page.
 *
 * Three stacked overlays: share → send-mail → contacts. Each closes on
 * backdrop click, ESC, or its × button. Functions are exposed on window
 * so existing call sites in index.html (Album-Card-Kebab, Settings-Modal)
 * keep working unchanged.
 *
 * Dependencies (must be globals before this script runs):
 *   - window.MPD_I18N (i18n.js) — for localized strings
 *   - window.mpdToast (mpd-dialog.js) — for toast notifications
 * Backend endpoints used: /api/share/{list,create,revoke,send-email},
 *                         /api/contacts (GET + DELETE).
 */
(function () {
    'use strict';

    const API_BASE = window.location.origin;

    function escapeHtml(text) {
        if (text == null) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    function _t(k, vars, fb) {
        return (window.MPD_I18N ? window.MPD_I18N.t(k, vars) : fb) || fb;
    }

    // ── Share-Modal ───────────────────────────────────────────────
    async function _openAlbumShareModal(album /*, card unused*/) {
        const ov = document.createElement('div');
        ov.className = 'alb-share-overlay';
        ov.innerHTML = `
            <div class="alb-share-box" role="dialog" aria-modal="true">
                <div class="alb-share-header">
                    <h3>${_t('share_modal.title', { album: escapeHtml(album.title || album.name) }, `Album teilen: „${escapeHtml(album.title || album.name)}"`)}</h3>
                    <button type="button" class="alb-share-close" aria-label="${_t('share_modal.close_aria', null, 'Close')}">&times;</button>
                </div>
                <div class="alb-share-body">
                    <div class="alb-share-intro">
                        ${_t('share_modal.intro', null, 'Erzeuge einen Read-only-Link, den du verschicken kannst. Private Elemente (🔒) bleiben ausgeblendet.')}
                    </div>
                    <div class="alb-share-list" id="alb-share-list">
                        <div class="alb-share-empty">${_t('share_modal.loading', null, 'Lade aktive Links…')}</div>
                    </div>
                    <div class="alb-share-new" id="alb-share-new">
                        <select class="alb-share-expiry" title="${_t('share_modal.expiry_title', null, 'Gültigkeitsdauer des neuen Links')}">
                            <option value="">${_t('share_modal.expiry_unlimited', null, 'Unbegrenzt gültig')}</option>
                            <option value="7">${_t('share_modal.expiry_7', null, '7 Tage gültig')}</option>
                            <option value="30" selected>${_t('share_modal.expiry_30', null, '30 Tage gültig')}</option>
                            <option value="365">${_t('share_modal.expiry_365', null, '1 Jahr gültig')}</option>
                        </select>
                        <button class="alb-share-create" type="button">${_t('share_modal.btn_create', null, '🔗 Neuen Link erzeugen')}</button>
                    </div>
                    <div class="alb-share-status" id="alb-share-status"></div>
                </div>
            </div>`;
        document.body.appendChild(ov);

        const listEl   = ov.querySelector('#alb-share-list');
        const status   = ov.querySelector('#alb-share-status');
        const btnCreate= ov.querySelector('.alb-share-create');
        const selExpiry= ov.querySelector('.alb-share-expiry');
        const btnClose = ov.querySelector('.alb-share-close');

        const close = () => { ov.remove(); document.removeEventListener('keydown', onKey); };
        const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } };
        ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
        btnClose.addEventListener('click', close);
        document.addEventListener('keydown', onKey);

        async function refreshList() {
            listEl.innerHTML = `<div class="alb-share-empty">${_t('share_modal.loading', null, 'Lade aktive Links…')}</div>`;
            try {
                const r = await fetch(
                    `${API_BASE}/api/share/list?space=${encodeURIComponent(album.space)}&album_name=${encodeURIComponent(album.name)}`
                );
                const data = await r.json();
                if (!r.ok) throw new Error(data.detail || r.statusText);
                renderList(data.shares || []);
            } catch (err) {
                listEl.innerHTML = `<div class="alb-share-empty" style="color:#e06868">${escapeHtml(_t('share_modal.error_prefix', { msg: (err.message || String(err)) }, `Fehler: ${err.message || err}`))}</div>`;
            }
        }

        function renderList(shares) {
            if (!shares.length) {
                listEl.innerHTML = `<div class="alb-share-empty">${_t('share_modal.empty', null, 'Noch keine aktiven Links.')}</div>`;
                return;
            }
            listEl.innerHTML = '';
            const lang = (window.MPD_I18N ? window.MPD_I18N.getLang() : 'de');
            const dateLocale = (lang === 'en') ? 'en-GB' : 'de-DE';
            shares.forEach(s => {
                const row = document.createElement('div');
                row.className = 'alb-share-row';
                const created = s.created_at ? new Date(s.created_at * 1000).toLocaleString(dateLocale) : '—';
                const lastSeenTxt = s.last_seen_at
                    ? new Date(s.last_seen_at * 1000).toLocaleString(dateLocale)
                    : _t('share_modal.row_not_seen', null, 'not opened yet');
                let expiryTxt = '';
                if (s.expires_at) {
                    const expDate = new Date(s.expires_at * 1000);
                    expiryTxt = (s.expires_at * 1000 < Date.now())
                        ? _t('share_modal.row_expired', null, 'abgelaufen')
                        : _t('share_modal.row_expires', { date: expDate.toLocaleDateString(dateLocale) }, `läuft ab ${expDate.toLocaleDateString(dateLocale)}`);
                }
                const hashShort = (s.token_hash || '').substring(0, 10);
                row.innerHTML = `
                    <div class="alb-share-row-main">
                        <div class="alb-share-row-hash">#${escapeHtml(hashShort)}</div>
                        <div class="alb-share-row-meta">
                            <span>${escapeHtml(_t('share_modal.row_created', { date: created }, `erstellt ${created}`))}</span>
                            <span>·</span>
                            <span>${escapeHtml(lastSeenTxt)}</span>
                            ${expiryTxt ? `<span>·</span><span${s.expires_at * 1000 < Date.now() ? ' style="color:#e06868"' : ''}>${escapeHtml(expiryTxt)}</span>` : ''}
                        </div>
                    </div>
                    <button class="alb-share-revoke" type="button" title="${_t('share_modal.revoke_title', null, 'Link widerrufen')}" aria-label="${_t('share_modal.revoke_title', null, 'Link widerrufen')}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg></button>`;
                row.querySelector('.alb-share-revoke').addEventListener('click', () => _revoke(s.token_hash));
                listEl.appendChild(row);
            });
        }

        async function _revoke(tokenHash) {
            const okRevoke = await window.mpdConfirm({
                icon        : '🔗',
                title       : _t('share_modal.revoke_title', null, 'Link widerrufen'),
                body        : _t('share_modal.revoke_confirm', null, 'Revoke this link? Recipients lose access immediately.'),
                confirmLabel: _t('share_modal.revoke_btn', null, 'Widerrufen'),
                destructive : true,
            });
            if (!okRevoke) return;
            status.textContent = _t('share_modal.revoke_status', null, 'Widerrufe…');
            try {
                const r = await fetch(`${API_BASE}/api/share/revoke`, {
                    method : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body   : JSON.stringify({ token_hash: tokenHash }),
                });
                const d = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(d.detail || r.statusText);
                status.textContent = '';
                if (typeof window.mpdToast === 'function') window.mpdToast(_t('share_modal.revoke_toast', null, 'Link widerrufen'), { duration: 2000 });
                refreshList();
            } catch (err) {
                status.textContent = _t('share_modal.error_prefix', { msg: (err.message || err) }, `Fehler: ${err.message || err}`);
            }
        }

        btnCreate.addEventListener('click', async () => {
            btnCreate.disabled = true;
            status.textContent = _t('share_modal.create_status', null, 'Erzeuge Link…');
            try {
                const r = await fetch(`${API_BASE}/api/share/create`, {
                    method : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body   : JSON.stringify({
                        space       : album.space,
                        album_name  : album.name,
                        expires_days: parseInt(selExpiry.value, 10) || undefined,
                    }),
                });
                const d = await r.json();
                if (!r.ok) throw new Error(d.detail || r.statusText);
                const url = `${window.location.origin}/share/${d.token}`;
                _showCreatedUrl(url);
                refreshList();
            } catch (err) {
                status.textContent = _t('share_modal.error_prefix', { msg: (err.message || err) }, `Fehler: ${err.message || err}`);
            } finally {
                btnCreate.disabled = false;
            }
        });

        function _showCreatedUrl(url) {
            let block = ov.querySelector('.alb-share-fresh');
            if (!block) {
                block = document.createElement('div');
                block.className = 'alb-share-fresh';
                ov.querySelector('.alb-share-body').insertBefore(block, listEl);
            }
            block.innerHTML = `
                <div class="alb-share-fresh-label">${_t('share_modal.fresh_label', null, 'Neuer Link erzeugt:')}</div>
                <div class="alb-share-fresh-urlrow">
                    <input type="text" class="alb-share-fresh-url" readonly>
                </div>
                <div class="alb-share-fresh-actions">
                    <button class="alb-share-fresh-copy"    type="button" title="${_t('share_modal.fresh_copy_title', null, 'In Zwischenablage kopieren')}">${_t('share_modal.fresh_btn_copy', null, '🔗 Kopieren')}</button>
                    <button class="alb-share-fresh-preview" type="button" title="${_t('share_modal.fresh_preview_title', null, 'Vorschau in neuem Tab')}">${_t('share_modal.fresh_btn_preview', null, '↗ Vorschau')}</button>
                    <button class="alb-share-fresh-mail"    type="button" title="${_t('share_modal.fresh_mail_title', null, 'Per E-Mail verschicken')}">${_t('share_modal.fresh_btn_mail', null, '✉ E-Mail…')}</button>
                </div>
                <div class="alb-share-fresh-hint">${_t('share_modal.fresh_hint', null, 'Copy once or send by mail — the full form is only shown now.')}</div>`;
            const input  = block.querySelector('.alb-share-fresh-url');
            const btnCp  = block.querySelector('.alb-share-fresh-copy');
            const btnPv  = block.querySelector('.alb-share-fresh-preview');
            const btnMl  = block.querySelector('.alb-share-fresh-mail');
            input.value = url;
            setTimeout(() => { input.focus(); input.select(); }, 30);

            btnCp.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(url);
                    btnCp.textContent = _t('share_modal.fresh_btn_copied', null, '✓ Kopiert');
                    setTimeout(() => { btnCp.textContent = _t('share_modal.fresh_btn_copy', null, '🔗 Kopieren'); }, 1500);
                } catch {
                    input.select();
                    document.execCommand && document.execCommand('copy');
                }
            });
            btnPv.addEventListener('click', () => window.open(url, '_blank', 'noopener,noreferrer'));
            btnMl.addEventListener('click', () => _openSendMailModal({
                token      : url.split('/share/')[1] || '',
                albumTitle : album.title || album.name,
            }));
        }

        refreshList();
    }

    // ── Share via e-mail — sub-modal with address-book autocomplete ──
    async function _openSendMailModal({ token, albumTitle }) {
        const ov = document.createElement('div');
        ov.className = 'alb-mail-overlay';
        ov.innerHTML = `
            <div class="alb-mail-box" role="dialog" aria-modal="true">
                <div class="alb-mail-header">
                    <h3>${_t('send_mail.title', null, 'Link per E-Mail senden')}</h3>
                    <button type="button" class="alb-mail-close" aria-label="${_t('send_mail.close_aria', null, 'Close')}">&times;</button>
                </div>
                <div class="alb-mail-body">
                    <div class="alb-mail-intro">
                        ${_t('send_mail.intro', { album: escapeHtml(albumTitle) }, `The recipient gets a short mail with the link to „${album}".`)}
                    </div>
                    <div class="alb-mail-label-row">
                        <label class="alb-mail-label" for="alb-mail-to">${_t('send_mail.label_to', null, 'Recipient address')}</label>
                        <button type="button" class="alb-mail-manage" title="${_t('send_mail.manage_title', null, 'Gespeicherte Adressen verwalten')}">${_t('send_mail.btn_manage', null, 'Adressen verwalten…')}</button>
                    </div>
                    <input type="email" id="alb-mail-to" list="alb-mail-contacts" required
                           autocomplete="off" spellcheck="false" placeholder="${_t('send_mail.to_placeholder', null, 'name@beispiel.de')}">
                    <datalist id="alb-mail-contacts"></datalist>
                    <label class="alb-mail-label" for="alb-mail-name">${_t('send_mail.label_name', null, 'Name')} <span class="alb-mail-label-opt">${_t('send_mail.label_optional', null, '(optional)')}</span></label>
                    <input type="text" id="alb-mail-name" maxlength="100" placeholder="${_t('send_mail.name_placeholder', null, 'Vorname oder Spitzname')}">
                    <label class="alb-mail-label" for="alb-mail-msg">${_t('send_mail.label_msg', null, 'Personal message')} <span class="alb-mail-label-opt">${_t('send_mail.label_optional', null, '(optional)')}</span></label>
                    <textarea id="alb-mail-msg" rows="3" maxlength="2000"
                              placeholder="${_t('send_mail.msg_placeholder', null, 'A short sentence the recipient will see in the mail…')}"></textarea>
                    <div class="alb-mail-status" id="alb-mail-status"></div>
                    <div class="alb-mail-actions">
                        <button class="alb-mail-cancel" type="button">${_t('send_mail.btn_cancel', null, 'Abbrechen')}</button>
                        <button class="alb-mail-send"   type="button">${_t('send_mail.btn_send', null, '✉ Senden')}</button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(ov);

        const inputTo   = ov.querySelector('#alb-mail-to');
        const inputName = ov.querySelector('#alb-mail-name');
        const textMsg   = ov.querySelector('#alb-mail-msg');
        const datalist  = ov.querySelector('#alb-mail-contacts');
        const status    = ov.querySelector('#alb-mail-status');
        const btnSend   = ov.querySelector('.alb-mail-send');
        const btnCnc    = ov.querySelector('.alb-mail-cancel');
        const btnClose  = ov.querySelector('.alb-mail-close');
        const btnManage = ov.querySelector('.alb-mail-manage');

        const close = () => { ov.remove(); document.removeEventListener('keydown', onKey); };
        const onKey = (e) => {
            if (e.key === 'Escape') { e.preventDefault(); close(); }
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault(); send();
            }
        };
        ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
        btnCnc.addEventListener('click', close);
        btnClose.addEventListener('click', close);
        document.addEventListener('keydown', onKey);

        let contactsByEmail = {};
        async function reloadContacts() {
            contactsByEmail = {};
            datalist.innerHTML = '';
            try {
                const r = await fetch(`${API_BASE}/api/contacts`);
                if (!r.ok) return;
                const d = await r.json();
                (d.contacts || []).forEach(c => {
                    contactsByEmail[c.email] = c;
                    const opt = document.createElement('option');
                    opt.value = c.email;
                    if (c.name) opt.label = c.name;
                    datalist.appendChild(opt);
                });
            } catch { /* still, Adressbuch ist optional */ }
        }
        reloadContacts();

        btnManage.addEventListener('click', () => {
            _openContactsModal(() => reloadContacts());
        });

        inputTo.addEventListener('change', () => {
            const c = contactsByEmail[inputTo.value.trim().toLowerCase()];
            if (c && c.name && !inputName.value) inputName.value = c.name;
        });

        setTimeout(() => inputTo.focus(), 30);

        async function send() {
            const to_email = inputTo.value.trim().toLowerCase();
            if (!to_email) { status.textContent = _t('send_mail.err_to_missing', null, 'Recipient address missing.'); inputTo.focus(); return; }

            btnSend.disabled = true; btnCnc.disabled = true;
            status.textContent = _t('send_mail.status_sending', null, 'Sende Mail…');
            try {
                const r = await fetch(`${API_BASE}/api/share/send-email`, {
                    method : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body   : JSON.stringify({
                        token           : token,
                        to_email        : to_email,
                        to_name         : inputName.value.trim() || undefined,
                        personal_message: textMsg.value.trim()   || undefined,
                    }),
                });
                const d = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
                if (typeof window.mpdToast === 'function') window.mpdToast(_t('send_mail.toast_sent', { email: to_email }, `Mail an ${to_email} verschickt`), { duration: 2500 });
                close();
            } catch (err) {
                status.textContent = _t('send_mail.error_prefix', { msg: (err.message || err) }, `Fehler: ${err.message || err}`);
                btnSend.disabled = false; btnCnc.disabled = false;
            }
        }
        btnSend.addEventListener('click', send);
    }

    // ── Adressbuch-Verwaltung Sub-Sub-Modal ────────────────────────
    async function _openContactsModal(onChange) {
        const ov = document.createElement('div');
        ov.className = 'alb-contacts-overlay';
        ov.innerHTML = `
            <div class="alb-contacts-box" role="dialog" aria-modal="true">
                <div class="alb-contacts-header">
                    <h3>${_t('contacts_modal.title', null, 'Adressbuch')}</h3>
                    <button type="button" class="alb-contacts-close" aria-label="${_t('contacts_modal.close_aria', null, 'Close')}">&times;</button>
                </div>
                <div class="alb-contacts-body">
                    <div class="alb-contacts-intro">
                        ${_t('contacts_modal.intro', null, 'Saved recipients from previous share mails. Quietly learned on send, can be removed here.')}
                    </div>
                    <div class="alb-contacts-list" id="alb-contacts-list">
                        <div class="alb-contacts-empty">${_t('contacts_modal.loading', null, 'Lade…')}</div>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(ov);

        const listEl   = ov.querySelector('#alb-contacts-list');
        const btnClose = ov.querySelector('.alb-contacts-close');

        const close = () => {
            ov.remove();
            document.removeEventListener('keydown', onKey);
            if (typeof onChange === 'function') onChange();
        };
        const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } };
        ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
        btnClose.addEventListener('click', close);
        document.addEventListener('keydown', onKey);

        async function refresh() {
            listEl.innerHTML = `<div class="alb-contacts-empty">${_t('contacts_modal.loading', null, 'Lade…')}</div>`;
            try {
                const r = await fetch(`${API_BASE}/api/contacts`);
                const d = await r.json();
                if (!r.ok) throw new Error(d.detail || r.statusText);
                render(d.contacts || []);
            } catch (err) {
                listEl.innerHTML = `<div class="alb-contacts-empty" style="color:#e06868">${escapeHtml(_t('contacts_modal.error_prefix', { msg: (err.message || String(err)) }, `Fehler: ${err.message || err}`))}</div>`;
            }
        }

        function render(contacts) {
            if (!contacts.length) {
                listEl.innerHTML = `<div class="alb-contacts-empty">${_t('contacts_modal.empty', null, 'Noch keine Adressen gespeichert.')}</div>`;
                return;
            }
            listEl.innerHTML = '';
            const lang = (window.MPD_I18N ? window.MPD_I18N.getLang() : 'de');
            contacts.forEach(c => {
                const row = document.createElement('div');
                row.className = 'alb-contacts-row';
                const lastUsed = c.last_used_at
                    ? new Date(c.last_used_at * 1000).toLocaleDateString(lang === 'en' ? 'en-GB' : 'de-DE')
                    : _t('contacts_modal.last_used_never', null, '—');
                const lastUsedTxt = c.last_used_at
                    ? _t('contacts_modal.last_used', { date: lastUsed }, `zuletzt ${lastUsed}`)
                    : lastUsed;
                row.innerHTML = `
                    <div class="alb-contacts-row-main">
                        <div class="alb-contacts-row-email">${escapeHtml(c.email)}</div>
                        <div class="alb-contacts-row-meta">
                            ${c.name ? `<span>${escapeHtml(c.name)}</span><span>·</span>` : ''}
                            <span>${escapeHtml(lastUsedTxt)}</span>
                        </div>
                    </div>
                    <button class="alb-contacts-del" type="button" title="${_t('contacts_modal.del_title', null, 'Entfernen')}" aria-label="${_t('contacts_modal.del_title', null, 'Entfernen')}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg></button>`;
                row.querySelector('.alb-contacts-del').addEventListener('click', async () => {
                    const ok = await window.mpdConfirm({
                        icon        : '🗑',
                        title       : _t('contacts_modal.del_title', null, 'Entfernen'),
                        body        : _t('contacts_modal.del_confirm', { email: c.email }, `Adresse „${c.email}" aus dem Adressbuch entfernen?`),
                        confirmLabel: _t('contacts_modal.del_title', null, 'Entfernen'),
                        destructive : true,
                    });
                    if (!ok) return;
                    try {
                        const r = await fetch(`${API_BASE}/api/contacts/${encodeURIComponent(c.email)}`, { method: 'DELETE' });
                        if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || r.statusText); }
                        refresh();
                    } catch (err) {
                        window.mpdToast(_t('contacts_modal.del_failed', { msg: (err.message || err) }, 'Fehler beim Entfernen: ' + (err.message || err)), { duration: 4000 });
                    }
                });
                listEl.appendChild(row);
            });
        }

        refresh();
    }

    // ── Einzelfoto-Share (Teilen v3, 04.09.2026) ─────────────────
    // Kompaktes Modal aus der Lightbox heraus: Gültigkeitsdauer wählen,
    // Link erzeugen, kopieren; bestehende Links dieses Fotos widerrufen.
    async function _openPhotoShareModal({ space, album, file }) {
        const ov = document.createElement('div');
        ov.className = 'alb-share-overlay';
        ov.innerHTML = `
            <div class="alb-share-box" role="dialog" aria-modal="true">
                <div class="alb-share-header">
                    <h3>${_t('share_photo.title', { file: escapeHtml(file) }, `Foto teilen: „${escapeHtml(file)}"`)}</h3>
                    <button type="button" class="alb-share-close" aria-label="${_t('share_modal.close_aria', null, 'Close')}">&times;</button>
                </div>
                <div class="alb-share-body">
                    <div class="alb-share-intro">
                        ${_t('share_photo.intro', null, 'Der Link zeigt genau dieses eine Foto — mit Download-Knopf für den Empfänger.')}
                    </div>
                    <div class="alb-share-list" id="alb-pshare-list">
                        <div class="alb-share-empty">${_t('share_modal.loading', null, 'Lade aktive Links…')}</div>
                    </div>
                    <div class="alb-share-new">
                        <select class="alb-share-expiry" title="${_t('share_modal.expiry_title', null, 'Gültigkeitsdauer des neuen Links')}">
                            <option value="">${_t('share_modal.expiry_unlimited', null, 'Unbegrenzt gültig')}</option>
                            <option value="7">${_t('share_modal.expiry_7', null, '7 Tage gültig')}</option>
                            <option value="30" selected>${_t('share_modal.expiry_30', null, '30 Tage gültig')}</option>
                            <option value="365">${_t('share_modal.expiry_365', null, '1 Jahr gültig')}</option>
                        </select>
                        <button class="alb-share-create" type="button">${_t('share_modal.btn_create', null, '🔗 Neuen Link erzeugen')}</button>
                    </div>
                    <div class="alb-share-status" id="alb-pshare-status"></div>
                </div>
            </div>`;
        document.body.appendChild(ov);

        const listEl    = ov.querySelector('#alb-pshare-list');
        const status    = ov.querySelector('#alb-pshare-status');
        const btnCreate = ov.querySelector('.alb-share-create');
        const selExpiry = ov.querySelector('.alb-share-expiry');
        const btnClose  = ov.querySelector('.alb-share-close');

        const close = () => { ov.remove(); document.removeEventListener('keydown', onKey); };
        const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } };
        ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
        btnClose.addEventListener('click', close);
        document.addEventListener('keydown', onKey, true);

        async function refreshList() {
            try {
                const r = await fetch(
                    `${API_BASE}/api/share/list?space=${encodeURIComponent(space)}&album_name=${encodeURIComponent(album)}`
                );
                const data = await r.json();
                if (!r.ok) throw new Error(data.detail || r.statusText);
                renderList((data.shares || []).filter(sh => sh.file === file));
            } catch (err) {
                listEl.innerHTML = `<div class="alb-share-empty" style="color:#e06868">${escapeHtml(_t('share_modal.error_prefix', { msg: (err.message || String(err)) }, `Fehler: ${err.message || err}`))}</div>`;
            }
        }

        function renderList(items) {
            if (!items.length) {
                listEl.innerHTML = `<div class="alb-share-empty">${_t('share_modal.empty', null, 'Noch keine aktiven Links.')}</div>`;
                return;
            }
            listEl.innerHTML = '';
            const lang = (window.MPD_I18N ? window.MPD_I18N.getLang() : 'de');
            const dateLocale = (lang === 'en') ? 'en-GB' : 'de-DE';
            items.forEach(sh => {
                const row = document.createElement('div');
                row.className = 'alb-share-row';
                const created = sh.created_at ? new Date(sh.created_at * 1000).toLocaleString(dateLocale) : '—';
                const seenTxt = sh.last_seen_at
                    ? new Date(sh.last_seen_at * 1000).toLocaleString(dateLocale)
                    : _t('share_modal.row_not_seen', null, 'noch nicht geöffnet');
                let expiryTxt = '';
                if (sh.expires_at) {
                    const expDate = new Date(sh.expires_at * 1000);
                    expiryTxt = (sh.expires_at * 1000 < Date.now())
                        ? _t('share_modal.row_expired', null, 'abgelaufen')
                        : _t('share_modal.row_expires', { date: expDate.toLocaleDateString(dateLocale) }, `läuft ab ${expDate.toLocaleDateString(dateLocale)}`);
                }
                row.innerHTML = `
                    <div class="alb-share-row-main">
                        <div class="alb-share-row-hash">#${escapeHtml((sh.token_hash || '').substring(0, 10))}</div>
                        <div class="alb-share-row-meta">
                            <span>${escapeHtml(_t('share_modal.row_created', { date: created }, `erstellt ${created}`))}</span>
                            <span>·</span>
                            <span>${escapeHtml(seenTxt)}</span>
                            ${expiryTxt ? `<span>·</span><span${sh.expires_at * 1000 < Date.now() ? ' style="color:#e06868"' : ''}>${escapeHtml(expiryTxt)}</span>` : ''}
                        </div>
                    </div>
                    <button class="alb-share-revoke" type="button" title="${_t('share_modal.revoke_title', null, 'Link widerrufen')}" aria-label="${_t('share_modal.revoke_title', null, 'Link widerrufen')}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg></button>`;
                row.querySelector('.alb-share-revoke').addEventListener('click', async () => {
                    status.textContent = _t('share_modal.revoke_status', null, 'Widerrufe…');
                    try {
                        const r = await fetch(`${API_BASE}/api/share/revoke`, {
                            method : 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body   : JSON.stringify({ token_hash: sh.token_hash }),
                        });
                        const d = await r.json().catch(() => ({}));
                        if (!r.ok) throw new Error(d.detail || r.statusText);
                        status.textContent = '';
                        if (typeof window.mpdToast === 'function') window.mpdToast(_t('share_modal.revoke_toast', null, 'Link widerrufen'), { duration: 2000 });
                        refreshList();
                    } catch (err) {
                        status.textContent = _t('share_modal.error_prefix', { msg: (err.message || err) }, `Fehler: ${err.message || err}`);
                    }
                });
                listEl.appendChild(row);
            });
        }

        btnCreate.addEventListener('click', async () => {
            btnCreate.disabled = true;
            status.textContent = _t('share_modal.create_status', null, 'Erzeuge Link…');
            try {
                const r = await fetch(`${API_BASE}/api/share/create`, {
                    method : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body   : JSON.stringify({
                        space       : space,
                        album_name  : album,
                        file        : file,
                        expires_days: parseInt(selExpiry.value, 10) || undefined,
                    }),
                });
                const d = await r.json();
                if (!r.ok) throw new Error(d.detail || r.statusText);
                status.textContent = '';
                showFreshUrl(`${window.location.origin}/share/${d.token}`);
                refreshList();
            } catch (err) {
                status.textContent = _t('share_modal.error_prefix', { msg: (err.message || err) }, `Fehler: ${err.message || err}`);
            } finally {
                btnCreate.disabled = false;
            }
        });

        function showFreshUrl(url) {
            let block = ov.querySelector('.alb-share-fresh');
            if (!block) {
                block = document.createElement('div');
                block.className = 'alb-share-fresh';
                ov.querySelector('.alb-share-body').insertBefore(block, listEl);
            }
            block.innerHTML = `
                <div class="alb-share-fresh-label">${_t('share_modal.fresh_label', null, 'Neuer Link erzeugt:')}</div>
                <div class="alb-share-fresh-urlrow">
                    <input type="text" class="alb-share-fresh-url" readonly>
                </div>
                <div class="alb-share-fresh-actions">
                    <button class="alb-share-fresh-copy"    type="button" title="${_t('share_modal.fresh_copy_title', null, 'In Zwischenablage kopieren')}">${_t('share_modal.fresh_btn_copy', null, '🔗 Kopieren')}</button>
                    <button class="alb-share-fresh-preview" type="button" title="${_t('share_modal.fresh_preview_title', null, 'Vorschau in neuem Tab')}">${_t('share_modal.fresh_btn_preview', null, '↗ Vorschau')}</button>
                </div>
                <div class="alb-share-fresh-hint">${_t('share_photo.fresh_hint', null, 'Link einmal kopieren — die vollständige Form wird nur jetzt angezeigt.')}</div>`;
            const input = block.querySelector('.alb-share-fresh-url');
            const btnCp = block.querySelector('.alb-share-fresh-copy');
            const btnPv = block.querySelector('.alb-share-fresh-preview');
            input.value = url;
            setTimeout(() => { input.focus(); input.select(); }, 30);
            btnCp.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(url);
                    btnCp.textContent = _t('share_modal.fresh_btn_copied', null, '✓ Kopiert');
                    setTimeout(() => { btnCp.textContent = _t('share_modal.fresh_btn_copy', null, '🔗 Kopieren'); }, 1500);
                } catch {
                    input.select();
                    document.execCommand && document.execCommand('copy');
                }
            });
            btnPv.addEventListener('click', () => window.open(url, '_blank', 'noopener,noreferrer'));
        }

        refreshList();
    }

    // Expose on window so existing call sites in index.html keep working.
    window._openAlbumShareModal = _openAlbumShareModal;
    window._openPhotoShareModal = _openPhotoShareModal;
    window._openSendMailModal   = _openSendMailModal;
    window._openContactsModal   = _openContactsModal;
})();
