"""
routers/share.py
MPD share endpoints (Phase 2 of the sharing implementation).

Three groups:
  1. Owner API under /api/share/*  (session auth)
     - POST /api/share/create        create token
     - GET  /api/share/list          list shares of an album
     - POST /api/share/revoke        revoke token
  2. Share viewer under /share/<token>  (token auth, public)
     - GET /share/<token>            placeholder HTML (real viewer: Phase 3)
     - GET /share/<token>/api/album  filtered PDX JSON (without locked elements)

Media delivery (/share/<token>/thumbnail/..., /document/...) follows
in a separate round together with the frontend viewer.
"""

import asyncio
import html
import io
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from PIL import Image

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from core.http_range import ranged_file_response
from pydantic import BaseModel, ConfigDict, Field

from core import shares
from core import userdb
from core import ratelimit
from core import mailer
from core import contacts
from core import settings as app_settings
from core.i18n import t as _t, t_lang as _tl, lang_from_request
from core.thumb_pipeline import get_thumb, get_thumb_path, get_video_thumb_path
from config import PHOTO_PATH_SHARED
from routers.deps import (require_session, _VIDEO_EXTS, _PHOTO_EXTS,
                          _SIZE_ALIAS, _DOC_MIME, _PHOTO_MIME, _AUDIO_MIME,
                          _VIDEO_MIME, public_base_url)
from routers.albums import (
    _enrich_photo_meta,
    _is_safe_album_name,
    _migrate_hidden_to_locked,
    _write_atomic,
)

_FRONTEND = Path(__file__).parent.parent.parent / "frontend"


logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request models — extra='forbid' rejects unexpected fields with 422,
# so frontend/backend drift surfaces immediately.
# ---------------------------------------------------------------------------

class CreateShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space:        str = Field(min_length=1)
    album_name:   str = Field(min_length=1)
    theme:        Optional[str] = None
    # Teilen v2 (03.09.2026): optionales Ablaufdatum in Tagen.
    # None = unbefristet (Bestandsverhalten).
    expires_days: Optional[int] = Field(None, ge=1, le=3650)
    # Teilen v3 (04.09.2026): gesetzt = Einzelfoto-Share — der Link zeigt
    # genau dieses eine Foto des Albums, nicht das Album.
    file:         Optional[str] = Field(None, min_length=1, max_length=255)


class RevokeShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token:      Optional[str] = None
    token_hash: Optional[str] = None


class SendShareEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token:            str = Field(min_length=1)
    to_email:         str = Field(min_length=1)
    to_name:          Optional[str] = None
    personal_message: Optional[str] = None


class MailTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_email: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _share_base_path(info: shares.ShareInfo, request: Optional[Request] = None) -> Path:
    """
    Base path under which the shared album lives.
    - 'shared' → PHOTO_PATH_SHARED (global, all admins/editors share the same)
    - 'personal' → personal_path of the owner (from users.json)
    """
    if info.space == "shared":
        return PHOTO_PATH_SHARED
    if info.space == "personal":
        record = userdb.lookup(info.owner)
        if record is None:
            raise HTTPException(410, _t("share.owner_gone", request))
        return record.personal_path
    raise HTTPException(500, _t("share_be.unknown_space_in_share", request, {"space": info.space}))


def _ensure_share_enabled(request: Optional[Request] = None) -> None:
    """Global kill switch — admin setting. 503 instead of 404 so owner/
    recipient can see that it's not the link but the instance."""
    if not app_settings.get("share_enabled"):
        raise HTTPException(503, _t("share.disabled_global", request))


def _resolve_or_404(request: Request, token: str) -> shares.ShareInfo:
    _ensure_share_enabled(request)
    info = shares.resolve(token)
    if info is None:
        ip = request.client.host if request.client else "unknown"
        ratelimit.record_failure(ip)
        raise HTTPException(404, _t("share.invalid_or_revoked", request))
    return info


def _load_filtered_album(info: shares.ShareInfo, base: Path,
                         request: Optional[Request] = None) -> dict:
    """Load album.json + apply hidden→locked migration (not persisted)."""
    album_json_path = base / info.album_name / "album.json"
    if not album_json_path.exists():
        raise HTTPException(410, _t("share.album_gone", request))
    with open(album_json_path, "r", encoding="utf-8") as f:
        album_data = json.load(f)
    _migrate_hidden_to_locked(album_data)
    return album_data


def _assert_shared_path(info: shares.ShareInfo, base: Path,
                        album_name: str, filename: str,
                        request: Optional[Request] = None) -> Path:
    """
    Checks: is (album_name, filename) referenced in a non-locked element
    of the shared PDX? Returns the resolved file path or raises 404.
    Prevents a share token from granting access to any file inside the
    album folder — only the exact paths that appear in the filtered
    JSON are allowed.
    """
    base_resolved = base.resolve()

    # Teilen v3: Einzelfoto-Share — der Token gibt GENAU eine Datei frei.
    # Kein album.json-Abgleich (funktioniert damit auch im Durchlauf-Ordner);
    # eine spätere locked-Markierung sperrt über _photo_is_locked trotzdem.
    if info.file is not None:
        if album_name != info.album_name or filename != info.file:
            raise HTTPException(404, _t("share_be.path_not_in_album", request))
        candidate = (base / album_name / filename).resolve()
        if not candidate.is_relative_to(base_resolved):
            raise HTTPException(400, _t("common.invalid_path", request))
        if not candidate.is_file():
            raise HTTPException(404, _t("common.file_not_found", request))
        if _photo_is_locked(base, album_name, filename):
            raise HTTPException(404, _t("share_be.path_not_in_album", request))
        return candidate

    album_data = _load_filtered_album(info, base)

    for elem in album_data.get("elements", []):
        if elem.get("locked") is True:
            continue
        if elem.get("file") != filename:
            continue
        src = elem.get("source_album")
        resolved = src if (src and _is_safe_album_name(src)) else info.album_name
        if resolved != album_name:
            continue
        # Path validated against traversal — must resolve within base.
        candidate = (base / album_name / filename).resolve()
        if not candidate.is_relative_to(base_resolved):
            raise HTTPException(400, _t("common.invalid_path", request))
        if not candidate.is_file():
            raise HTTPException(404, _t("common.file_not_found", request))
        return candidate

    raise HTTPException(404, _t("share_be.path_not_in_album", request))


