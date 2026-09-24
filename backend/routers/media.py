"""
routers/media.py
My Photo Diary v2 – thumbnail, EXIF and media endpoints (session-based)
"""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, FileResponse
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

from core.http_range import ranged_file_response
from core.i18n import t as _t
from core.thumb_pipeline import etag_for, get_thumb, get_thumb_path, get_video_thumb_path
from routers.deps import (require_session, get_path, resolve_media_path,
                          _PHOTO_EXTS, _VIDEO_EXTS,
                          _SIZE_ALIAS, _DOC_MIME, _PHOTO_MIME,
                          _AUDIO_MIME, _VIDEO_MIME)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Thumbnail — v2 thumb_pipeline (WebP, cache outside the photo trees)
# ---------------------------------------------------------------------------

def _etag_headers(source: Path) -> tuple:
    """(etag, Header-Dict) für Antworten mit mtime-basiertem Validator.

    Cache-Control "no-cache" heißt: speichern ja, aber vor jeder Nutzung
    per If-None-Match revalidieren — die Antwort ist dann ein 304 ohne
    Body (Kosten serverseitig: ein stat()). Ersetzt das blinde
    max-age=86400, mit dem jeder Client Edits bis zu 24 h verpasste
    (offener Punkt aus CLAUDE.md, aufgeschlagen am 01.09.2026 beim
    Foto-Edit aus der nativen App)."""
    etag = f'"{etag_for(source)}"'
    return etag, {"ETag": etag, "Cache-Control": "no-cache"}


def _etag_matches(request: Request, etag: str) -> bool:
    """If-None-Match auswerten (Liste, W/-Präfix, '*')."""
    inm = request.headers.get("if-none-match")
    if not inm:
        return False
    if inm.strip() == "*":
        return True
    tags = [t.strip().removeprefix("W/") for t in inm.split(",")]
    return etag in tags


