"""
routers/photo_edit.py
Photo editor endpoints (destructive, with rolling backup).

POST   /api/photo/{space}/{album_name}/{filename}/edit
DELETE /api/photo/{space}/{album_name}/{filename}/edit           (reset → .bak)
POST   /api/photo/{space}/{album_name}/{filename}/rename/preview (dry-run)
POST   /api/photo/{space}/{album_name}/{filename}/rename         (ISO rename)
POST   /api/photo/{space}/{album_name}/{filename}/exif           (DateTime/GPS + optional rename)
"""

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import photo_edit
from core.i18n import t as _t
from core.media_types import EDITABLE_EXTS
from routers.deps import require_session, get_path, may_edit_photo
from core.timeline_index import invalidate_timeline_index
from core import events

logger = logging.getLogger(__name__)
router = APIRouter()


class CropBox(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class EditRequest(BaseModel):
    rotation:   int             = 0
    flip:       bool            = False
    flip_v:     bool            = False
    straighten: float           = 0.0
    brightness: int             = 0
    contrast:   int             = 0
    saturation: int             = 0
    crop:       CropBox | None  = None


def _safe_photo_path(base: Path, album_name: str, filename: str,
                     request: Optional[Request] = None) -> Path:
    """Pfad-Haertung fuer die SCHREIBENDEN Foto-Endpunkte.

    Sicherheits-Audit M3, nachgezogen 05.09.2026: Hier stand bis dahin nur
    eine Zeichenkettenpruefung auf '..' und '/'. Damit kam ein Foto durch,
    das in Wahrheit ein **Symlink nach draussen** ist — und die Endpunkte
    dieser Datei lesen nicht, sie SCHREIBEN: edit_photo bearbeitet das
    Bild, rename_photo benennt um, save_exif schreibt Metadaten. Ein
    Symlink im Albumordner waere also kein Leseleck gewesen, sondern die
    Moeglichkeit, fremde Dateien zu ueberschreiben.

    resolve() folgt Symlinks; is_relative_to() gegen die aufgeloeste Basis
    faengt damit beides ab — Rest-'..' und Symlink-Ausbruch. Dieselbe
    Mechanik wie resolve_media_path() in deps.py, das die lesenden
    Endpunkte absichert.
    """
    for part in (album_name, filename):
        s = str(part)
        if not s or ".." in s or "\\" in s or s.startswith(("~", "/")):
            raise HTTPException(400, _t("common.invalid_path", request))
    if "/" in filename:
        raise HTTPException(400, _t("common.invalid_path", request))

    root = base.resolve()
    path = (root / album_name / filename).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(400, _t("common.invalid_path", request))
    # EDITABLE_EXTS, nicht PHOTO_EXTS: seit 07.09.2026 zeigt MPD auch
    # Formate an, die es nicht zurueckschreiben soll (mehrseitige TIFFs).
    if path.suffix.lower() not in EDITABLE_EXTS:
        raise HTTPException(400, _t("photo_edit.not_editable", request, {"file": filename}))
    if not path.is_file():
        raise HTTPException(404, _t("photo_edit.photo_not_found", request, {"file": filename}))
    return path


@router.post("/api/photo/{space}/{album_name}/{filename}/edit")
async def edit_photo(request: Request, space: str, album_name: str, filename: str, body: EditRequest):
    session = require_session(request)
    if not may_edit_photo(session, space, get_path(session, space) / album_name, filename):
        raise HTTPException(403, _t("photo_edit.no_write_permission", request))

    base = get_path(session, space)
    _safe_photo_path(base, album_name, filename, request)   # validation (path, extension, existence)

    params: dict[str, Any] = body.model_dump()
    if params.get("crop") is None:
        params.pop("crop", None)

    try:
        result = photo_edit.save_edit(
            base       = base,
            album_name = album_name,
            filename   = filename,
            params     = params,
        )
    except FileNotFoundError as e:
        # N2 (Audit): kein absoluter NAS-Pfad im Fehlertext — Details ins Log
        logger.warning("photo_edit: not found %s/%s/%s: %s", space, album_name, filename, e)
        raise HTTPException(404, _t("media.file_not_found", request, {"filename": filename}))
    except Exception as e:
        logger.exception("Edit failed for %s/%s/%s", space, album_name, filename)
        raise HTTPException(500, _t("photo_edit.edit_error", request, {"msg": str(e)}))

    logger.info(
        "Photo edit saved: user=%s %s/%s/%s backup=%s",
        session.user, space, album_name, filename, result["backup"],
    )
    return {"status": "ok", **result}


@router.delete("/api/photo/{space}/{album_name}/{filename}/edit")
async def reset_photo(request: Request, space: str, album_name: str, filename: str):
    session = require_session(request)
    if not may_edit_photo(session, space, get_path(session, space) / album_name, filename):
        raise HTTPException(403, _t("photo_edit.no_write_permission", request))

    base = get_path(session, space)
    _safe_photo_path(base, album_name, filename, request)

    try:
        result = photo_edit.reset_to_original(
            base       = base,
            album_name = album_name,
            filename   = filename,
        )
    except FileNotFoundError as e:
        # N2 (Audit): kein absoluter NAS-Pfad im Fehlertext — Details ins Log
        logger.warning("photo_edit: not found %s/%s/%s: %s", space, album_name, filename, e)
        raise HTTPException(404, _t("media.file_not_found", request, {"filename": filename}))
    except Exception as e:
        logger.exception("Reset failed for %s/%s/%s", space, album_name, filename)
        raise HTTPException(500, _t("photo_edit.reset_error", request, {"msg": str(e)}))

    logger.info(
        "Photo reset: user=%s %s/%s/%s restored_from=%s",
        session.user, space, album_name, filename, result["restored_from"],
    )
    return {"status": "ok", **result}


# ---------------------------------------------------------------------------
# Rename (preview + apply)
# ---------------------------------------------------------------------------

class RenameRequest(BaseModel):
    new_stem: str


@router.post("/api/photo/{space}/{album_name}/{filename}/rename/preview")
async def rename_preview(request: Request, space: str, album_name: str, filename: str, body: RenameRequest):
    session = require_session(request)
    if not may_edit_photo(session, space, get_path(session, space) / album_name, filename):
        raise HTTPException(403, _t("photo_edit.no_write_permission", request))

    base = get_path(session, space)
    _safe_photo_path(base, album_name, filename, request)

    try:
        final_name, collision = photo_edit.compute_rename_target(
            album_dir = base / album_name,
            old_name  = filename,
            new_stem  = body.new_stem,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"final_name": final_name, "collision": collision}


@router.post("/api/photo/{space}/{album_name}/{filename}/rename")
async def rename_photo(request: Request, space: str, album_name: str, filename: str, body: RenameRequest):
    session = require_session(request)
    if not may_edit_photo(session, space, get_path(session, space) / album_name, filename):
        raise HTTPException(403, _t("photo_edit.no_write_permission", request))

    base = get_path(session, space)
    _safe_photo_path(base, album_name, filename, request)

    try:
        result = photo_edit.rename_photo(
            base       = base,
            album_name = album_name,
            old_name   = filename,
            new_stem   = body.new_stem,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        # N2 (Audit): kein absoluter NAS-Pfad im Fehlertext — Details ins Log
        logger.warning("photo_edit: not found %s/%s/%s: %s", space, album_name, filename, e)
        raise HTTPException(404, _t("media.file_not_found", request, {"filename": filename}))
    except Exception as e:
        logger.exception("Rename failed for %s/%s/%s", space, album_name, filename)
        raise HTTPException(500, _t("photo_edit.rename_error", request, {"msg": str(e)}))

    if result.get("renamed"):
        events.emit("album_changed", space=space, album=album_name)
        invalidate_timeline_index()
        logger.info(
            "Photo renamed: user=%s %s/%s/%s → %s (collision=%s, album_json=%s)",
            session.user, space, album_name, filename,
            result["renamed"], result["collision"], result["album_json_updated"],
        )
    return {"status": "ok", **result}


# ---------------------------------------------------------------------------
# EXIF save (DateTime + GPS, optionally with rename)
# ---------------------------------------------------------------------------

class GpsPayload(BaseModel):
    lat: float
    lon: float
    alt: float | None = None


class ExifRequest(BaseModel):
    datetime: str | None = None
    gps:      GpsPayload | None = None
    new_stem: str | None = None


@router.post("/api/photo/{space}/{album_name}/{filename}/exif")
async def save_exif(request: Request, space: str, album_name: str, filename: str, body: ExifRequest):
    session = require_session(request)
    if not may_edit_photo(session, space, get_path(session, space) / album_name, filename):
        raise HTTPException(403, _t("photo_edit.no_write_permission", request))

    base = get_path(session, space)
    _safe_photo_path(base, album_name, filename, request)

    meta: dict[str, Any] = {}
    if body.datetime:
        meta["datetime"] = body.datetime
    if body.gps:
        meta["gps"] = body.gps.model_dump(exclude_none=True)
    if body.new_stem:
        meta["new_stem"] = body.new_stem

    try:
        result = photo_edit.save_metadata(
            base       = base,
            album_name = album_name,
            filename   = filename,
            meta       = meta,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        # N2 (Audit): kein absoluter NAS-Pfad im Fehlertext — Details ins Log
        logger.warning("photo_edit: not found %s/%s/%s: %s", space, album_name, filename, e)
        raise HTTPException(404, _t("media.file_not_found", request, {"filename": filename}))
    except Exception as e:
        logger.exception("EXIF save failed for %s/%s/%s", space, album_name, filename)
        raise HTTPException(500, _t("photo_edit.exif_error", request, {"msg": str(e)}))

    if result.get("renamed"):
        events.emit("album_changed", space=space, album=album_name)
        invalidate_timeline_index()
    if body.gps:
        events.emit("photo_meta_changed", base=base, space=space, album=album_name)

    logger.info(
        "Photo exif saved: user=%s %s/%s/%s wrote_exif=%s renamed=%s backup=%s",
        session.user, space, album_name, filename,
        result.get("wrote_exif"), result.get("renamed"), result.get("backup"),
    )
    return {"status": "ok", **result}
