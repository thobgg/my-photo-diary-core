"""
core/i18n.py — Backend language detection and locale lookup.

Pattern: identical to the frontend (frontend/js/i18n.js).
- Inline LOCALES as a Python dict (no YAML/JSON, no build step).
- t(key, request, vars) reads Accept-Language from the request, falls
  back to the key string itself if missing (defensive).
- Used in routers for HTTPException details and other user-visible
  strings: raise HTTPException(404, t("album.not_found", request))

Wave 4: Migration of the ~30-50 user-visible HTTPExceptions in
albums/photo_edit/share/users_admin/settings. Technical 401/403/500
messages remain in DE as a fallback.
"""

from typing import Any, Mapping, Optional

SUPPORTED = ("de", "en")
DEFAULT_LANG = "de"

# ─────────────────────────────────────────────────────────────────────────
# LOCALES — populated incrementally with the Wave-4 migration.
# Shape: {lang: {namespace: {key: "string"}}}
# Variable substitution: "{var}" → vars["var"]
# ─────────────────────────────────────────────────────────────────────────

LOCALES: dict = {
    "de": {
        "_meta": {"lang": "de", "name": "Deutsch"},
        "album": {
            "already_exists": 'Album „{name}“ existiert bereits.',
            "not_found": "Album nicht gefunden.",
            "json_not_found": 'album.json nicht gefunden: „{name}“',
            "json_already_exists": 'album.json existiert bereits in „{folder}“',
            "durchlauf_missing": 'Kein Durchlauf-Ordner „{year}“ in diesem Bestand.',
            "durchlauf_no_album": '„{folder}“ ist ein Durchlauf-Ordner (nackte Jahreszahl) und kann kein Album werden — Fotos von dort einsortieren.',
            "fields_required": "space, folder_name und thumbnail sind erforderlich.",
            "space_readonly": 'Space „{space}“ ist read-only (Rolle: {role}).',
            "upload_too_large": "Datei zu groß (max. 2 GB).",
            "upload_empty": "Leerer Upload.",
            "upload_needs_album": "add_to_album erfordert ein existierendes Album (album.json).",
            "entry_failed": "Datei gespeichert, aber Album-Eintrag fehlgeschlagen.",
            "file_exists": '„{file}“ existiert bereits.',
            "file_exists_target": '„{file}“ existiert im Ziel bereits.',
            "import_target_not_album": "Ziel ist kein Album (keine album.json).",
            "import_source_referenced": "Datei ist im Quellalbum referenziert — erst dort entfernen.",
            "delete_referenced": "Datei ist in einem Album referenziert — löschen kann nur der Kurator.",
            "delete_in_album_folder": "\u201e{file}\u201c geh\u00f6rt zu einem Album \u2014 dort l\u00f6schen, nicht von hier.",
            "delete_used_elsewhere": "\u201e{file}\u201c wird in {count} weiteren Album(en) verwendet: {albums}. Dort zuerst entfernen.",
            "conflict_modified": "Album wurde inzwischen geändert (Stand: {current}).",
            "invalid_folder_name": "folder_name darf keinen Pfad-Trenner und kein '..' enthalten.",
            "folder_already_exists": 'Ordner „{folder}“ existiert bereits — bitte aus der Liste auswählen oder anderen Namen wählen.',
            "folder_not_in_synology": 'Ordner „{folder}“ nicht in Synology Photos.',
            "thumbnail_not_found": 'Thumbnail-Datei „{file}“ nicht gefunden.',
            "source_required": "source_album und file sind erforderlich.",
            "source_invalid_path": "source_album darf keinen Pfad-Trenner enthalten.",
            "file_invalid_path": "file darf keinen Pfad-Trenner enthalten.",
            "source_file_not_found": 'Quell-Datei „{file}“ nicht gefunden.',
            "target_not_found": 'Ziel-Album „{name}“ nicht gefunden.',
            "invalid_name": "Ungültiger Album-Name.",
            "invalid_backup_name": "Ungültiger Backup-Name.",
            "backup_name_mismatch": "Backup-Name passt nicht zum Schema.",
            "backup_not_found": 'Backup „{file}“ nicht gefunden.',
            "exists_no_undo_needed": "Album existiert bereits — kein Undo nötig.",
            "filename_missing": "filename fehlt.",
            "invalid_filename": "Ungültiger Dateiname.",
            "invalid_filepath": "Ungültiger Dateipfad.",
            "only_photos_allowed": "Nur Foto-Dateien erlaubt ({exts}).",
            "file_not_found": 'Datei „{file}“ nicht gefunden.',
            "not_a_regular_file": "Keine reguläre Datei.",
        },
        "share": {
            "disabled_global": "Teilen ist aktuell deaktiviert (globale Einstellung).",
            "invalid_or_revoked": "Share-Link ungültig oder widerrufen.",
            "album_gone": "Album existiert nicht mehr.",
            "owner_gone": "Share-Owner existiert nicht mehr.",
            "fields_required": "space und album_name sind erforderlich.",
            "invalid_album_name": "Ungültiger Album-Name.",
            "shared_only_admin_editor": "Shared-Space teilen nur für admin/editor.",
            "list_needs_both": "space und album_name nur gemeinsam angeben — ohne beide kommen alle eigenen Links.",
            "token_required": "token oder token_hash erforderlich.",
            "share_not_found": "Share nicht gefunden.",
            "not_your_share": "Nicht dein Share.",
            "token_missing": "token fehlt.",
            "invalid_email": "Ungültige Empfänger-Adresse.",
            "message_too_long": "Persönliche Nachricht zu lang (max. 2000 Zeichen).",
            "smtp_not_configured": "SMTP nicht konfiguriert (MPD_SMTP_* in mpd.env).",
            "mail_send_failed": "Mail-Versand fehlgeschlagen: {msg}",
            "admin_only": "Nur für Admin.",
        },
        "users": {
            "body_must_be_object": "Body muss Objekt sein.",
            "invalid_username": "Username: Kleinbuchstaben, Ziffern, .-_ (2–32 Zeichen, beginnt mit Buchstabe).",
            "invalid_role": "role muss {roles} sein.",
            "password_min": "Passwort: mindestens {n} Zeichen.",
            "user_exists": 'User „{user}“ existiert bereits.',
            "user_unknown": "User unbekannt.",
            "display_name_must_be_string": "display_name muss String sein.",
            "self_admin_role_locked": "Eigene Admin-Rolle kann nicht entzogen werden.",
            "last_admin_role_locked": "Das ist der letzte Admin — Rolle kann nicht geändert werden.",
            "self_delete_denied": "Eigener User kann nicht gelöscht werden.",
            "last_admin_delete_denied": "Das ist der letzte Admin — kann nicht gelöscht werden.",
            "admin_only": "Nur für Admin.",
        },
        "photo_edit": {
            "not_editable": '„{file}“ ist kein bearbeitbares Foto.',
            "photo_not_found": 'Foto „{file}“ nicht gefunden.',
            "no_write_permission": "Kein Schreibrecht in diesem Space.",
            "edit_error": "Edit-Fehler: {msg}",
            "reset_error": "Reset-Fehler: {msg}",
            "rename_error": "Rename-Fehler: {msg}",
            "exif_error": "EXIF-Fehler: {msg}",
        },
        "settings_be": {
            "connect_no_url": "Keine Adresse bekannt — bitte MPD_BASE_URL setzen.",
            "license_empty": "Kein Lizenztext übergeben.",
            "license_invalid": "Lizenz nicht angenommen: {msg}",
            "admin_only": "Nur für Admin.",
            "body_must_be_object": "Body muss Objekt sein.",
        },
        "contacts": {
            "invalid_email": "Ungültige E-Mail-Adresse.",
            "contact_not_found": "Kontakt nicht gefunden.",
        },
        "mail": {
            "share_subject": 'Fotoalbum „{album}“ für dich',
            "share_greeting_named": "Hallo {name},",
            "share_greeting_anon": "Hallo,",
            "share_intro": 'ich hab ein Album mit dir geteilt — „{album}“:',
            "share_open_label": "Album öffnen:",
            "share_open_btn": "Album öffnen",
            "share_signoff": "Liebe Grüße,",
            "share_footer_text": "Geteilt aus My Photo Diary.",
            "share_footer_html": "Geteilt aus My Photo Diary &middot; where memories stay yours — and kindly shared",
            "share_brand_tagline": "where memories stay yours",
            "share_shared_by": "Geteilt von {name}",
            "share_album_alt": "Album-Cover",
            # Teilen v3 — Einzelfoto. Ohne Albumnamen mit Absicht: geteilt
            # ist genau ein Foto, der Albumtitel geht den Empfaenger nichts an.
            "share_subject_photo": "Ein Foto für dich",
            "share_intro_photo": "ich hab ein Foto mit dir geteilt:",
            "share_open_label_photo": "Foto öffnen:",
            "share_open_btn_photo": "Foto öffnen",
            "share_photo_alt": "Geteiltes Foto",
            "test_subject": "MPD Test-Mail",
            "test_body": "Das ist eine Testmail aus My Photo Diary.\nWenn du das liest, funktioniert SMTP korrekt.\n",
        },
        "common": {
            "not_logged_in": "Nicht eingeloggt.",
            "session_expired": "Session abgelaufen.",
            "no_shared_access": "Kein Zugriff auf Shared Space.",
            "space_not_available": "Space '{space}' nicht verfügbar.",
            "invalid_path": "Ungültiger Pfad.",
            "invalid_filename": "Ungültiger Dateiname.",
            "file_not_found": "Datei nicht gefunden.",
            "internal_error": "Interner Fehler: {msg}",
            "error_generic": "Fehler: {msg}",
        },
        "static": {
            "not_found": "{kind} nicht gefunden.",
        },
        "pdx": {
            "invalid_version": "Ungültige PDX-Version: '{version}'",
            "invalid_structure": "Ungültige Struktur: 'meta' oder 'elements' fehlt.",
            "elements_not_list": "'elements' muss eine Liste sein.",
            "meta_field_missing": "meta.{field} fehlt.",
            "element_not_dict": "Element {idx} ist kein Dictionary.",
            "element_id_missing": "Element {idx}: 'id' fehlt.",
            "element_id_invalid": "Element {idx}: id '{id}' ungültig.",
            "duplicate_id": "Doppelte id: {id}.",
            "text_missing": "Text-Element {id}: 'text' fehlt.",
            "file_missing": "{type}-Element {id}: 'file' fehlt.",
            "source_album_invalid": "Element {id}: 'source_album' muss ein einfacher Albumordner-Name ohne Pfad-Trenner sein.",
            "link_url_missing": "Link-Element {id}: 'url' fehlt.",
            "link_url_invalid": "Link-Element {id}: 'url' muss mit http:// oder https:// beginnen.",
            "link_url_too_long": "Link-Element {id}: 'url' ist zu lang (max. 2048 Zeichen).",
        },
        "media": {
            "file_not_found": "Datei '{filename}' nicht gefunden.",
            "image_not_found": "Bild '{filename}' nicht gefunden.",
            "video_not_found": "Video '{filename}' nicht gefunden.",
            "audio_not_found": "Audio '{filename}' nicht gefunden.",
            "doc_not_found": "Dokument '{filename}' nicht gefunden.",
            "gpx_not_found": "GPX-Datei '{filename}' nicht gefunden.",
            "album_not_found": "Album '{album}' nicht gefunden.",
            "not_a_video": "'{filename}' ist kein Video.",
            "not_a_gpx": "'{filename}' ist keine GPX-Datei.",
            "no_photo_thumb": "Kein Foto-Thumb für '{ext}' — Frontend sollte den passenden Endpoint nutzen.",
            "thumb_error": "Thumbnail-Fehler: {msg}",
            "exif_error": "EXIF-Fehler: {msg}",
            "video_error": "Video-Fehler: {msg}",
            "video_thumb_error": "Video-Thumb-Fehler: {msg}",
            "ffmpeg_error": "FFmpeg-Fehler: {msg}",
            "ffmpeg_failed": "FFmpeg-Fehler.",
            "doc_error": "Dokument-Fehler: {msg}",
            "audio_error": "Audio-Fehler: {msg}",
            "gpx_error": "GPX-Fehler: {msg}",
            "gpx_list_error": "GPX-Liste-Fehler: {msg}",
            "demo_no_download": "Demo: Original-Download nicht verfügbar.",
        },
        "memories": {
            "scan_error": "Fehler: {msg}",
            "frontend_not_found": "Memories-Frontend nicht gefunden.",
            "no_photo_thumb": "Kein Foto-Thumb für '{filename}'.",
            "file_not_found": "Datei '{photo_id}' nicht gefunden.",
            "thumb_error": "Thumbnail-Fehler: {msg}",
            "demo_unavailable": "Demo: Memories nicht verfügbar.",
        },
        "modules": {
            "locked": "Modul „{module}“ ist nicht freigeschaltet.",
        },
        "tours": {
            "invalid_filename": "Ungültiger Dateiname.",
            "gpx_not_found": "GPX '{filename}' nicht gefunden.",
            "fields_required": "filename und album erforderlich.",
            "invalid_path": "Ungültiger Pfad.",
            "album_folder_not_found": "Album-Ordner '{album}' nicht gefunden.",
            "already_in_album": "'{filename}' existiert bereits in '{album}'.",
            "demo_unavailable": "Demo: Tours nicht verfügbar.",
        },
        "trails": {
            "invalid_filename": "Ungültiger Dateiname.",
            "gpx_not_found": "GPX '{filename}' nicht gefunden.",
            "demo_unavailable": "Demo: Trails nicht verfügbar.",
        },
        "search": {
            "index_error": "Index-Fehler: {msg}",
            "frontend_not_found": "Search-Frontend nicht gefunden.",
        },
        "stats_be": {
            "frontend_not_found": "Stats-Frontend nicht gefunden.",
            "maptiler_not_configured": "MAPTILER_KEY nicht konfiguriert.",
        },
        "companion": {
            "disabled": "Companion deaktiviert (kein API-Key).",
            "demo_unavailable": "Demo: Companion nicht verfügbar.",
            "empty_text": "Leerer Text.",
            "text_too_long": "Text zu lang (max {n} Zeichen).",
            "timeout": "Companion-Timeout.",
            "unreachable": "Companion nicht erreichbar.",
            "api_error": "API-Fehler {code}.",
            "parse_error": "Antwort konnte nicht verarbeitet werden.",
            "no_suggestions": "Keine verwertbaren Vorschläge erhalten.",
            # Vorher meldete die Sekundensperre "Companion deaktiviert" —
            # das war irrefuehrend, er war nur zu schnell dran.
            "rate_limited": "Kurz durchatmen — gleich wieder.",
            "quota_exhausted": "Stundenkontingent erreicht ({n} Anfragen). Später weiter.",
        },
        "share_be": {
            "unknown_space_in_share": "Unbekannter Space im Share: {space}.",
            "unknown_space": "Unbekannter Space: {space}.",
            "default_og_missing": "Default-OG-Image nicht installiert.",
            "viewer_not_installed": "Share-Viewer nicht installiert.",
            "album_gone": "Album existiert nicht mehr.",
            "not_video": "Datei ist kein Video.",
            "ffmpeg_failed": "FFmpeg-Fehler.",
            "path_not_in_album": "Pfad nicht im geteilten Album.",
            "paused_title": "Momentan nicht verfügbar",
            "paused_body": "Der Absender hat das Teilen vorübergehend pausiert.<br>Komm später nochmal zurück &mdash; der Link bleibt gültig.",
            "invalid_title": "Link ungültig",
            "invalid_body": "Dieser Share-Link ist ungültig oder wurde widerrufen.",
            "not_a_photo": "Nur Fotos können einzeln geteilt werden.",
            "photo_locked": "Dieses Foto ist als privat markiert.",
        },
        "albums_be": {
            "load_albums_error": "Fehler beim Laden der Alben: {msg}",
            "load_album_error": "Fehler beim Laden des Albums: {msg}",
            "folder_not_in_synology": "Ordner '{folder}' nicht in Synology Photos.",
        },
        "auth_be": {
            "rate_limit_share": "Zu viele ungültige Share-Link-Aufrufe. Bitte {seconds}s warten.",
            "rate_limit_login": "Zu viele Fehlversuche. Bitte {seconds}s warten.",
            "invalid_request": "Ungültiger Request.",
            "credentials_required": "Benutzer und Passwort erforderlich.",
            "demo_disabled": "Demo-Zugang ist derzeit deaktiviert.",
            "must_change_password": "Bitte zuerst ein neues Passwort setzen.",
            "old_password_wrong": "Das bisherige Passwort stimmt nicht.",
            "password_too_short": "Neues Passwort zu kurz (mindestens {n} Zeichen).",
            "password_unchanged": "Das neue Passwort muss sich vom bisherigen unterscheiden.",
        },
    },
    "en": {
        "_meta": {"lang": "en", "name": "English"},
        "album": {
            "already_exists": 'Album “{name}” already exists.',
            "not_found": "Album not found.",
            "json_not_found": 'album.json not found: “{name}”',
            "json_already_exists": 'album.json already exists in “{folder}”',
            "durchlauf_missing": 'No inbox folder “{year}” in this space.',
            "durchlauf_no_album": '“{folder}” is an inbox folder (bare year) and cannot become an album — sort its photos into albums instead.',
            "fields_required": "space, folder_name and thumbnail are required.",
            "space_readonly": 'Space “{space}” is read-only (role: {role}).',
            "upload_too_large": "File too large (max. 2 GB).",
            "upload_empty": "Empty upload.",
            "upload_needs_album": "add_to_album requires an existing album (album.json).",
            "entry_failed": "File saved, but adding the album entry failed.",
            "file_exists": '"{file}" already exists.',
            "file_exists_target": '"{file}" already exists at the target.',
            "import_target_not_album": "Target is not an album (no album.json).",
            "import_source_referenced": "File is still referenced in the source album — remove it there first.",
            "delete_referenced": "File is referenced in an album — only the curator can delete it.",
            "delete_in_album_folder": "\u201e{file}\u201c belongs to an album — delete it there, not from here.",
            "delete_used_elsewhere": "\u201e{file}\u201c is used by {count} other album(s): {albums}. Remove it there first.",
            "conflict_modified": "Album was modified in the meantime (current: {current}).",
            "invalid_folder_name": "folder_name must not contain path separators or '..'.",
            "folder_already_exists": 'Folder “{folder}” already exists — please pick from the list or choose a different name.',
            "folder_not_in_synology": 'Folder “{folder}” not in Synology Photos.',
            "thumbnail_not_found": 'Thumbnail file “{file}” not found.',
            "source_required": "source_album and file are required.",
            "source_invalid_path": "source_album must not contain path separators.",
            "file_invalid_path": "file must not contain path separators.",
            "source_file_not_found": 'Source file “{file}” not found.',
            "target_not_found": 'Target album “{name}” not found.',
            "invalid_name": "Invalid album name.",
            "invalid_backup_name": "Invalid backup name.",
            "backup_name_mismatch": "Backup name does not match schema.",
            "backup_not_found": 'Backup “{file}” not found.',
            "exists_no_undo_needed": "Album already exists — no undo needed.",
            "filename_missing": "filename missing.",
            "invalid_filename": "Invalid filename.",
            "invalid_filepath": "Invalid file path.",
            "only_photos_allowed": "Only photo files allowed ({exts}).",
            "file_not_found": 'File “{file}” not found.',
            "not_a_regular_file": "Not a regular file.",
        },
        "share": {
            "disabled_global": "Sharing is currently disabled (global setting).",
            "invalid_or_revoked": "Share link invalid or revoked.",
            "album_gone": "Album no longer exists.",
            "owner_gone": "Share owner no longer exists.",
            "fields_required": "space and album_name are required.",
            "invalid_album_name": "Invalid album name.",
            "shared_only_admin_editor": "Sharing the shared space requires admin/editor.",
            "list_needs_both": "Pass space and album_name together — omit both to list all your own links.",
            "token_required": "token or token_hash required.",
            "share_not_found": "Share not found.",
            "not_your_share": "Not your share.",
            "token_missing": "token missing.",
            "invalid_email": "Invalid recipient address.",
            "message_too_long": "Personal message too long (max 2000 characters).",
            "smtp_not_configured": "SMTP not configured (MPD_SMTP_* in mpd.env).",
            "mail_send_failed": "Mail delivery failed: {msg}",
            "admin_only": "Admin only.",
        },
        "users": {
            "body_must_be_object": "Body must be an object.",
            "invalid_username": "Username: lowercase letters, digits, .-_ (2–32 chars, starting with a letter).",
            "invalid_role": "role must be one of {roles}.",
            "password_min": "Password: at least {n} characters.",
            "user_exists": 'User “{user}” already exists.',
            "user_unknown": "User unknown.",
            "display_name_must_be_string": "display_name must be a string.",
            "self_admin_role_locked": "Cannot remove your own admin role.",
            "last_admin_role_locked": "This is the last admin — role cannot be changed.",
            "self_delete_denied": "Cannot delete your own user.",
            "last_admin_delete_denied": "This is the last admin — cannot be deleted.",
            "admin_only": "Admin only.",
        },
        "photo_edit": {
            "not_editable": '“{file}” is not an editable photo.',
            "photo_not_found": 'Photo “{file}” not found.',
            "no_write_permission": "No write permission in this space.",
            "edit_error": "Edit error: {msg}",
            "reset_error": "Reset error: {msg}",
            "rename_error": "Rename error: {msg}",
            "exif_error": "EXIF error: {msg}",
        },
        "settings_be": {
            "connect_no_url": "No address known — please set MPD_BASE_URL.",
            "license_empty": "No license text given.",
            "license_invalid": "License not accepted: {msg}",
            "admin_only": "Admin only.",
            "body_must_be_object": "Body must be an object.",
        },
        "contacts": {
            "invalid_email": "Invalid email address.",
            "contact_not_found": "Contact not found.",
        },
        "mail": {
            "share_subject": 'Photo album “{album}” for you',
            "share_greeting_named": "Hello {name},",
            "share_greeting_anon": "Hello,",
            "share_intro": 'I shared an album with you — “{album}”:',
            "share_open_label": "Open album:",
            "share_open_btn": "Open album",
            "share_signoff": "Best regards,",
            "share_footer_text": "Shared from My Photo Diary.",
            "share_footer_html": "Shared from My Photo Diary &middot; where memories stay yours — and kindly shared",
            "share_brand_tagline": "where memories stay yours",
            "share_shared_by": "Shared by {name}",
            "share_album_alt": "Album cover",
            "share_subject_photo": "A photo for you",
            "share_intro_photo": "I shared a photo with you:",
            "share_open_label_photo": "Open photo:",
            "share_open_btn_photo": "Open photo",
            "share_photo_alt": "Shared photo",
            "test_subject": "MPD test mail",
            "test_body": "This is a test mail from My Photo Diary.\nIf you can read this, SMTP works correctly.\n",
        },
        "common": {
            "not_logged_in": "Not signed in.",
            "session_expired": "Session expired.",
            "no_shared_access": "No access to shared space.",
            "space_not_available": "Space '{space}' not available.",
            "invalid_path": "Invalid path.",
            "invalid_filename": "Invalid filename.",
            "file_not_found": "File not found.",
            "internal_error": "Internal error: {msg}",
            "error_generic": "Error: {msg}",
        },
        "static": {
            "not_found": "{kind} not found.",
        },
        "pdx": {
            "invalid_version": "Invalid PDX version: '{version}'.",
            "invalid_structure": "Invalid structure: 'meta' or 'elements' missing.",
            "elements_not_list": "'elements' must be a list.",
            "meta_field_missing": "meta.{field} missing.",
            "element_not_dict": "Element {idx} is not a dictionary.",
            "element_id_missing": "Element {idx}: 'id' missing.",
            "element_id_invalid": "Element {idx}: id '{id}' invalid.",
            "duplicate_id": "Duplicate id: {id}.",
            "text_missing": "Text element {id}: 'text' missing.",
            "file_missing": "{type} element {id}: 'file' missing.",
            "source_album_invalid": "Element {id}: 'source_album' must be a simple album folder name without path separators.",
            "link_url_missing": "Link element {id}: 'url' missing.",
            "link_url_invalid": "Link element {id}: 'url' must start with http:// or https://.",
            "link_url_too_long": "Link element {id}: 'url' is too long (max 2048 characters).",
        },
        "media": {
            "file_not_found": "File '{filename}' not found.",
            "image_not_found": "Image '{filename}' not found.",
            "video_not_found": "Video '{filename}' not found.",
            "audio_not_found": "Audio '{filename}' not found.",
            "doc_not_found": "Document '{filename}' not found.",
            "gpx_not_found": "GPX file '{filename}' not found.",
            "album_not_found": "Album '{album}' not found.",
            "not_a_video": "'{filename}' is not a video.",
            "not_a_gpx": "'{filename}' is not a GPX file.",
            "no_photo_thumb": "No photo thumbnail for '{ext}' — frontend should use the matching endpoint.",
            "thumb_error": "Thumbnail error: {msg}",
            "exif_error": "EXIF error: {msg}",
            "video_error": "Video error: {msg}",
            "video_thumb_error": "Video thumbnail error: {msg}",
            "ffmpeg_error": "FFmpeg error: {msg}",
            "ffmpeg_failed": "FFmpeg failed.",
            "doc_error": "Document error: {msg}",
            "audio_error": "Audio error: {msg}",
            "gpx_error": "GPX error: {msg}",
            "gpx_list_error": "GPX list error: {msg}",
            "demo_no_download": "Demo: original download not available.",
        },
        "memories": {
            "scan_error": "Error: {msg}",
            "frontend_not_found": "Memories frontend not found.",
            "no_photo_thumb": "No photo thumbnail for '{filename}'.",
            "file_not_found": "File '{photo_id}' not found.",
            "thumb_error": "Thumbnail error: {msg}",
            "demo_unavailable": "Demo: memories not available.",
        },
        "modules": {
            "locked": "Module “{module}” is not unlocked.",
        },
        "tours": {
            "invalid_filename": "Invalid filename.",
            "gpx_not_found": "GPX '{filename}' not found.",
            "fields_required": "filename and album required.",
            "invalid_path": "Invalid path.",
            "album_folder_not_found": "Album folder '{album}' not found.",
            "already_in_album": "'{filename}' already exists in '{album}'.",
            "demo_unavailable": "Demo: tours not available.",
        },
        "trails": {
            "invalid_filename": "Invalid filename.",
            "gpx_not_found": "GPX '{filename}' not found.",
            "demo_unavailable": "Demo: trails not available.",
        },
        "search": {
            "index_error": "Index error: {msg}",
            "frontend_not_found": "Search frontend not found.",
        },
        "stats_be": {
            "frontend_not_found": "Stats frontend not found.",
            "maptiler_not_configured": "MAPTILER_KEY not configured.",
        },
        "companion": {
            "disabled": "Companion disabled (no API key).",
            "demo_unavailable": "Demo: companion not available.",
            "empty_text": "Empty text.",
            "text_too_long": "Text too long (max {n} characters).",
            "timeout": "Companion timeout.",
            "unreachable": "Companion not reachable.",
            "api_error": "API error {code}.",
            "parse_error": "Response could not be parsed.",
            "no_suggestions": "No usable suggestions received.",
            "rate_limited": "Slow down a moment — try again shortly.",
            "quota_exhausted": "Hourly limit reached ({n} requests). Continue later.",
        },
        "share_be": {
            "unknown_space_in_share": "Unknown space in share: {space}.",
            "unknown_space": "Unknown space: {space}.",
            "default_og_missing": "Default OG image not installed.",
            "viewer_not_installed": "Share viewer not installed.",
            "album_gone": "Album no longer exists.",
            "not_video": "File is not a video.",
            "ffmpeg_failed": "FFmpeg failed.",
            "path_not_in_album": "Path not in shared album.",
            "paused_title": "Currently unavailable",
            "paused_body": "The sender has paused sharing temporarily.<br>Come back later &mdash; the link stays valid.",
            "invalid_title": "Link invalid",
            "invalid_body": "This share link is invalid or has been revoked.",
            "not_a_photo": "Only photos can be shared individually.",
            "photo_locked": "This photo is marked private.",
        },
        "albums_be": {
            "load_albums_error": "Error loading albums: {msg}",
            "load_album_error": "Error loading album: {msg}",
            "folder_not_in_synology": "Folder '{folder}' not in Synology Photos.",
        },
        "auth_be": {
            "rate_limit_share": "Too many invalid share-link requests. Please wait {seconds}s.",
            "rate_limit_login": "Too many failed login attempts. Please wait {seconds}s.",
            "invalid_request": "Invalid request.",
            "credentials_required": "Username and password required.",
            "demo_disabled": "Demo access is currently disabled.",
            "must_change_password": "Please set a new password first.",
            "old_password_wrong": "The current password is incorrect.",
            "password_too_short": "New password too short (at least {n} characters).",
            "password_unchanged": "The new password must differ from the current one.",
        },
    },
}