@router.get("/api/thumbnail/{space}/{album_name}/{filename}")
async def get_thumbnail(
    request   : Request,
    space     : str,
    album_name: str,
    filename  : str,
    size      : str = Query("m", pattern="^(sm|m|xl|full|thumb|cover|preview)$"),
    v         : str = Query(None, max_length=40, pattern="^[0-9a-f]*$"),
):
    session = require_session(request)
    try:
        pipeline_size = _SIZE_ALIAS.get(size, size)
        base          = get_path(session, space).resolve()
        source        = (base / album_name / filename).resolve()

        if not source.is_relative_to(base):
            raise HTTPException(400, _t("common.invalid_path", request))
        if not source.is_file():
            raise HTTPException(404, _t("media.file_not_found", request, {"filename": filename}))
        if source.suffix.lower() not in _PHOTO_EXTS:
            # Videos use /api/video-thumb/, other types have no thumb
            raise HTTPException(415, _t("media.no_photo_thumb", request, {"ext": source.suffix}))

        # ETag = Thumb-Cache-Schlüssel (sha1(Pfad+mtime_ns)) — Edits ändern
        # mtime, Clients revalidieren per If-None-Match und bekommen 304.
        etag, cache_headers = _etag_headers(source)
        # ?v= (Performance-Paket 03.09.2026, Google-Muster): Stimmt die
        # mitgelieferte Kurzversion mit dem aktuellen Schlüssel überein,
        # ist die URL inhaltsadressiert → ein Jahr immutable, null
        # Revalidierungs-Roundtrips. Veraltetes v fällt defensiv auf
        # no-cache zurück (kein Cache-Poisoning alter URLs).
        if v and etag.strip('"').startswith(v):
            cache_headers = {"ETag": etag,
                             "Cache-Control": "public, max-age=31536000, immutable"}
        if _etag_matches(request, etag):
            return Response(status_code=304, headers=cache_headers)

        cache_file = get_thumb_path(source, pipeline_size)
        if not cache_file.is_file():
            # Pillow work in the thread pool — would otherwise block the event loop
            await asyncio.to_thread(get_thumb, source, pipeline_size)

        return FileResponse(
            cache_file,
            media_type = "image/webp",
            headers    = cache_headers,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Thumbnail error for %s/%s/%s", space, album_name, filename)
        raise HTTPException(500, _t("media.thumb_error", request, {"msg": str(e)}))


# ---------------------------------------------------------------------------
# Original-file download (lightbox kebab "Herunterladen")
# ---------------------------------------------------------------------------

@router.get("/api/photo/{space}/{album_name}/{filename}/download")
async def download_photo(request: Request, space: str, album_name: str, filename: str):
    """
    Stream the unmodified source file with Content-Disposition: attachment.
    Demo accounts are blocked — they may view the XL thumbnail in the
    lightbox, but originals must not leave the host.
    """
    session = require_session(request)
    if session.is_demo:
        raise HTTPException(403, _t("media.demo_no_download", request))

    base   = get_path(session, space).resolve()
    source = (base / album_name / filename).resolve()
    if not source.is_relative_to(base):
        raise HTTPException(400, _t("common.invalid_path", request))
    if not source.is_file():
        raise HTTPException(404, _t("media.file_not_found", request, {"filename": filename}))

    # ETag auf derselben mtime-Basis wie die Thumbnails (If-None-Match
    # gewinnt laut RFC 9110 vor Range — 304 kommt also auch bei
    # Range-Requests, wenn der Validator passt).
    etag, cache_headers = _etag_headers(source)
    if _etag_matches(request, etag):
        return Response(status_code=304, headers=cache_headers)

    # MIME-Typ ausdruecklich mitgeben statt raten lassen (07.09.2026):
    # ohne `media_type` faellt Starlette auf `mimetypes.guess_type`
    # zurueck, und was die eingebaute Tabelle kennt, haengt an der
    # Python-Fassung des Images und daran, ob eine /etc/mime.types
    # mitkommt. Bei „nicht erraten" liefert Starlette `text/plain` —
    # fuer ein HEIC waere das schlechter als jede falsche Bildangabe.
    media_type = _PHOTO_MIME.get(source.suffix.lower()) \
        or _VIDEO_MIME.get(source.suffix.lower()) \
        or "application/octet-stream"

    # ranged_file_response: ohne Range-Header identisch zu FileResponse,
    # mit Range-Header 206-Teilantwort (native Player, Download-Resume)
    return ranged_file_response(
        request,
        source,
        media_type = media_type,
        filename   = source.name,
        headers    = cache_headers,
    )


# ---------------------------------------------------------------------------
# EXIF
# ---------------------------------------------------------------------------

@router.get("/api/photo/{space}/{album_name}/{filename}/exif")
async def get_exif(request: Request, space: str, album_name: str, filename: str):
    session = require_session(request)
    try:
        image_path = resolve_media_path(session, space, album_name, filename, request)
        if not image_path.exists():
            raise HTTPException(404, _t("media.image_not_found", request, {"filename": filename}))
        return extract_exif(image_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("media.exif_error", request, {"msg": str(e)}))


# ---------------------------------------------------------------------------
# Video stream
# ---------------------------------------------------------------------------

@router.get("/api/video/{space}/{album_name}/{filename}")
async def get_video(request: Request, space: str, album_name: str, filename: str):
    session = require_session(request)
    try:
        video_path = resolve_media_path(session, space, album_name, filename, request)
        if not video_path.exists():
            raise HTTPException(404, _t("media.video_not_found", request, {"filename": filename}))
        if video_path.suffix.lower() not in _VIDEO_EXTS:
            raise HTTPException(400, _t("media.not_a_video", request, {"filename": filename}))
        # Rueckfall octet-stream statt video/mp4: eine .wmv als MP4 zu
        # etikettieren ist derselbe Fehler wie ein HEIC als JPEG.
        media_type = _VIDEO_MIME.get(video_path.suffix.lower(),
                                     "application/octet-stream")
        # ranged_file_response: ohne Range-Header identisch zu FileResponse,
        # mit Range-Header 206-Teilantwort (ExoPlayer-Seeking)
        return ranged_file_response(
            request,
            video_path,
            media_type = media_type,
            headers    = {"Accept-Ranges": "bytes"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("media.video_error", request, {"msg": str(e)}))


# ---------------------------------------------------------------------------
# FFmpeg video thumbnail (fallback for ame_defect)
# ---------------------------------------------------------------------------

@router.get("/api/video-thumb/{space}/{album_name}/{filename}")
async def get_video_thumb(request: Request, space: str, album_name: str, filename: str):
    session = require_session(request)
    try:
        video_path = resolve_media_path(session, space, album_name, filename, request)
        if not video_path.exists():
            raise HTTPException(404, _t("media.video_not_found", request, {"filename": filename}))

        thumb_path = get_video_thumb_path(video_path)

        if not thumb_path.exists():
            thumb_path.parent.mkdir(parents=True, exist_ok=True)
            import subprocess
            result = subprocess.run([
                "ffmpeg", "-y",
                "-ss", "00:00:01",
                "-i", str(video_path),
                "-vframes", "1",
                "-q:v", "4",
                str(thumb_path)
            ], capture_output=True, timeout=30)
            if result.returncode != 0 or not thumb_path.exists():
                raise HTTPException(500, _t("media.ffmpeg_error", request, {"msg": result.stderr.decode()[:200]}))

        return Response(
            content    = thumb_path.read_bytes(),
            media_type = "image/jpeg",
            headers    = {"Cache-Control": "public, max-age=86400"},
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("media.video_thumb_error", request, {"msg": str(e)}))


# ---------------------------------------------------------------------------
# Document stream (PDF)
# ---------------------------------------------------------------------------

@router.get("/api/document/{space}/{album_name}/{filename}")
async def get_document(request: Request, space: str, album_name: str, filename: str):
    session = require_session(request)
    try:
        doc_path = resolve_media_path(session, space, album_name, filename, request)
        if not doc_path.exists():
            raise HTTPException(404, _t("media.doc_not_found", request, {"filename": filename}))
        # N3 (Audit): nur echte Dokument-/Bildtypen ausliefern — vorher kam
        # JEDE Datei des Ordners raus (album.json, .bak, EXIF-Cache …).
        if doc_path.suffix.lower() not in _DOC_MIME:
            raise HTTPException(415, _t("media.doc_not_found", request, {"filename": filename}))
        media_type = _DOC_MIME[doc_path.suffix.lower()]
        return FileResponse(
            path       = str(doc_path),
            media_type = media_type,
            headers    = {"Content-Disposition": f"inline; filename=\"{filename}\""},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("media.doc_error", request, {"msg": str(e)}))


# ---------------------------------------------------------------------------
# Audio stream
# ---------------------------------------------------------------------------

@router.get("/api/audio/{space}/{album_name}/{filename}")
async def get_audio(request: Request, space: str, album_name: str, filename: str):
    session = require_session(request)
    try:
        audio_path = resolve_media_path(session, space, album_name, filename, request)
        if not audio_path.exists():
            raise HTTPException(404, _t("media.audio_not_found", request, {"filename": filename}))
        media_type = _AUDIO_MIME.get(audio_path.suffix.lower(), "audio/mpeg")
        # ranged_file_response: ohne Range-Header identisch zu FileResponse,
        # mit Range-Header 206-Teilantwort (MediaPlayer-Seeking)
        return ranged_file_response(
            request,
            audio_path,
            media_type = media_type,
            headers    = {"Accept-Ranges": "bytes"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("media.audio_error", request, {"msg": str(e)}))

# ---------------------------------------------------------------------------
# GPX file (tour element)
# ---------------------------------------------------------------------------

@router.get("/api/gpx/{space}/{album_name}/{filename}")
async def get_gpx(request: Request, space: str, album_name: str, filename: str):
    session = require_session(request)
    try:
        gpx_path = resolve_media_path(session, space, album_name, filename, request)
        if not gpx_path.exists():
            raise HTTPException(404, _t("media.gpx_not_found", request, {"filename": filename}))
        if gpx_path.suffix.lower() != ".gpx":
            raise HTTPException(400, _t("media.not_a_gpx", request, {"filename": filename}))
        return FileResponse(
            path       = str(gpx_path),
            media_type = "application/gpx+xml",
            headers    = {"Cache-Control": "public, max-age=3600"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("media.gpx_error", request, {"msg": str(e)}))


@router.get("/api/gpx-files/{space}/{album_name}")
async def list_gpx_files(request: Request, space: str, album_name: str):
    session = require_session(request)
    try:
        album_dir = resolve_media_path(session, space, album_name, request=request)
        if not album_dir.exists():
            raise HTTPException(404, _t("media.album_not_found", request, {"album": album_name}))
        files = sorted(
            f.name for f in album_dir.iterdir()
            if f.suffix.lower() == ".gpx" and f.is_file()
        )
        return {"files": files}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("media.gpx_list_error", request, {"msg": str(e)}))


def extract_exif(image_path: Path) -> dict:
    try:
        img      = Image.open(image_path)
        exif_raw = img.getexif()
        if not exif_raw:
            return {"error": "Keine EXIF-Daten"}

        exif = {TAGS.get(tag, tag): value for tag, value in exif_raw.items()}
        # DateTimeOriginal, LensModel, settings etc. live in the Exif sub-IFD (0x8769),
        # not the 0th IFD. Without this merge the date logic falls back to
        # 'DateTime' (modification time) — see the Google Takeout case.
        try:
            exif_sub = exif_raw.get_ifd(0x8769)
            for tag, value in exif_sub.items():
                name = TAGS.get(tag, tag)
                exif.setdefault(name, value)
        except Exception as e:
            logger.debug("Exif sub-IFD not readable (%s): %s", image_path, e)
        result = {}

        if "DateTimeOriginal" in exif:
            result["datetime"] = str(exif["DateTimeOriginal"])
        elif "DateTime" in exif:
            result["datetime"] = str(exif["DateTime"])

        camera = {}
        if "Make"  in exif: camera["make"]  = str(exif["Make"]).strip()
        if "Model" in exif: camera["model"] = str(exif["Model"]).strip()
        if camera:
            result["camera"] = camera

        if "LensModel" in exif:
            result["lens"] = str(exif["LensModel"]).strip()

        settings = {}

        def rational_to_float(val):
            if hasattr(val, "numerator"):
                return val.numerator / val.denominator if val.denominator else float(val.numerator)
            if isinstance(val, tuple):
                return val[0] / val[1] if val[1] else float(val[0])
            return float(val)

        if "FocalLength" in exif:
            try: settings["focal_length"] = f"{int(rational_to_float(exif['FocalLength']))}mm"
            except Exception: pass

        if "FNumber" in exif:
            try: settings["aperture"] = f"f/{rational_to_float(exif['FNumber']):.1f}"
            except Exception: pass

        if "ExposureTime" in exif:
            try:
                exp = exif["ExposureTime"]
                v   = rational_to_float(exp)
                if hasattr(exp, "numerator") and exp.numerator == 1:
                    settings["shutter_speed"] = f"1/{exp.denominator}"
                elif isinstance(exp, tuple) and exp[0] == 1:
                    settings["shutter_speed"] = f"1/{exp[1]}"
                else:
                    settings["shutter_speed"] = f"{v:.2f}s"
            except Exception: pass

        if "ISOSpeedRatings" in exif:
            try: settings["iso"] = int(exif["ISOSpeedRatings"])
            except Exception: pass

        if settings:
            result["settings"] = settings

        _GPS_IFD_TAG = 0x8825
        gps_raw = exif_raw.get_ifd(_GPS_IFD_TAG)
        if gps_raw:
            gps_named = {GPSTAGS.get(t, t): v for t, v in gps_raw.items()}
            gps = {}

            def dms_to_decimal(dms, ref):
                d, m, s = [rational_to_float(x) for x in dms]
                val = d + m / 60 + s / 3600
                return -val if ref in ("S", "W") else val

            if "GPSLatitude" in gps_named and "GPSLatitudeRef" in gps_named:
                gps["lat"] = round(dms_to_decimal(gps_named["GPSLatitude"], gps_named["GPSLatitudeRef"]), 6)
            if "GPSLongitude" in gps_named and "GPSLongitudeRef" in gps_named:
                gps["lon"] = round(dms_to_decimal(gps_named["GPSLongitude"], gps_named["GPSLongitudeRef"]), 6)
            if "GPSAltitude" in gps_named:
                try: gps["altitude"] = int(rational_to_float(gps_named["GPSAltitude"]))
                except Exception: pass

            if gps:
                result["gps"] = gps

        raw_desc = exif.get("ImageDescription", "")
        if raw_desc:
            if isinstance(raw_desc, bytes):
                try:
                    desc = raw_desc.decode("utf-8").strip()
                except UnicodeDecodeError:
                    desc = raw_desc.decode("latin-1").strip()
            else:
                try:
                    desc = raw_desc.encode("latin-1").decode("utf-8").strip()
                except (UnicodeDecodeError, UnicodeEncodeError):
                    desc = raw_desc.strip()
            if desc:
                result["description"] = desc

        return result

    except Exception as e:
        return {"error": f"EXIF-Fehler: {e}"}