# ---------------------------------------------------------------------------
# Owner API (session auth — runs through AuthMiddleware like every /api/*)
# ---------------------------------------------------------------------------

def _photo_is_locked(base: Path, album_name: str, filename: str) -> bool:
    """Ist das Foto im album.json als locked markiert? Ohne album.json
    (Durchlauf-Ordner) gibt es keine Sperren → False."""
    album_json = base / album_name / "album.json"
    if not album_json.exists():
        return False
    try:
        with open(album_json, encoding="utf-8") as f:
            data = json.load(f)
        return any(e.get("file") == filename and e.get("locked") is True
                   for e in data.get("elements", []))
    except Exception:
        return True  # kaputtes JSON → lieber nicht teilen


@router.post("/api/share/create")
async def create_share(request: Request, body: CreateShareRequest):
    """
    Create a share token for an album. Returns plain token — only the
    hash stays in the DB. Owner = current session user.
    """
    session = require_session(request)
    _ensure_share_enabled()

    if body.space not in ("shared", "personal"):
        raise HTTPException(400, _t("share_be.unknown_space", request, {"space": body.space}))
    if not _is_safe_album_name(body.album_name):
        raise HTTPException(400, _t("share.invalid_album_name", request))

    # Teilen ist eine eigene Achse (routers/deps.may_share): im
    # Familienbestand admin+editor, im eigenen Bereich jeder — und darueber
    # seit 06.09.2026 ein Schalter je Konto. Bis dahin wurde im eigenen
    # Bereich GAR NICHT geprueft: ausgerechnet die einzige Handlung mit
    # Wirkung ausserhalb des Hauses war die einzige ungepruefte.
    from routers.deps import may_share
    if not may_share(session, body.space):
        raise HTTPException(403, _t("share.shared_only_admin_editor", request))

    # Teilen v3: Einzelfoto — Datei prüfen (existiert, ist ein Foto,
    # und ist nicht als privat (locked) markiert).
    if body.file is not None:
        fname = body.file
        if "/" in fname or "\\" in fname or ".." in fname:
            raise HTTPException(400, _t("common.invalid_path", request))
        if Path(fname).suffix.lower() not in _PHOTO_EXTS:
            raise HTTPException(400, _t("share_be.not_a_photo", request))
        from routers.deps import get_path
        base = get_path(session, body.space, request)
        photo_path = (base / body.album_name / fname).resolve()
        if not photo_path.is_relative_to(base.resolve()):
            raise HTTPException(400, _t("common.invalid_path", request))
        if not photo_path.is_file():
            raise HTTPException(404, _t("common.file_not_found", request))
        if _photo_is_locked(base, body.album_name, fname):
            raise HTTPException(403, _t("share_be.photo_locked", request))

    token = shares.create(
        space=body.space,
        album_name=body.album_name,
        owner=session.user,
        theme=body.theme or None,
        expires_days=body.expires_days,
        file=body.file,
    )
    return {"token": token, "expires_days": body.expires_days, "file": body.file}


@router.get("/api/share/list")
async def list_shares(request: Request,
                      space: Optional[str] = None,
                      album_name: Optional[str] = None):
    """Aktive Links als Metadaten, ohne Klartext-Token.

    Mit `space` UND `album_name`: die Links dieses Albums (Bestandsverhalten).
    Ohne beide (additiv 05.09.2026, angefordert von der App): **alle eigenen**
    Links — sonst müsste ein Besitzer-Screen über sämtliche Alben iterieren,
    ein Aufruf je Album. `space` und `album_name` stehen dann je Eintrag
    dabei, damit die Liste anzeigbar und verlinkbar ist.
    """
    session = require_session(request)
    if (space is None) != (album_name is None):
        raise HTTPException(400, _t("share.list_needs_both", request))

    if space is None:
        entries = shares.list_for_owner(session.user)
    else:
        if not _is_safe_album_name(album_name):
            raise HTTPException(400, _t("share.invalid_album_name", request))
        entries = shares.list_for_album(space, album_name)
        # Owner guard: only own shares visible (admins don't see others' either)
        entries = [e for e in entries if e.owner == session.user]

    return {
        "shares": [
            {
                "token_hash": e.token_hash,
                "space": e.space,
                "album_name": e.album_name,
                "created_at": e.created_at,
                "last_seen_at": e.last_seen_at,
                "theme": e.theme,
                "expires_at": e.expires_at,
                "file": e.file,
            }
            for e in entries
        ]
    }


@router.post("/api/share/revoke")
async def revoke_share(request: Request, body: RevokeShareRequest):
    """
    Revoke: either via plain token (if the owner still has it) or via
    hash (for the list UI, which only sees hashes).
    """
    session = require_session(request)
    token      = body.token
    token_hash = body.token_hash

    if not token and not token_hash:
        raise HTTPException(400, _t("share.token_required", request))

    # If hash is given: owner check via list + direct DELETE on hash.
    if token_hash and not token:
        # Find share via hash (no plain token known)
        entries_all = shares.list_for_owner(session.user)
        match = next((e for e in entries_all if e.token_hash == token_hash), None)
        if match is None:
            raise HTTPException(404, _t("share.share_not_found", request))
        # Revoke via a freshly generated "zombie token" hash: cumbersome.
        # Better: small backdoor at the DB level (delete the hash directly).
        _revoke_by_hash(token_hash)
        return {"revoked": True}

    # Plain-token revoke (owner copied it or just created it)
    info = shares.resolve(token)
    if info is None:
        raise HTTPException(404, _t("share.share_not_found", request))
    if info.owner != session.user:
        raise HTTPException(403, _t("share.not_your_share", request))
    shares.revoke(token)
    return {"revoked": True}


