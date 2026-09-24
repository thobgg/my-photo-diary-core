"""
routers/albums.py
My Photo Diary v2 – album endpoints (session-based)

GET  /api/albums
GET  /api/albums/without-json
GET  /api/album/files
POST /api/album/create
GET  /api/album/{space}/{album_name}
POST /api/album/{space}/{album_name}/update
GET  /api/album/{space}/{album_name}/backups
GET  /api/album/{space}/{album_name}/unassigned-photos
POST /api/album/{space}/{album_name}/restore
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from core.thumb_pipeline import compute_photo_meta, etag_for
from core.i18n import t as _t
from core.media_types import is_editable
from routers.deps import (
    require_session, get_api, get_path, is_readonly, available_spaces,
    media_urls, _PHOTO_EXTS, _MEDIA_EXTS,
)
from core.timeline_index import invalidate_timeline_index
from core import events

# Cap for lazy migration per request. 200 covers typical family albums
# completely on first open (200 × 500 ms HEIC / 8 threads ≈ 12 s on
# DS225+, under the 60 s reverse-proxy timeout). Larger albums migrate
# across subsequent opens.
_MIGRATION_BATCH = 200

logger = router_logger = logging.getLogger(__name__)
router = APIRouter()


class CreateAlbumRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space:       str = Field(min_length=1)
    folder_name: str = Field(min_length=1)
    thumbnail:   str = Field(min_length=1)


class CreateEmptyAlbumRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space:       str = Field(min_length=1)
    folder_name: str = Field(min_length=1)


# Durchlauf-Ordner (Kuratier-Modell 02.09.2026): nackte Jahreszahl = der
# Sammelordner, in den Kamera und Upload liefern. Er darf NIE zum Album
# werden — sonst haengt der naechste Kamera-Upload als Element direkt
# hinein (Upload in einen Albumordner haengt an), und der Durchlauf ist
# keiner mehr. Bis 08.09.2026 stand er trotzdem unter „Album hinzufuegen":
# ohne album.json, mit Medien — genau das Muster der Liste.
_DURCHLAUF_RE = re.compile(r"^\d{4}$")


def is_durchlauf(folder_name: str) -> bool:
    return bool(_DURCHLAUF_RE.fullmatch((folder_name or "").strip()))


class CoverFromSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_album: str = Field(min_length=1)
    file:         str = Field(min_length=1)


# M1: verbotene Zeichen in Dateinamen aus album.json — Markup (<>),
# Attribut-Ausbruch ("'), Pfade (/\), Steuerzeichen. '&' bleibt erlaubt:
# kommt in echten Dateinamen vor und kann allein kein Markup erzeugen.
_FILENAME_BAD_RE = re.compile(r'[<>"\'/\\\x00-\x1f]')


def _safe_path(base: Path, *parts: str, request: Optional[Request] = None) -> Path:
    """Prevents path traversal via '..' in user input.

    Sicherheits-Audit 02.09.2026 (H1): Zusätzlich Pfadtrenner und absolute
    Pfade abweisen — Path.joinpath('/x') ERSETZT die Basis komplett, damit
    war z. B. über den Query-Parameter backup_filename jede Container-Datei
    erreichbar. Jeder erwartete Wert hier ist ein einzelnes Pfadsegment.
    """
    for part in parts:
        s = str(part)
        if ".." in s or "/" in s or "\\" in s or not s or s.startswith("~"):
            raise HTTPException(400, _t("common.invalid_path", request))
    return base.joinpath(*parts)


SUPPORTED_PDX_VERSIONS = {"1.1", "1.2", "1.3", "1.4", "1.4.1", "1.5"}


# PDX v1.4.1: erlaubte Schemata fuer link-Elemente. Bewusst eng — der Wert
# landet als href im Viewer, auch in oeffentlich geteilten Alben.
_LINK_URL_RE = re.compile(r"^https?://[^\s]+\Z", re.IGNORECASE)


def _is_safe_album_name(name: str) -> bool:
    """source_album must be a simple folder name — not a path."""
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_year_from_name(folder_name: str) -> int:
    """First 4-digit year (19xx/20xx) inside a folder name; 0 if none.
    Used as a sort key fallback when album.json has no explicit year."""
    match = re.search(r'\b(19|20)\d{2}\b', folder_name)
    return int(match.group()) if match else 0


def validate_album_data(data: dict, request: Optional[Request] = None):
    """PDX format gate before any write: version, required meta fields,
    element ID uniqueness, type-specific fields. Raises HTTPException(400)
    on the first violation. Empty albums are valid (PDX v1.4)."""
    version = data.get("version")
    if version not in SUPPORTED_PDX_VERSIONS:
        raise HTTPException(400, _t("pdx.invalid_version", request, {"version": version}))

    if "meta" not in data or "elements" not in data:
        raise HTTPException(400, _t("pdx.invalid_structure", request))
    if not isinstance(data["elements"], list):
        raise HTTPException(400, _t("pdx.elements_not_list", request))

    meta = data["meta"]
    for field in ("title", "year", "thumbnail", "created", "modified"):
        if field not in meta:
            raise HTTPException(400, _t("pdx.meta_field_missing", request, {"field": field}))

    ids         = set()
    known_types = {"text", "photo", "video", "separator", "document", "audio",
                   "map", "tour", "link"}
    id_pattern  = re.compile(r"^\d{4}$")

    for idx, elem in enumerate(data["elements"]):
        if not isinstance(elem, dict):
            raise HTTPException(400, _t("pdx.element_not_dict", request, {"idx": idx}))

        elem_type = elem.get("type")
        elem_id   = elem.get("id")

        if elem_type not in known_types:
            logger.info(f"Element {idx}: unknown type '{elem_type}' – tolerated")
            continue

        if not elem_id:
            raise HTTPException(400, _t("pdx.element_id_missing", request, {"idx": idx}))
        if not id_pattern.match(str(elem_id)):
            raise HTTPException(400, _t("pdx.element_id_invalid", request, {"idx": idx, "id": elem_id}))
        if elem_id in ids:
            raise HTTPException(400, _t("pdx.duplicate_id", request, {"id": elem_id}))
        ids.add(elem_id)

        if elem_type == "text" and "text" not in elem:
            raise HTTPException(400, _t("pdx.text_missing", request, {"id": elem_id}))
        if elem_type in ("photo", "video", "document", "tour") and "file" not in elem:
            raise HTTPException(400, _t("pdx.file_missing", request, {"type": elem_type, "id": elem_id}))

        # Sicherheits-Audit 02.09.2026 (M1): file muss ein schlichter
        # Dateiname sein. Ohne die Prüfung ließ sich Markup speichern, das
        # der Fehler-Platzhalter im Frontend per innerHTML rendert
        # (Stored XSS Richtung Admin und Share-Empfänger) — und Pfadzeichen
        # haben in einem Dateinamen ohnehin nichts verloren.
        fname = elem.get("file")
        if fname is not None and (
            not isinstance(fname, str) or not fname.strip()
            or len(fname) > 255 or ".." in fname
            or _FILENAME_BAD_RE.search(fname)
        ):
            raise HTTPException(400, _t("pdx.file_missing", request, {"type": elem_type, "id": elem_id}))

        # PDX v1.4.1: link element — verweist nach draußen, hat keine Datei.
        # Das Schema erlaubt nur http/https: javascript:, data: und file:
        # wuerden im Viewer zu einem anklickbaren XSS- bzw. Leak-Vektor.
        if elem_type == "link":
            url = elem.get("url")
            if not url or not isinstance(url, str) or not url.strip():
                raise HTTPException(400, _t("pdx.link_url_missing", request, {"id": elem_id}))
            url = url.strip()
            if len(url) > 2048:
                raise HTTPException(400, _t("pdx.link_url_too_long", request, {"id": elem_id}))
            if not _LINK_URL_RE.match(url):
                raise HTTPException(400, _t("pdx.link_url_invalid", request, {"id": elem_id}))

        # PDX v1.4: source_album references media from another album folder
        # (within the same space). Only a simple folder name is allowed.
        src_album = elem.get("source_album")
        if src_album is not None:
            if not isinstance(src_album, str) or not _is_safe_album_name(src_album):
                raise HTTPException(
                    400,
                    _t("pdx.source_album_invalid", request, {"id": elem_id})
                )

    # PDX v1.4: empty albums are format-valid (a freshly created curated
    # album waits for references). The workflow guard "really save empty?"
    # lives in the frontend, not the format validation.
    return True


def create_backup(album_json_path: Path) -> Path:
    """Timestamped copy of album.json under `.mpd_backups/`. Die neuesten
    `ALBUM_BACKUP_KEEP` bleiben, aeltere fliegen — begrenztes Wachstum auch
    bei viel Betrieb.

    Aufgeraeumt wird ueber `core.retention.prune_dir()`, dasselbe Muster wie
    fuer die Trails-Sicherungspunkte und spaeter das Zugriffsprotokoll (E1).
    Ohne Altersgrenze: Ein Album, das ein Jahr lang niemand angefasst hat,
    soll seine letzten Staende behalten, nicht verlieren."""
    import config
    from core import retention

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir  = album_json_path.parent / ".mpd_backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"album.json.backup_{timestamp}"
    shutil.copy2(album_json_path, backup_path)

    retention.prune_dir(backup_dir,
                        keep_newest=config.ALBUM_BACKUP_KEEP,
                        max_age_days=0,
                        pattern="album.json.backup_*")

    return backup_path


def _write_atomic(target: Path, data: dict):
    """Write JSON via tmp-file + os.replace. Either the new file is fully
    in place or the old file is untouched — no half-written album.json
    can be observed by a concurrent reader."""
    temp_fd, temp_path = tempfile.mkstemp(
        dir=target.parent,
        prefix=".album.json.tmp.",
        suffix=".json"
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, target)
    except Exception as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise e


async def _enrich_photo_meta(base: Path, album_name: str, elements: list) -> int:
    """
    Lazy migration: compute resolution + BlurHash for photo elements
    that don't have them yet. Mutates `elements` in place.

    Capped at _MIGRATION_BATCH so a large legacy album doesn't block
    for minutes on first open — the rest migrates across subsequent calls.

    Returns: number of freshly filled elements (0 = nothing to do).
    """
    todo = []
    for elem in elements:
        if elem.get("type") != "photo":
            continue
        if elem.get("resolution") and elem.get("blurhash"):
            continue
        filename = elem.get("file")
        if not filename:
            continue
        # PDX v1.4: with source_album the file lives in the source album folder.
        src_album = elem.get("source_album")
        if src_album and _is_safe_album_name(src_album):
            path = base / src_album / filename
        else:
            path = base / album_name / filename
        if not path.is_file():
            continue
        todo.append((elem, path))
        if len(todo) >= _MIGRATION_BATCH:
            break

    if not todo:
        return 0

    metas = await asyncio.gather(
        *(asyncio.to_thread(compute_photo_meta, p) for _, p in todo)
    )
    any_filled = False
    for (elem, _), meta in zip(todo, metas):
        if meta.get("resolution") and not elem.get("resolution"):
            elem["resolution"] = meta["resolution"]
            any_filled = True
        if meta.get("blurhash") and not elem.get("blurhash"):
            elem["blurhash"] = meta["blurhash"]
            any_filled = True
    return len(todo) if any_filled else 0


def _migrate_hidden_to_locked(album_data: dict) -> bool:
    """
    Move legacy nomenclature `style: "hidden"` and `meta.show_hidden`
    to the universal `locked: true` / `meta.show_locked`. Runs once
    on the first load after the update and persists on the next save.
    """
    changed = False
    meta = album_data.get("meta")
    if isinstance(meta, dict) and "show_hidden" in meta:
        meta["show_locked"] = meta.pop("show_hidden")
        changed = True
    for elem in album_data.get("elements", []) or []:
        if elem.get("style") == "hidden":
            elem["locked"] = True
            del elem["style"]
            changed = True
    return changed


# ---------------------------------------------------------------------------
# Internal loader helpers
# ---------------------------------------------------------------------------

def _load_one_album_sync(album_json_path: Path, folder: dict, folder_name: str,
                         space: str, read_only: bool):
    """Read one album.json from disk and assemble the listing entry.
    Blocking I/O — wrapped in `asyncio.to_thread` by the caller so a slow
    NAS doesn't stall the event loop. Falls back to a stub entry if the
    file is missing or unreadable, so a single broken folder cannot break
    the whole album list."""
    try:
        with open(album_json_path, "r", encoding="utf-8") as f:
            album_data = json.load(f)
        elements    = album_data.get("elements", [])
        photo_count = sum(1 for e in elements if e.get("type") == "photo")
        video_count = sum(1 for e in elements if e.get("type") == "video")
        return {
            "id"             : folder["id"],
            "name"           : folder_name,
            "path"           : folder["name"],
            "space"          : space,
            "read_only"      : read_only,
            "thumbnail"      : album_data["meta"].get("thumbnail", ""),
            "thumbnail_crop" : album_data["meta"].get("thumbnail_crop"),
            "year"           : album_data["meta"].get("year", 0),
            "title"          : album_data["meta"].get("title", folder_name),
            "description"    : album_data["meta"].get("description", ""),
            "photo_count"    : photo_count,
            "video_count"    : video_count,
        }
    except Exception as e:
        logger.warning(f"Album '{folder_name}' ({space}) skipped: {e}")
        return None


async def _load_albums_for_space(session, space: str) -> list:
    """
    Reads all album.json sequentially — for 39 albums this is <300ms
    locally, and we bypass the thread pool that the library warmer may
    be occupying.
    """
    api       = get_api(session, space)
    base      = get_path(session, space)
    read_only = is_readonly(session, space)

    folders = api.list_folders()
    albums = []
    for folder in folders:
        folder_name     = folder["name"].lstrip("/")
        album_json_path = base / folder_name / "album.json"
        if not album_json_path.exists():
            continue
        a = _load_one_album_sync(album_json_path, folder, folder_name, space, read_only)
        if a is not None:
            albums.append(a)
    return albums


async def _folders_without_json_for_space(session, space: str) -> list:
    api  = get_api(session, space)
    base = get_path(session, space)
    result = []

    folders = api.list_folders()
    for folder in folders:
        folder_name     = folder["name"].lstrip("/")

        # Skip system/metadata folders: Synology @eaDir, macOS .DS_Store, etc.
        if folder_name.startswith("@") or folder_name.startswith("."):
            continue

        album_json_path = base / folder_name / "album.json"

        if album_json_path.exists():
            continue

        if is_durchlauf(folder_name):
            continue  # Sammelordner, kein Album-Kandidat (s. is_durchlauf)

        album_dir = base / folder_name
        if not album_dir.is_dir():
            continue

        media_files = [f for f in album_dir.iterdir() if f.suffix.lower() in _MEDIA_EXTS]
        if not media_files:
            continue

        photo_files = sorted(
            [f for f in media_files if f.suffix.lower() in _PHOTO_EXTS],
            key=lambda f: f.name
        )
        first_photo = photo_files[0].name if photo_files else None

        result.append({
            "folder_name" : folder_name,
            "folder_path" : folder["name"],
            "space"       : space,
            "media_count" : len(media_files),
            "first_photo" : first_photo,
        })

    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_ALBUMS_CACHE: dict = {}  # key: (user, role) -> (timestamp, payload)
_ALBUMS_TTL_S = 30


def invalidate_albums_cache():
    """Call from the create_album/update_album/delete flow when anything changes."""
    _ALBUMS_CACHE.clear()


@router.get("/api/albums")
async def list_albums(request: Request):
    import time
    session = require_session(request)
    try:
        key = (session.user, session.role)
        now = time.monotonic()
        cached = _ALBUMS_CACHE.get(key)
        if cached and (now - cached[0]) < _ALBUMS_TTL_S:
            return cached[1]

        t0 = time.perf_counter()
        albums = []
        for space in available_spaces(session):
            albums += await _load_albums_for_space(session, space)
        albums.sort(key=lambda x: x["year"], reverse=True)
        payload = {"albums": albums, "role": session.role}
        _ALBUMS_CACHE[key] = (now, payload)
        logger.info("list_albums user=%s %dms %d albums (fresh, cached %ds)",
                    session.user, int((time.perf_counter() - t0) * 1000),
                    len(albums), _ALBUMS_TTL_S)
        return payload

    except Exception as e:
        raise HTTPException(500, _t("albums_be.load_albums_error", request, {"msg": str(e)}))


@router.get("/api/spaces-writable")
async def list_writable_spaces(request: Request):
    """Returns the spaces in which the current user is allowed to write.
    Frontend uses this for the space selector when creating empty mixed albums."""
    session = require_session(request)
    spaces = [s for s in available_spaces(session) if not is_readonly(session, s)]
    return {"spaces": spaces}


@router.get("/api/albums/without-json")
async def list_albums_without_json(request: Request):
    session = require_session(request)
    try:
        result = []
        for space in available_spaces(session):
            if not is_readonly(session, space):
                result += await _folders_without_json_for_space(session, space)
        return {"folders": result, "count": len(result)}
    except Exception as e:
        logger.error(f"without-json error: {e}", exc_info=True)
        raise HTTPException(500, _t("common.error_generic", request, {"msg": str(e)}))


@router.get("/api/album/files")
async def get_album_files(
    request: Request,
    space : str = Query(...),
    folder: str = Query(...)
):
    session = require_session(request)
    try:
        api     = get_api(session, space)
        folders = api.list_folders()
        folder_id = next(
            (f["id"] for f in folders if f["name"] in (folder, f"/{folder}")),
            None
        )
        if folder_id is None:
            raise HTTPException(404, _t("albums_be.folder_not_in_synology", request, {"folder": folder}))

        photos = api.list_photos(folder_id)
        files  = []
        for p in sorted(photos, key=lambda x: (x.get("time", 0), x["filename"])):
            folder_enc   = quote(folder)
            filename_enc = quote(p["filename"])
            files.append({
                "filename" : p["filename"],
                "time"     : p.get("time", 0),
                "thumbnail": f"/api/thumbnail/{space}/{folder_enc}/{filename_enc}?size=sm",
            })

        return {"folder": folder, "space": space, "files": files, "count": len(files)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"album/files error '{folder}': {e}", exc_info=True)
        raise HTTPException(500, _t("common.error_generic", request, {"msg": str(e)}))


@router.get("/api/album/{space}/{album_name}")
async def get_album(request: Request, space: str, album_name: str):
    session = require_session(request)
    try:
        base            = get_path(session, space)
        album_dir       = base / album_name
        album_json_path = album_dir / "album.json"

        if not album_json_path.exists():
            raise HTTPException(404, _t("album.not_found", request))

        with open(album_json_path, "r", encoding="utf-8") as f:
            album_data = json.load(f)

        # Lazy migration: compute missing resolution/BlurHash + persist
        migrated       = await _enrich_photo_meta(base, album_name, album_data.get("elements", []))
        locked_renamed = _migrate_hidden_to_locked(album_data)
        if migrated or locked_renamed:
            _write_atomic(album_json_path, album_data)
            if migrated:
                logger.info(
                    "Album '%s' (%s): %d photo meta entries filled in",
                    album_name, space, migrated,
                )
            if locked_renamed:
                logger.info(
                    "Album '%s' (%s): hidden→locked nomenclature migrated",
                    album_name, space,
                )

        elements_enriched = []
        for elem in album_data.get("elements", []):
            elem_copy = elem.copy()

            if elem.get("type") in ("photo", "video", "document") and "file" in elem:
                filename     = elem["file"]
                filename_enc = quote(filename)

                # PDX v1.4: source_album resolves the file from another
                # album folder in the same space. Defensive filtering so
                # a manipulated field can't escape the space.
                src_album = elem.get("source_album")
                if src_album and _is_safe_album_name(src_album):
                    resolved_album = src_album
                    file_path      = base / src_album / filename
                else:
                    resolved_album = album_name
                    file_path      = album_dir / filename
                resolved_enc = quote(resolved_album)
                is_pdf       = filename.lower().endswith(".pdf")

                if not file_path.is_file():
                    elem_copy["error"] = f"'{filename}' nicht gefunden"
                    if src_album:
                        elem_copy["source_missing"] = True
                elif elem.get("type") == "document" and is_pdf:
                    elem_copy["document_url"] = f"/api/document/{space}/{resolved_enc}/{filename_enc}"
                else:
                    # sm/m/xl fürs Web, full (4096 px, additiv 09/2026) für
                    # die native App — Schema zentral in deps.media_urls().
                    # v = Kurz-Cache-Schlüssel → immutable-Caching beim Client.
                    elem_copy["thumbnails"], elem_copy["original"] = media_urls(
                        space, resolved_album, filename,
                        version=etag_for(file_path)[:16],
                    )
                    # Darf MPD diese Datei ueberschreiben? Nur die
                    # FORMAT-Haelfte der Antwort — die Rechte-Haelfte
                    # steht in `read_only` unten und in `rights`
                    # (/api/whoami). Der Stift braucht beides.
                    elem_copy["editable_format"] = is_editable(filename)

            elements_enriched.append(elem_copy)

        # Was im Ordner liegt, aber in keinem Element steht — der dezente
        # Hinweis im Album (08.09.2026). Ein iterdir je Aufruf; die Datei-
        # liste selbst holt der Einfuege-Dialog bei Bedarf.
        from core.media_types import unassigned_in
        unassigned_count = len(unassigned_in(album_dir, album_data))

        return {
            "meta"            : album_data["meta"],
            "elements"        : elements_enriched,
            "space"           : space,
            "read_only"       : is_readonly(session, space),
            "unassigned_count": unassigned_count,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_album error '%s/%s'", space, album_name)
        raise HTTPException(500, _t("albums_be.load_album_error", request, {"msg": str(e)}))


@router.post("/api/album/create")
async def create_album(request: Request, body: CreateAlbumRequest):
    session     = require_session(request)
    space       = body.space
    folder_name = body.folder_name
    thumbnail   = body.thumbnail

    if is_readonly(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))
    if is_durchlauf(folder_name):
        raise HTTPException(400, _t("album.durchlauf_no_album", request, {"folder": folder_name}))

    try:
        api  = get_api(session, space)
        base = get_path(session, space)
        album_json_path = _safe_path(base, folder_name, "album.json")

        if album_json_path.exists():
            raise HTTPException(409, _t("album.json_already_exists", request, {"folder": folder_name}))

        folders   = api.list_folders()
        folder_id = next(
            (f["id"] for f in folders if f["name"] in (folder_name, f"/{folder_name}")),
            None
        )
        if folder_id is None:
            raise HTTPException(404, _t("album.folder_not_in_synology", request, {"folder": folder_name}))

        syno_photos = api.list_items(folder_id)
        syno_map    = {p["filename"]: p for p in syno_photos}

        album_dir = _safe_path(base, folder_name)
        all_media = [f for f in album_dir.iterdir() if f.suffix.lower() in _MEDIA_EXTS]

        def _sort_key(f):
            syno = syno_map.get(f.name)
            if syno and syno.get("time"):
                return (0, syno["time"], f.name)
            return (1, 0, f.name)

        all_media.sort(key=_sort_key)

        thumbnail_path = album_dir / thumbnail
        if not thumbnail_path.exists():
            raise HTTPException(400, _t("album.thumbnail_not_found", request, {"file": thumbnail}))

        elements = []
        for i, f in enumerate(all_media, start=1):
            elem_type = "photo" if f.suffix.lower() in _PHOTO_EXTS else "video"
            elements.append({
                "id"  : str(i).zfill(4),
                "type": elem_type,
                "file": f.name,
            })

        # Initial meta for the first _MIGRATION_BATCH photos. The rest
        # migrates on first album open. Keeps create latency in check.
        await _enrich_photo_meta(base, folder_name, elements)

        now   = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        album = {
            "version": "1.4",
            "meta": {
                "title"      : folder_name,
                "description": "",
                "year"       : _extract_year_from_name(folder_name),
                "thumbnail"  : thumbnail,
                "created"    : now,
                "modified"   : now,
                "show_locked": False,
            },
            "elements": elements,
        }

        _write_atomic(album_json_path, album)
        events.emit("album_changed")
        invalidate_albums_cache()
        invalidate_timeline_index()
        logger.info(f"Album created: {space}/{folder_name} ({len(elements)} elements)")

        return {
            "success"    : True,
            "folder_name": folder_name,
            "space"      : space,
            "elements"   : len(elements),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create error '{folder_name}': {e}", exc_info=True)
        raise HTTPException(500, _t("common.internal_error", request, {"msg": str(e)}))


@router.post("/api/album/create-empty")
async def create_empty_album(request: Request, body: CreateEmptyAlbumRequest):
    """
    PDX v1.4: creates a new, empty album folder + minimal album.json.
    Starting point for curated mixed albums — references are added
    afterwards in the editor.
    """
    session     = require_session(request)
    space       = body.space
    folder_name = body.folder_name.strip()

    if not folder_name:
        raise HTTPException(400, _t("album.fields_required", request))
    if not _is_safe_album_name(folder_name):
        raise HTTPException(400, _t("album.invalid_folder_name", request))
    if is_durchlauf(folder_name):
        raise HTTPException(400, _t("album.durchlauf_no_album", request, {"folder": folder_name}))
    if is_readonly(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))

    base      = get_path(session, space)
    album_dir = _safe_path(base, folder_name)
    if album_dir.exists():
        raise HTTPException(409, _t("album.folder_already_exists", request, {"folder": folder_name}))

    try:
        album_dir.mkdir(parents=True, exist_ok=False)
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        album = {
            "version": "1.4",
            "meta": {
                "title"      : folder_name,
                "description": "",
                "year"       : _extract_year_from_name(folder_name),
                "thumbnail"  : "",
                "created"    : now,
                "modified"   : now,
                "show_locked": False,
            },
            "elements": [],
        }
        _write_atomic(album_dir / "album.json", album)
        invalidate_albums_cache()
        events.emit("album_changed")
        invalidate_timeline_index()
        logger.info(f"Empty album created: {space}/{folder_name}")

        return {"success": True, "folder_name": folder_name, "space": space}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create-empty error '{folder_name}': {e}", exc_info=True)
        raise HTTPException(500, _t("common.internal_error", request, {"msg": str(e)}))


@router.post("/api/album/{space}/{album_name}/cover-from-source")
async def cover_from_source(request: Request, space: str, album_name: str, body: CoverFromSourceRequest):
    """
    PDX v1.4: physically copies a referenced photo from a source album
    as cover.<ext> into the target album folder. Called when the user
    sets a source_album photo as cover in a mixed album — spec-compliant:
    the cover stays as a local file in the own folder.
    """
    session = require_session(request)
    if is_readonly(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))

    src_album = body.source_album.strip()
    file_name = body.file.strip()
    if not src_album or not file_name:
        raise HTTPException(400, _t("album.source_required", request))
    if not _is_safe_album_name(src_album):
        raise HTTPException(400, _t("album.source_invalid_path", request))
    if "/" in file_name or "\\" in file_name or ".." in file_name:
        raise HTTPException(400, _t("album.file_invalid_path", request))

    base       = get_path(session, space)
    src_path   = base / src_album / file_name
    target_dir = base / album_name
    if not src_path.is_file():
        raise HTTPException(404, _t("album.source_file_not_found", request, {"file": file_name}))
    if not target_dir.is_dir():
        raise HTTPException(404, _t("album.target_not_found", request, {"name": album_name}))

    ext = Path(file_name).suffix.lower() or ".jpg"
    target_name = f"cover{ext}"
    target_path = target_dir / target_name
    try:
        shutil.copy2(src_path, target_path)
        invalidate_albums_cache()
        logger.info(f"Cover copied: {space}/{src_album}/{file_name} -> {space}/{album_name}/{target_name}")
        return {"success": True, "thumbnail": target_name}
    except Exception as e:
        logger.error(f"cover-from-source error: {e}", exc_info=True)
        raise HTTPException(500, _t("common.internal_error", request, {"msg": str(e)}))


@router.post("/api/album/{space}/{album_name}/update")
async def update_album(request: Request, space: str, album_name: str, data: dict):
    """
    Body stays a dynamic dict on purpose — the album shape follows the
    PDX spec (own version axis), validation lives in validate_album_data
    + the ALLOWED whitelist below. A Pydantic model here would duplicate
    the spec in code.
    """
    session = require_session(request)
    if is_readonly(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))
    try:
        album_json_path = get_path(session, space) / album_name / "album.json"
        if not album_json_path.exists():
            raise HTTPException(404, _t("album.json_not_found", request, {"name": album_name}))

        # Opt-in-Konfliktschutz (09/2026, native App): Schickt der Client
        # den Stand mit, auf dem sein Edit basiert, lehnen wir ab, wenn
        # inzwischen jemand anderes gespeichert hat — statt Last-Write-Wins.
        # Ohne Header verhält sich update exakt wie bisher (Web unberührt).
        base_modified = request.headers.get("x-mpd-base-modified")
        if base_modified:
            try:
                with open(album_json_path, encoding="utf-8") as f:
                    current_modified = json.load(f).get("meta", {}).get("modified")
            except Exception:
                current_modified = None
            if current_modified and current_modified != base_modified.strip():
                raise HTTPException(
                    409,
                    _t("album.conflict_modified", request,
                       {"current": current_modified}),
                )

        # Die Version folgt dem Inhalt: Link-Elemente gibt es erst ab v1.4.1,
        # heading-Stil und quote-source erst ab v1.5 — die Spec verlangt,
        # dass ein Album seine höchste genutzte Fähigkeit ausweist.
        # Vorher stand hier fest "1.4", was jedes gespeicherte Album auf den
        # alten Stand zurueckgesetzt haette.
        elements_in = data.get("elements") or []
        has_link = any(e.get("type") == "link" for e in elements_in)
        has_v15 = any(
            e.get("type") == "text"
            and (e.get("style") == "heading" or e.get("source"))
            for e in elements_in
        )
        if has_v15:
            data["version"] = "1.5"
        else:
            data["version"] = "1.4.1" if has_link else "1.4"

        ALLOWED = {
            # "source": PDX v1.5, Quellenangabe an quote-Texten.
            "text"     : {"id", "type", "text", "style", "source", "locked"},
            "photo"    : {"id", "type", "file", "resolution", "blurhash", "source_album", "locked"},
            "video"    : {"id", "type", "file", "source_album", "locked"},
            # "style": sep-style-btn im Web-Editor (line/bold/dashed/dots/
            # space) — fehlte hier und wurde bei jedem Save verworfen.
            "separator": {"id", "type", "label", "style", "locked"},
            "document" : {"id", "type", "file", "title", "kind", "source_album", "locked"},
            "audio"    : {"id", "type", "file", "title", "source_album", "locked"},
            # "layer": Kartengrundlage osm/topo (album-render.js) — der
            # Feldname weicht bewusst vom "map_layer" des tour-Elements ab,
            # so schreibt es das Frontend seit jeher.
            "map"      : {"id", "type", "title", "pins", "layer", "locked"},
            "tour"     : {"id", "type", "file", "title", "show_elevation", "map_layer", "source_album", "locked"},
            # PDX v1.4.1. Fehlt ein Typ hier, behaelt der Zweig unten nur
            # {"id", "type"} — die uebrigen Felder werden stillschweigend
            # verworfen, und validate_album_data meldet danach voellig zu
            # Recht "'url' fehlt". Genau das ist beim Link-Element passiert.
            "link"     : {"id", "type", "url", "title", "note", "locked"},
        }

        existing_ids = [
            int(e["id"]) for e in data.get("elements", [])
            if e.get("id") and str(e["id"]).isdigit()
        ]
        next_id = max(existing_ids, default=0) + 1

        clean_elements = []
        for elem in data.get("elements", []):
            elem_type    = elem.get("type")
            allowed_keys = ALLOWED.get(elem_type)
            if allowed_keys:
                clean = {k: v for k, v in elem.items() if k in allowed_keys}
                if clean.get("style") == "default":
                    clean.pop("style")
            else:
                clean = {k: v for k, v in elem.items() if k in {"id", "type"}}

            if not clean.get("id"):
                clean["id"] = str(next_id).zfill(4)
                next_id += 1

            clean_elements.append(clean)

        data["elements"] = clean_elements
        validate_album_data(data, request)
        data["meta"]["modified"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        backup_path = create_backup(album_json_path)
        _write_atomic(album_json_path, data)

        events.emit("album_changed")
        invalidate_albums_cache()
        invalidate_timeline_index()
        logger.info(f"Album updated: {space}/{album_name} ({len(data['elements'])} elements)")

        return {
            "success"       : True,
            "message"       : f"Album '{album_name}' gespeichert",
            "backup"        : backup_path.name,
            "elements_count": len(data["elements"]),
            "timestamp"     : data["meta"]["modified"],
            # Additiv 09/2026: neuer Stand für den Konfliktschutz-Header
            # X-MPD-Base-Modified des nächsten Speicherns.
            "modified"      : data["meta"]["modified"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update error '{space}/{album_name}': {e}", exc_info=True)
        raise HTTPException(500, _t("common.internal_error", request, {"msg": str(e)}))


@router.delete("/api/album/{space}/{album_name}")
async def delete_album(request: Request, space: str, album_name: str):
    """Deletes ONLY album.json (overlay model — the photo folder stays untouched).
    Backup is created before deletion; manual recovery by renaming the backup
    back to album.json is possible."""
    session = require_session(request)
    if is_readonly(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))
    if not _is_safe_album_name(album_name):
        raise HTTPException(400, _t("album.invalid_name", request))
    try:
        album_dir       = _safe_path(get_path(session, space), album_name)
        album_json_path = album_dir / "album.json"
        if not album_json_path.exists():
            raise HTTPException(404, _t("album.json_not_found", request, {"name": album_name}))

        backup_path = create_backup(album_json_path)
        album_json_path.unlink()

        events.emit("album_changed")
        invalidate_albums_cache()
        invalidate_timeline_index()
        logger.info(f"Album deleted: {space}/{album_name} (backup: {backup_path.name})")

        return {
            "success": True,
            "message": f"Album '{album_name}' geloescht",
            "backup" : backup_path.name,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete error '{space}/{album_name}': {e}", exc_info=True)
        raise HTTPException(500, _t("common.internal_error", request, {"msg": str(e)}))


@router.post("/api/album/{space}/{album_name}/undo-delete")
async def undo_delete(request: Request, space: str, album_name: str, backup_filename: str):
    """Recover a JUST-DELETED album from the backup created right before
    deletion. /restore does not work for this case because it requires an
    existing album.json."""
    session = require_session(request)
    if is_readonly(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))
    if not _is_safe_album_name(album_name):
        raise HTTPException(400, _t("album.invalid_name", request))
    if "/" in backup_filename or "\\" in backup_filename or ".." in backup_filename:
        raise HTTPException(400, _t("album.invalid_backup_name", request))
    if not backup_filename.startswith("album.json.backup_"):
        raise HTTPException(400, _t("album.backup_name_mismatch", request))
    try:
        album_path  = _safe_path(get_path(session, space), album_name)
        backup_path = album_path / ".mpd_backups" / backup_filename
        album_json  = album_path / "album.json"

        if not backup_path.exists():
            raise HTTPException(404, _t("album.backup_not_found", request, {"file": backup_filename}))
        if album_json.exists():
            raise HTTPException(409, _t("album.exists_no_undo_needed", request))

        shutil.copy2(backup_path, album_json)
        events.emit("album_changed")
        invalidate_albums_cache()
        invalidate_timeline_index()
        logger.info(f"Album restored (undo-delete): {space}/{album_name} from {backup_filename}")

        return {
            "success": True,
            "message": f"Album '{album_name}' wiederhergestellt",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Undo-delete error '{space}/{album_name}': {e}", exc_info=True)
        raise HTTPException(500, _t("common.internal_error", request, {"msg": str(e)}))


@router.get("/api/album/{space}/{album_name}/backups")
async def list_backups(request: Request, space: str, album_name: str):
    session = require_session(request)
    try:
        album_path = get_path(session, space) / album_name
        if not album_path.exists():
            raise HTTPException(404, _t("album.not_found", request))

        backups = sorted((album_path / ".mpd_backups").glob("album.json.backup_*"), reverse=True)
        return {
            "album"  : album_name,
            "space"  : space,
            "backups": [
                {
                    "filename": b.name,
                    "size"    : b.stat().st_size,
                    "created" : datetime.fromtimestamp(b.stat().st_mtime).isoformat(),
                }
                for b in backups
            ],
            "count": len(backups),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("common.error_generic", request, {"msg": str(e)}))


@router.post("/api/album/{space}/{album_name}/restore")
async def restore_backup(request: Request, space: str, album_name: str, backup_filename: str):
    session = require_session(request)
    if is_readonly(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))
    try:
        # Sicherheits-Audit 02.09.2026 (H1): backup_filename streng auf das
        # von create_backup() erzeugte Muster prüfen und den aufgelösten
        # Pfad im Backup-Ordner verankern (wie in undo_delete).
        if not re.fullmatch(r"album\.json\.backup_\d{8}_\d{6}", backup_filename):
            raise HTTPException(400, _t("common.invalid_path", request))

        album_path  = _safe_path(get_path(session, space), album_name)
        backup_dir  = album_path / ".mpd_backups"
        backup_path = _safe_path(backup_dir, backup_filename)
        if not backup_path.resolve().is_relative_to(backup_dir.resolve()):
            raise HTTPException(400, _t("common.invalid_path", request))
        album_json  = album_path / "album.json"

        if not backup_path.exists():
            raise HTTPException(404, _t("album.backup_not_found", request, {"file": backup_filename}))

        current_backup = create_backup(album_json)
        shutil.copy2(backup_path, album_json)
        events.emit("album_changed")
        invalidate_timeline_index()
        invalidate_albums_cache()
        logger.info(f"Backup restored: {backup_filename}")

        return {
            "success"       : True,
            "message"       : f"'{backup_filename}' wiederhergestellt",
            "current_backup": current_backup.name,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("common.error_generic", request, {"msg": str(e)}))