def lang_from_request(request: Optional[Any]) -> str:
    """Extract language from Accept-Language, fall back to DE."""
    if request is None:
        return DEFAULT_LANG
    raw = request.headers.get("accept-language", "")
    if not raw:
        return DEFAULT_LANG
    # First language in the header — e.g. "en-US,en;q=0.9,de;q=0.8" → "en"
    for part in raw.split(","):
        code = part.strip().split(";")[0].strip().split("-")[0].lower()
        if code in SUPPORTED:
            return code
    return DEFAULT_LANG


def _lookup(messages: Mapping, key: str) -> Optional[str]:
    cur = messages
    for p in key.split("."):
        if isinstance(cur, Mapping) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur if isinstance(cur, str) else None


def t_lang(key: str, lang: Optional[str] = None, vars: Optional[Mapping] = None) -> str:
    """
    Translate a key for an explicit language (used outside HTTP request scope,
    e.g. mail templates rendered from a background context). Same fallback
    chain as t().
    """
    lang = lang if (lang in SUPPORTED) else DEFAULT_LANG
    msg = _lookup(LOCALES.get(lang, {}), key)
    if msg is None and lang != DEFAULT_LANG:
        msg = _lookup(LOCALES[DEFAULT_LANG], key)
    if msg is None:
        return key
    if not vars:
        return msg
    try:
        return msg.format(**vars)
    except (KeyError, IndexError):
        return msg


def t(key: str, request: Optional[Any] = None, vars: Optional[Mapping] = None) -> str:
    """
    Translate a key into the request's language.

    Fallback chain:
    1. Look up in the detected language
    2. Look up in DE (fallback when an EN key is missing)
    3. The key string itself (defensive — visible as "album.not_found")
    """
    lang = lang_from_request(request)
    msg = _lookup(LOCALES.get(lang, {}), key)
    if msg is None and lang != DEFAULT_LANG:
        msg = _lookup(LOCALES[DEFAULT_LANG], key)
    if msg is None:
        return key
    if not vars:
        return msg
    try:
        return msg.format(**vars)
    except (KeyError, IndexError):
        return msg