def _revoke_by_hash(token_hash: str) -> bool:
    """Internal helper — revokes without a plain token."""
    conn = shares._ensure_conn()
    with shares._lock:
        cur = conn.execute(
            "DELETE FROM shares WHERE token_hash = ?", (token_hash,)
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Share per email (Phase 6b)
# ---------------------------------------------------------------------------

import re as _re
_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Mail template helpers (Share-per-Mail, Phase 6b expansion)
# ---------------------------------------------------------------------------

def _br_escape(s: str) -> str:
    """HTML escape + line breaks → <br>."""
    return html.escape(s).replace("\n", "<br>")


def _build_cover_jpeg_for_mail(info: shares.ShareInfo) -> Optional[bytes]:
    """
    JPEG bytes for CID embedding in share mails (size=m thumb → PIL → JPEG);
    falls back to the default OG PNG when nothing usable is found.

    Welches Bild gezeigt wird, haengt am Share-Typ:
      * Album-Share  → das Albumcover (meta.thumbnail)
      * Foto-Share   → GENAU das geteilte Foto (info.file)

    Der Foto-Zweig ist der Grund fuer diese Unterscheidung: vorher bettete
    auch eine Einzelfoto-Mail das Albumcover ein und stellte dem Empfaenger
    damit ein Bild zu, das gar nicht geteilt worden war.
    """
    try:
        base = _share_base_path(info)

        if info.file is not None:
            # Teilen v3. Bewusst ohne _load_filtered_album: ein Foto-Share
            # braucht kein album.json und funktioniert so auch im
            # Durchlauf-Ordner (siehe _assert_shared_path).
            thumb_file = info.file
            if _photo_is_locked(base, info.album_name, info.file):
                thumb_file = None
        else:
            album_data = _load_filtered_album(info, base)
            meta       = album_data.get("meta", {}) or {}
            thumb_file = meta.get("thumbnail")

        # Traversal protection: der Dateiname ist per Konvention ohne
        # Pfadtrenner — sonst ueberspringen und das Standardbild nehmen.
        if (thumb_file
                and "/" not in thumb_file
                and "\\" not in thumb_file
                and ".." not in thumb_file):
            source = base / info.album_name / thumb_file
            if source.is_file():
                cache_file = get_thumb_path(source, "cover")  # = size=m
                if not cache_file.is_file():
                    get_thumb(source, "cover")
                if cache_file.is_file():
                    img = Image.open(cache_file).convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=78, optimize=True)
                    return buf.getvalue()
    except Exception:
        logger.warning("Album cover JPEG failed, falling back to default",
                       exc_info=True)

    default_png = _FRONTEND / "img" / "share-og-default.png"
    if default_png.exists():
        try:
            img = Image.open(default_png).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82, optimize=True)
            return buf.getvalue()
        except Exception:
            logger.warning("Default OG JPEG failed", exc_info=True)
    return None


def _build_share_mail_text(
    *, url: str, album_name: str, sender_name: str,
    to_name: Optional[str], personal_message: str, lang: str = "de",
    is_photo: bool = False,
) -> str:
    """Plaintext fallback — personal message gets a '> ' quote prefix.

    is_photo=True (Teilen v3): Foto-Formulierungen ohne Albumnamen."""
    greeting = (
        _tl("mail.share_greeting_named", lang, {"name": to_name})
        if to_name
        else _tl("mail.share_greeting_anon", lang)
    )
    if is_photo:
        intro      = _tl("mail.share_intro_photo", lang)
        open_label = _tl("mail.share_open_label_photo", lang)
    else:
        intro      = _tl("mail.share_intro", lang, {"album": album_name})
        open_label = _tl("mail.share_open_label", lang)

    if personal_message:
        quoted = "\n".join(f"> {line}" for line in personal_message.split("\n"))
        pm_block = f"\n{quoted}\n"
    else:
        pm_block = ""

    return (
        f"{greeting}\n"
        f"\n"
        f"{intro}\n"
        f"{pm_block}"
        f"\n"
        f"{open_label}\n"
        f"{url}\n"
        f"\n"
        f"{_tl('mail.share_signoff', lang)}\n"
        f"{sender_name}\n"
        f"\n"
        f"—\n"
        f"{_tl('mail.share_footer_text', lang)}\n"
    )


def _load_mail_logo_png() -> Optional[bytes]:
    """Small PNG logo for the brand line in share mails (CID: logo)."""
    p = _FRONTEND / "img" / "mail-logo.png"
    try:
        return p.read_bytes() if p.exists() else None
    except Exception:
        logger.warning("Mail logo PNG not readable", exc_info=True)
        return None


def _build_share_mail_html(
    *, url: str, album_name: str, sender_name: str,
    to_name: Optional[str], personal_message: str,
    has_cover: bool, has_logo: bool, lang: str = "de",
    is_photo: bool = False,
) -> str:
    """
    HTML body in paper layout. Table-based for Outlook compatibility.
    Expects the cover image to be embedded via cid:cover (attached as
    inline_image by the mailer) — when has_cover=False the image row
    is dropped.
    """
    greeting_html    = (
        html.escape(_tl("mail.share_greeting_named", lang, {"name": to_name}))
        if to_name
        else html.escape(_tl("mail.share_greeting_anon", lang))
    )
    album_name_html  = html.escape(album_name)
    sender_name_html = html.escape(sender_name)
    url_attr         = html.escape(url, quote=True)
    if is_photo:
        # Teilen v3: keine Album-Interpolation — der Titel bleibt draussen.
        intro_html     = html.escape(_tl("mail.share_intro_photo", lang))
        open_btn_html  = html.escape(_tl("mail.share_open_btn_photo", lang))
        album_alt_html = html.escape(_tl("mail.share_photo_alt", lang))
        title_html     = html.escape(_tl("mail.share_subject_photo", lang))
        headline_html  = title_html
    else:
        # Die Vorlage bringt die Anfuehrungszeichen selbst mit
        # (`… — „{album}“:`), das <em> setzt deshalb keine eigenen. Bis
        # 07.09.2026 tat es das doch, und die Mail zeigte „„Sommer 2026““.
        intro_html     = html.escape(
            _tl("mail.share_intro", lang, {"album": "__ALBUM__"})
        ).replace("__ALBUM__", f'<em style="font-style:italic;color:#2C4A6E;">{album_name_html}</em>')
        open_btn_html  = html.escape(_tl("mail.share_open_btn", lang))
        album_alt_html = html.escape(_tl("mail.share_album_alt", lang))
        title_html     = album_name_html
        headline_html  = album_name_html
    shared_by_html   = html.escape(_tl("mail.share_shared_by", lang, {"name": sender_name}))
    signoff_html     = html.escape(_tl("mail.share_signoff", lang))
    brand_tagline    = html.escape(_tl("mail.share_brand_tagline", lang))
    footer_html      = _tl("mail.share_footer_html", lang)

    if personal_message:
        pm_html = _br_escape(personal_message)
        pm_block = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'border="0" style="margin:0 0 22px;"><tr>'
            '<td width="3" bgcolor="#c8922a" '
            'style="background:#c8922a;font-size:0;line-height:0;width:3px;"></td>'
            '<td bgcolor="#f2eee1" style="background:#f2eee1;padding:12px 18px;'
            "font-family:Georgia,'Times New Roman',serif;font-size:15px;line-height:1.6;"
            'color:#3a3f4f;font-style:italic;">'
            f'{pm_html}'
            '</td></tr></table>'
        )
    else:
        pm_block = ""

    cover_row = (
        f'<tr><td><img src="cid:cover" alt="{album_alt_html}" width="480" '
        'style="display:block;width:100%;height:auto;border:0;" /></td></tr>'
    ) if has_cover else ""

    if has_logo:
        brand_inner = (
            '<tr>'
            '<td width="48" valign="middle" style="vertical-align:middle;width:48px;">'
              '<img src="cid:logo" alt="" width="40" height="40" '
              'style="display:block;width:40px;height:40px;border:0;" />'
            '</td>'
            '<td valign="middle" style="vertical-align:middle;padding-left:10px;'
            "font-size:13px;color:#6e7280;letter-spacing:0.02em;font-family:Georgia,serif;font-style:italic;\">"
            f'<b style="color:#2C4A6E;font-style:normal;font-weight:600;letter-spacing:0;">My Photo Diary</b> &mdash; {brand_tagline}'
            '</td></tr>'
        )
    else:
        brand_inner = (
            '<tr><td style="font-size:13px;color:#6e7280;letter-spacing:0.02em;'
            'font-family:Georgia,serif;font-style:italic;">'
            f'<b style="color:#2C4A6E;font-style:normal;font-weight:600;letter-spacing:0;">My Photo Diary</b> &mdash; {brand_tagline}'
            '</td></tr>'
        )

    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title_html}</title>
</head>
<body style="margin:0;padding:0;background:#f3f2ee;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f3f2ee;padding:24px 12px;">
 <tr><td align="center">
  <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%;background:#faf8f3;">
   <tr><td style="padding:28px 32px 24px;font-family:Georgia,'Times New Roman',serif;font-size:15px;line-height:1.6;color:#2c3240;">

    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px solid #e5e0d0;padding-bottom:16px;margin-bottom:20px;">
     {brand_inner}
    </table>

    <p style="font-size:16px;margin:0 0 12px;">{greeting_html}</p>
    <p style="margin:0 0 18px;">{intro_html}</p>

    {pm_block}

    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto 22px;max-width:480px;border:1px solid #e0dccb;">
     {cover_row}
     <tr><td bgcolor="#ffffff" style="background:#ffffff;padding:16px 20px 20px;text-align:center;">
      <div style="font-family:Georgia,'Times New Roman',serif;font-size:22px;color:#2C4A6E;font-weight:500;margin:0 0 4px;letter-spacing:-0.01em;">{headline_html}</div>
      <div style="font-size:13px;color:#7a7f8e;margin:0 0 16px;font-style:italic;font-family:Georgia,serif;">{shared_by_html}</div>
      <a href="{url_attr}" style="display:inline-block;background:#2C4A6E;color:#ffffff;text-decoration:none;padding:10px 22px;border-radius:4px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;letter-spacing:0.02em;">{open_btn_html}</a>
     </td></tr>
    </table>

    <p style="margin:0 0 4px;">{signoff_html}</p>
    <p style="margin:0;">{sender_name_html}</p>

    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:28px;border-top:1px solid #e5e0d0;padding-top:14px;">
     <tr><td align="center" style="font-size:11px;color:#8e8f95;font-family:Georgia,serif;font-style:italic;">
      {footer_html}
     </td></tr>
    </table>

   </td></tr>
  </table>
 </td></tr>
</table>
</body>
</html>"""


@router.post("/api/share/send-email")
async def send_share_email(request: Request, body: SendShareEmailRequest):
    """
    Sends a freshly created share link via email (multipart/alternative:
    plaintext + HTML with CID cover).
    The plain token must be sent along — after the /create call only
    the owner client has it, the DB only knows the hash.
    """
    session = require_session(request)
    _ensure_share_enabled()

    token            = body.token.strip()
    to_email         = body.to_email.strip().lower()
    to_name          = (body.to_name or "").strip() or None
    personal_message = (body.personal_message or "").strip()

    if not token:
        raise HTTPException(400, _t("share.token_missing", request))
    if not to_email or not _EMAIL_RE.match(to_email) or len(to_email) > 254:
        raise HTTPException(400, _t("share.invalid_email", request))
    if len(personal_message) > 2000:
        raise HTTPException(400, _t("share.message_too_long", request))

    info = shares.resolve(token)
    if info is None:
        raise HTTPException(404, _t("share.invalid_or_revoked", request))
    if info.owner != session.user:
        raise HTTPException(403, _t("share.not_your_share", request))

    if not mailer.is_configured():
        raise HTTPException(503, _t("share.smtp_not_configured", request))

    base = public_base_url(request)
    url  = f"{base}/share/{token}"

    owner_record = userdb.lookup(session.user)
    sender_name  = (owner_record and owner_record.display_name) or session.user

    # Mail language follows the sender's UI (Accept-Language). Recipient
    # link carries its own ?lang= for the share viewer (set when creating).
    lang = lang_from_request(request)

    # Teilen v3: Einzelfoto-Share spricht anders und zeigt ein anderes Bild.
    is_photo = info.file is not None

    subject = (_tl("mail.share_subject_photo", lang) if is_photo
               else _tl("mail.share_subject", lang, {"album": info.album_name}))

    cover_jpeg = await asyncio.to_thread(_build_cover_jpeg_for_mail, info)
    logo_png   = _load_mail_logo_png()

    text_body = _build_share_mail_text(
        url=url, album_name=info.album_name, sender_name=sender_name,
        to_name=to_name, personal_message=personal_message, lang=lang,
        is_photo=is_photo,
    )
    html_body = _build_share_mail_html(
        url=url, album_name=info.album_name, sender_name=sender_name,
        to_name=to_name, personal_message=personal_message,
        has_cover=cover_jpeg is not None,
        has_logo =logo_png   is not None,
        lang=lang, is_photo=is_photo,
    )

    inline_images = []
    if cover_jpeg:
        inline_images.append(("cover", cover_jpeg, "jpeg"))
    if logo_png:
        inline_images.append(("logo", logo_png, "png"))
    inline_images = inline_images or None

    try:
        mailer.send(
            to=to_email, subject=subject,
            body_text=text_body, body_html=html_body,
            inline_images=inline_images,
        )
    except mailer.MailerNotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        logger.exception("Share-per-mail failed")
        raise HTTPException(502, _t("share.mail_send_failed", request, {"msg": str(e)}))

    # Successfully sent — add recipient to the address book (MRU).
    try:
        contacts.upsert(session.user, to_email, to_name)
    except Exception:
        logger.warning("contacts.upsert failed", exc_info=True)

    return {"sent": True}


@router.post("/api/mail-test")
async def mail_test(request: Request, body: MailTestRequest):
    """
    Admin-only: sends a test mail to the given address to verify SMTP
    configuration after env changes.
    """
    session = require_session(request)
    if session.role != "admin":
        raise HTTPException(403, _t("share.admin_only", request))

    to_email = body.to_email.strip().lower()
    if not to_email or not _EMAIL_RE.match(to_email) or len(to_email) > 254:
        raise HTTPException(400, _t("share.invalid_email", request))

    if not mailer.is_configured():
        raise HTTPException(503, _t("share.smtp_not_configured", request))

    lang = lang_from_request(request)
    try:
        mailer.send(
            to        = to_email,
            subject   = _tl("mail.test_subject", lang),
            body_text = _tl("mail.test_body", lang),
        )
    except Exception as e:
        logger.exception("Test mail failed")
        raise HTTPException(502, _t("share.mail_send_failed", request, {"msg": str(e)}))
    return {"sent": True}


# ---------------------------------------------------------------------------
# Public share viewer (token auth, bypasses the session)
# ---------------------------------------------------------------------------

# Important: this route MUST be declared before /share/{token}, otherwise
# the token matcher swallows the path and throws 404.
@router.get("/share/og-default.png")
async def share_og_default(request: Request):
    """Fallback OG image for albums without cover. Publicly fetchable
    (no token) so messenger crawlers (WhatsApp, Signal, iMessage) can
    load it without auth."""
    path = _FRONTEND / "img" / "share-og-default.png"
    if not path.exists():
        raise HTTPException(404, _t("share_be.default_og_missing", request))
    return FileResponse(path, media_type="image/png", headers=_CACHE_24H)


@router.get("/share/{token}", response_class=HTMLResponse)
async def share_viewer(request: Request, token: str):
    """
    Serves the share viewer page (share.html) with OG metadata so that
    WhatsApp/Signal/iMessage/mail can render a preview tile.
    Token resolution + album lookup in the frontend still happens via
    /share/<token>/api/album.
    """
    # Global kill switch: friendly page instead of 404, no OG tags
    if not app_settings.get("share_enabled"):
        lang = lang_from_request(request)
        return HTMLResponse(
            f"""<!doctype html><html lang="{lang}"><head>
            <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
            <meta name="referrer" content="no-referrer">
            <title>{_t("share_be.paused_title", request)}</title>
            <style>body{{font-family:system-ui;text-align:center;padding:4em;color:#444}}</style>
            </head><body>
            <h1>🌙</h1>
            <p>{_t("share_be.paused_body", request)}</p>
            </body></html>""",
            status_code=503,
        )
    info = shares.resolve(token)
    if info is None:
        ip = request.client.host if request.client else "unknown"
        ratelimit.record_failure(ip)
        lang = lang_from_request(request)
        return HTMLResponse(
            f"""<!doctype html><html lang="{lang}"><head>
            <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
            <meta name="referrer" content="no-referrer">
            <title>{_t("share_be.invalid_title", request)}</title>
            <style>body{{font-family:system-ui;text-align:center;padding:4em;color:#444}}</style>
            </head><body>
            <h1>🔒</h1>
            <p>{_t("share_be.invalid_body", request)}</p>
            </body></html>""",
            status_code=404,
        )

    # Teilen v3: Einzelfoto-Links bekommen die schlanke Foto-Seite
    # statt des Album-Viewers.
    page_name = "share-photo.html" if info.file else "share.html"
    path = _FRONTEND / page_name
    if not path.exists():
        raise HTTPException(500, _t("share_be.viewer_not_installed", request))

    _base = public_base_url(request)

    # OG metadata deliberately bare: the tile in messengers shows only
    # the MPD logo and brand claim, no album contents, no sender.
    # Memories live behind the click, not in the link preview.
    replacements = {
        "{{OG_TITLE}}"      : "My Photo Diary",
        "{{OG_DESCRIPTION}}": "where memories stay yours — and kindly shared",
        "{{OG_IMAGE}}"      : f"{_base}/share/og-default.png",
        "{{OG_URL}}"        : html.escape(f"{_base}/share/{token}", quote=True),
        "{{TOKEN}}"         : quote(token),
        "{{ALBUM}}"         : quote(info.album_name),
        "{{FILE}}"          : quote(info.file or ""),
    }

    html_str = path.read_text(encoding="utf-8")
    for k, v in replacements.items():
        html_str = html_str.replace(k, v)
    return HTMLResponse(html_str, headers={"Cache-Control": "no-store"})


@router.get("/share/{token}/api/album")
async def share_album_json(request: Request, token: str):
    """
    Shared PDX as JSON. In the share context all `locked` elements are
    already removed here (defense in depth — the frontend never gets
    to see them). `meta.show_locked` is forced to false unconditionally.
    """
    info = _resolve_or_404(request, token)
    if info.file is not None:
        # Einzelfoto-Token gibt kein Album frei
        raise HTTPException(404, _t("share_be.path_not_in_album", request))

    base            = _share_base_path(info)
    album_dir       = base / info.album_name
    album_json_path = album_dir / "album.json"

    if not album_json_path.exists():
        raise HTTPException(410, _t("share_be.album_gone", request))

    with open(album_json_path, "r", encoding="utf-8") as f:
        album_data = json.load(f)

    # Legacy migrations identical to the normal GET route
    migrated       = await _enrich_photo_meta(base, info.album_name,
                                              album_data.get("elements", []))
    locked_renamed = _migrate_hidden_to_locked(album_data)
    if migrated or locked_renamed:
        _write_atomic(album_json_path, album_data)

    # Share filter: drop locked, before any enrichment.
    filtered = [
        e for e in album_data.get("elements", []) if e.get("locked") is not True
    ]

    # Rewrite media URLs to the share proxy endpoints. The viewer only
    # needs the token — it knows neither space nor owner.
    enriched = []
    for elem in filtered:
        copy      = elem.copy()
        elem_type = elem.get("type")

        if elem_type in ("photo", "video", "document", "audio", "tour") and "file" in elem:
            filename     = elem["file"]
            filename_enc = quote(filename)
            src_album    = elem.get("source_album")
            if src_album and _is_safe_album_name(src_album):
                resolved_album = src_album
            else:
                resolved_album = info.album_name
            resolved_enc = quote(resolved_album)
            file_path    = base / resolved_album / filename

            if not file_path.is_file():
                copy["error"] = f"'{filename}' nicht gefunden"
            else:
                is_pdf = filename.lower().endswith(".pdf")
                base_url = f"/share/{token}"

                if elem_type == "document" and is_pdf:
                    copy["document_url"] = f"{base_url}/document/{resolved_enc}/{filename_enc}"
                elif elem_type == "audio":
                    copy["audio_url"] = f"{base_url}/audio/{resolved_enc}/{filename_enc}"
                elif elem_type == "tour":
                    copy["gpx_url"] = f"{base_url}/gpx/{resolved_enc}/{filename_enc}"
                else:
                    # photo, video, document(inline-image) → thumbnail set
                    copy["thumbnails"] = {
                        "sm": f"{base_url}/thumbnail/{resolved_enc}/{filename_enc}?size=sm",
                        "m" : f"{base_url}/thumbnail/{resolved_enc}/{filename_enc}?size=m",
                        "xl": f"{base_url}/thumbnail/{resolved_enc}/{filename_enc}?size=xl",
                    }
                    if elem_type == "video":
                        copy["video_url"]       = f"{base_url}/video/{resolved_enc}/{filename_enc}"
                        copy["video_thumb_url"] = f"{base_url}/video-thumb/{resolved_enc}/{filename_enc}"

        enriched.append(copy)

    meta = dict(album_data.get("meta", {}))
    meta["show_locked"] = False  # forced — regardless of what the PDX says

    return {
        "meta"     : meta,
        "elements" : enriched,
        "space"    : info.space,
        "share"    : {
            "album_name": info.album_name,
            "owner"     : info.owner,
            "theme"     : info.theme,
        },
        "read_only": True,
    }


# ---------------------------------------------------------------------------
# Media proxies — only deliver files when the requested path is referenced
# in the shared (unlocked) PDX. No delivery of locked elements, no directory
# traversal, no foreign albums.
# ---------------------------------------------------------------------------

_CACHE_24H = {"Cache-Control": "public, max-age=86400"}


@router.get("/share/{token}/thumbnail/{album_name}/{filename}")
async def share_thumbnail(
    request   : Request,
    token     : str,
    album_name: str,
    filename  : str,
    size      : str = Query("m", pattern="^(sm|m|xl|thumb|cover|preview)$"),
):
    info   = _resolve_or_404(request, token)
    base   = _share_base_path(info)
    source = _assert_shared_path(info, base, album_name, filename)

    pipeline_size = _SIZE_ALIAS.get(size, size)
    cache_file    = get_thumb_path(source, pipeline_size)
    if not cache_file.is_file():
        await asyncio.to_thread(get_thumb, source, pipeline_size)
    return FileResponse(cache_file, media_type="image/webp", headers=_CACHE_24H)


@router.get("/share/{token}/download")
async def share_photo_download(request: Request, token: str):
    """Teilen v3: Original-Datei eines Einzelfoto-Shares als Download.
    Nur für Foto-Shares — Album-Tokens haben keinen Pauschal-Download."""
    info = _resolve_or_404(request, token)
    if info.file is None:
        raise HTTPException(404, _t("share_be.path_not_in_album", request))
    base   = _share_base_path(info)
    source = _assert_shared_path(info, base, info.album_name, info.file, request)
    # Foto-Karte, nicht die Dokument-Karte: sonst gehen .heic, .gif und
    # .webp als image/jpeg raus. Rueckfall wie bei share_document.
    media_type = _PHOTO_MIME.get(source.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path       = str(source),
        media_type = media_type,
        headers    = {"Content-Disposition": f'attachment; filename="{info.file}"',
                      **_CACHE_24H},
    )


@router.get("/share/{token}/document/{album_name}/{filename}")
async def share_document(request: Request, token: str, album_name: str, filename: str):
    info   = _resolve_or_404(request, token)
    base   = _share_base_path(info)
    source = _assert_shared_path(info, base, album_name, filename)

    media_type = _DOC_MIME.get(source.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path       = str(source),
        media_type = media_type,
        headers    = {"Content-Disposition": f'inline; filename="{filename}"',
                      **_CACHE_24H},
    )


@router.get("/share/{token}/audio/{album_name}/{filename}")
async def share_audio(request: Request, token: str, album_name: str, filename: str):
    info   = _resolve_or_404(request, token)
    base   = _share_base_path(info)
    source = _assert_shared_path(info, base, album_name, filename)

    media_type = _AUDIO_MIME.get(source.suffix.lower(), "audio/mpeg")
    # ranged_file_response: der Accept-Ranges-Header war bisher ein leeres
    # Versprechen (Starlette 0.35 kann kein Range) — jetzt echtes 206.
    return ranged_file_response(
        request, source,
        media_type = media_type,
        headers    = {"Accept-Ranges": "bytes", **_CACHE_24H},
    )


@router.get("/share/{token}/video/{album_name}/{filename}")
async def share_video(request: Request, token: str, album_name: str, filename: str):
    info   = _resolve_or_404(request, token)
    base   = _share_base_path(info)
    source = _assert_shared_path(info, base, album_name, filename)

    if source.suffix.lower() not in _VIDEO_EXTS:
        raise HTTPException(400, _t("share_be.not_video", request))
    media_type = _VIDEO_MIME.get(source.suffix.lower(), "video/mp4")
    # ranged_file_response: echtes 206 — geteilte Videos sind damit beim
    # Empfaenger spulbar (Audit-/Refactoring-Fund 02.09.2026).
    return ranged_file_response(
        request, source,
        media_type = media_type,
        headers    = {"Accept-Ranges": "bytes"},
    )


@router.get("/share/{token}/video-thumb/{album_name}/{filename}")
async def share_video_thumb(request: Request, token: str, album_name: str, filename: str):
    info   = _resolve_or_404(request, token)
    base   = _share_base_path(info)
    source = _assert_shared_path(info, base, album_name, filename)

    thumb_path = get_video_thumb_path(source)

    if not thumb_path.exists():
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        # ffmpeg can take seconds — keep the event loop free so other
        # share viewers (and the owner) don't freeze meanwhile.
        result = await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-y", "-ss", "00:00:01", "-i", str(source),
             "-vframes", "1", "-q:v", "4", str(thumb_path)],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not thumb_path.exists():
            raise HTTPException(500, _t("share_be.ffmpeg_failed", request))

    return Response(
        content    = thumb_path.read_bytes(),
        media_type = "image/jpeg",
        headers    = _CACHE_24H,
    )


@router.get("/share/{token}/gpx/{album_name}/{filename}")
async def share_gpx(request: Request, token: str, album_name: str, filename: str):
    info   = _resolve_or_404(request, token)
    base   = _share_base_path(info)
    source = _assert_shared_path(info, base, album_name, filename)

    return FileResponse(
        path       = str(source),
        media_type = "application/gpx+xml",
        headers    = _CACHE_24H,
    )


# ---------------------------------------------------------------------------
# Beisteuern-Links (Teilen v2, 03.09.2026) — Gast-Upload in Sammelordner.
#
# Konstruktive Garantie: Ein Beisteuern-Link zeigt NIE auf ein Album —
# das Ziel darf keine album.json haben (Begruendung: „stoert die kuratierten
# Alben nicht"). Gäste erreichen ausschließlich diesen einen Ordner,
# sehen keine Dateiliste (Briefkasten + Zähler), Ablauf ist Pflicht.
# ---------------------------------------------------------------------------

_CONTRIB_MAX_BYTES  = 500 * 1024 * 1024   # 500 MB je Datei (Gast-Limit)
_CONTRIB_EXTS       = {".jpg", ".jpeg", ".png", ".heic", ".webp",
                       ".mp4", ".mov", ".m4v", ".3gp"}
import re as _guest_re_mod
_GUEST_RE = _guest_re_mod.compile(r"[^a-z0-9]+")


class CreateContribRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space:        str = Field(min_length=1)
    folder_name:  str = Field(min_length=1)
    expires_days: int = Field(14, ge=1, le=365)
    max_files:    int = Field(500, ge=1, le=5000)


class RevokeContribRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token:      Optional[str] = None
    token_hash: Optional[str] = None


@router.post("/api/contrib/create")
async def create_contrib_link(request: Request, body: CreateContribRequest):
    from routers.deps import is_readonly, get_path
    session = require_session(request)
    if body.space not in ("shared", "personal"):
        raise HTTPException(400, _t("share_be.unknown_space", request, {"space": body.space}))
    if is_readonly(session, body.space):
        raise HTTPException(403, _t("album.space_readonly", request,
                                    {"space": body.space, "role": session.role}))
    if not _is_safe_album_name(body.folder_name):
        raise HTTPException(400, _t("share.invalid_album_name", request))

    base   = get_path(session, body.space).resolve()
    folder = (base / body.folder_name).resolve()
    if not folder.is_relative_to(base):
        raise HTTPException(400, _t("common.invalid_path", request))
    if (folder / "album.json").exists():
        raise HTTPException(400, "Beisteuern-Links zeigen nie auf Alben — bitte einen Sammelordner nehmen")
    folder.mkdir(parents=False, exist_ok=True)

    token = shares.create_contrib(
        space=body.space, folder_name=body.folder_name,
        owner=session.user, expires_days=body.expires_days,
        max_files=body.max_files,
    )
    return {"token": token, "url": f"/contrib/{token}",
            "expires_days": body.expires_days, "max_files": body.max_files}


@router.get("/api/contrib/list")
async def list_contrib_links(request: Request):
    session = require_session(request)
    return {"contribs": [
        {"token_hash": c.token_hash, "space": c.space,
         "folder_name": c.folder_name, "created_at": c.created_at,
         "expires_at": c.expires_at, "max_files": c.max_files,
         "uploaded": c.uploaded}
        for c in shares.list_contribs_for_owner(session.user)
    ]}


@router.post("/api/contrib/revoke")
async def revoke_contrib_link(request: Request, body: RevokeContribRequest):
    session = require_session(request)
    ok = shares.revoke_contrib(owner=session.user,
                               token=body.token, token_hash=body.token_hash)
    if not ok:
        raise HTTPException(404, _t("share.not_found", request))
    return {"revoked": True}


def _resolve_contrib_or_404(request: Request, token: str):
    info = shares.resolve_contrib(token)
    if info is None:
        ip = request.client.host if request.client else "unknown"
        ratelimit.record_failure(ip)
        raise HTTPException(404, _t("share.not_found", request))
    return info


def _contrib_base(info) -> Path:
    from config import PHOTO_PATH_SHARED
    if info.space == "shared":
        return PHOTO_PATH_SHARED
    from core.userdb import lookup
    rec = lookup(info.owner)
    if rec is None:
        raise HTTPException(404, _t("share.not_found", request=None))
    return rec.personal_path


@router.get("/contrib/{token}", response_class=HTMLResponse)
async def contrib_page(request: Request, token: str):
    info = _resolve_contrib_or_404(request, token)
    path = Path(__file__).parent.parent.parent / "frontend" / "contrib.html"
    html_str = path.read_text(encoding="utf-8")
    html_str = (html_str
                .replace("{{TOKEN}}", html.escape(token))
                .replace("{{FOLDER}}", html.escape(info.folder_name)))
    return HTMLResponse(html_str, headers={"Cache-Control": "no-store",
                                           "Referrer-Policy": "no-referrer"})


@router.get("/contrib/{token}/status")
async def contrib_status(request: Request, token: str):
    info = _resolve_contrib_or_404(request, token)
    return {"folder": info.folder_name, "uploaded": info.uploaded,
            "max_files": info.max_files, "expires_at": info.expires_at}


@router.post("/contrib/{token}/upload")
async def contrib_upload(
    request: Request, token: str,
    filename: str = Query(..., min_length=1, max_length=255),
    guest: str = Query("", max_length=40),
):
    import os as _os
    import secrets as _secrets
    import tempfile as _tempfile
    from core.timeline_index import invalidate_timeline_index

    info = _resolve_contrib_or_404(request, token)

    suffix = Path(filename).suffix.lower()
    if suffix not in _CONTRIB_EXTS:
        raise HTTPException(415, _t("album.only_photos_allowed", request,
                                    {"exts": ", ".join(sorted(_CONTRIB_EXTS))}))
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _CONTRIB_MAX_BYTES:
        raise HTTPException(413, _t("album.upload_too_large", request))

    # Zähler ZUERST atomar erhöhen — bei vollem Topf kein Byte annehmen
    if not shares.count_contrib_upload(info.token_hash):
        raise HTTPException(403, "Dieser Beisteuern-Link ist voll")

    base   = _contrib_base(info).resolve()
    folder = (base / info.folder_name).resolve()
    if not folder.is_relative_to(base) or not folder.is_dir():
        raise HTTPException(404, _t("share.not_found", request))

    # Serververgebener Name: kein Datum (die Timeline soll das ECHTE
    # Aufnahmedatum aus dem EXIF ziehen, nicht den Upload-Moment),
    # Gast-Kürzel fürs Wiedererkennen, Zufall gegen Kollisionen.
    slug = _GUEST_RE.sub("", guest.lower())[:12] or "gast"
    dest = folder / f"beitrag-{slug}-{_secrets.token_hex(3)}{suffix}"
    while dest.exists():
        dest = folder / f"beitrag-{slug}-{_secrets.token_hex(3)}{suffix}"

    size = 0
    fd, tmp = _tempfile.mkstemp(dir=folder, prefix=".contrib_tmp.", suffix=suffix)
    try:
        with _os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > _CONTRIB_MAX_BYTES:
                    raise HTTPException(413, _t("album.upload_too_large", request))
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, _t("album.upload_empty", request))
        _os.replace(tmp, dest)
    except BaseException:
        try:
            _os.unlink(tmp)
        except OSError:
            pass
        raise

    invalidate_timeline_index()
    if suffix in {".jpg", ".jpeg", ".png", ".heic", ".webp"}:
        from routers.album_files import _warm_in_background
        _warm_in_background(dest)
    logger.info("Beitrag: %s/%s ← %s (%d Bytes, Gast=%s)",
                info.space, info.folder_name, dest.name, size, slug)
    return {"ok": True, "stored_as": dest.name}
