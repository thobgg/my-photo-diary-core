"""
routers/memories.py
My Photo Diary v2 – DiaryMemories endpoints (session-based)

GET /api/memories
GET /api/memories-thumb/{album}/{filename}

Die Web-Seite /memories und der MEMORIES_TOKEN-Bypass der alten
Extra-APK wurden am 03.09.2026 entfernt — Erinnerungen zeigt die
native App (Modul memories); Auth läuft ausschließlich über die
normale Session (Bearer/Cookie).
"""

import asyncio
import logging
import time
from datetime import date as date_type
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from config import PHOTO_PATH_SHARED
from core.filesystem_photos import FilesystemPhotosAPI
from core.i18n import t as _t
from routers.deps import _PHOTO_EXTS, require_session

logger = logging.getLogger(__name__)
router = APIRouter()

# Scan-result cache: key = target-date-ISO(+scope), value = (ts, results)
# TTL 30 min; seit 03.09.2026 mit Stale-Serve: Abgelaufenes wird sofort
# geliefert und im Hintergrund erneuert — der 12-s-Kaltscan trifft nie
# mehr einen Nutzer (die App lief in ihr Timeout).
_MEMORIES_CACHE: dict = {}
_MEMORIES_TTL_S = 30 * 60
_MEMORIES_REFRESHING: set = set()


def _cache_key_for(target, url_space: str, user: str) -> str:
    return (f"{target.isoformat()}|{url_space}"
            + (f"|{user}" if url_space == "personal" else ""))


async def _scan_to_cache(photo_base, target, url_space: str, cache_key: str):
    """Scan im Threadpool ausführen und Ergebnis in den Cache legen."""
    from diary_memories.scanner import scan
    t0 = time.perf_counter()
    results = await asyncio.to_thread(
        scan,
        photo_base   = photo_base,
        target_date  = target,
        current_year = date_type.today().year,
        url_space    = url_space,
    )
    _MEMORIES_CACHE[cache_key] = (time.monotonic(), results)
    logger.info("memories scan %s fresh %dms (cached %ds)",
                cache_key, int((time.perf_counter() - t0) * 1000), _MEMORIES_TTL_S)
    return results


def _refresh_in_background(photo_base, target, url_space: str, cache_key: str) -> None:
    if cache_key in _MEMORIES_REFRESHING:
        return
    _MEMORIES_REFRESHING.add(cache_key)

    async def _job():
        try:
            await _scan_to_cache(photo_base, target, url_space, cache_key)
        except Exception as e:
            logger.error("memories refresh failed (%s): %s", cache_key, e)
        finally:
            _MEMORIES_REFRESHING.discard(cache_key)

    asyncio.create_task(_job())


async def prewarm_memories(reason: str = "startup") -> None:
    """Tages-Scan vorwärmen: Shared + jeder Nutzer mit personal-Scope.
    Aufgerufen beim App-Start (lifespan) und täglich nach Mitternacht
    (neuer Datums-Schlüssel) durch den Scheduler."""
    from core.userdb import list_users, lookup
    today = date_type.today()
    targets = [(PHOTO_PATH_SHARED, "shared", "")]
    try:
        for u in list_users():
            rec = lookup(u)
            if rec and rec.memories_scope == "personal" and rec.personal_path.is_dir():
                targets.append((rec.personal_path, "personal", u))
    except Exception as e:
        logger.warning("memories prewarm: Nutzerliste fehlgeschlagen: %s", e)
    for base, space, user in targets:
        try:
            await _scan_to_cache(base, today, space, _cache_key_for(today, space, user))
        except Exception as e:
            logger.warning("memories prewarm (%s/%s) fehlgeschlagen: %s", space, user, e)
    logger.info("memories prewarm done (%s, %d Ziele)", reason, len(targets))


def _require_memories(request: Request):
    """Session-Pflicht + Demo-Block + Modul-Freischaltung. Seit dem
    Wegfall des Token-Bypasses der normale, strenge Weg.
    Gibt die Session zurück (für die Scope-Auflösung)."""
    session = require_session(request)
    if session.is_demo:
        raise HTTPException(403, _t("memories.demo_unavailable", request))
    # Kein require_module mehr: Memories ist seit 20.09.2026 Kern
    # (routers/deps.py, MODULE_KEYS).
    return session


def _scope_base(session):
    """(photo_base, url_space) je nach memories_scope des Nutzers
    (users.json, 03.09.2026). Bei personal kommt der Pfad IMMER aus der
    eigenen Session — fremde Personal-Fotos sind damit unerreichbar."""
    from core.userdb import lookup
    rec = lookup(session.user)
    if rec and rec.memories_scope == "personal":
        return session.personal_path, "personal"
    return PHOTO_PATH_SHARED, "shared"


@router.get("/api/memories")
async def get_memories(request: Request, date: str = Query(None, pattern=r"^\d{2}-\d{2}$"),
                       limit: int = Query(0, ge=0, le=100)):
    """`limit` (additiv 08.09.2026): hoechstens so viele Fotos je Jahr, in
    der Rangfolge des Scanners (rank_photos). 0 oder fehlend = alle, in
    derselben Rangfolge. `years[].total` bleibt die volle Zahl des Tages,
    das Top-Level-`total` ebenso — fuer „und N weitere"."""
    session = _require_memories(request)

    try:
        photo_base, url_space = _scope_base(session)
        if date:
            m, d   = date.split("-")
            target = date_type(date_type.today().year, int(m), int(d))
        else:
            target = date_type.today()

        # Scope/Nutzer gehören in den Cache-Schlüssel — Personal-Scans
        # sind je Nutzer verschieden.
        cache_key = _cache_key_for(target, url_space, session.user)
        now = time.monotonic()
        cached = _MEMORIES_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _MEMORIES_TTL_S:
            results = cached[1]
        elif cached:
            # Stale-Serve: alten Stand sofort liefern, frisch im Hintergrund
            results = cached[1]
            _refresh_in_background(photo_base, target, url_space, cache_key)
        else:
            # Nie gescannt (z. B. Wunschdatum ?date=…): synchron
            results = await _scan_to_cache(photo_base, target, url_space, cache_key)

        if limit:
            results = [dict(b, photos=b["photos"][:limit]) for b in results]
        return {
            "date" : target.strftime("%m-%d"),
            "label": target.strftime("%-d. %B"),
            "years": results,
            "total": sum(b.get("total", len(b["photos"])) for b in results),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Memories error: {e}", exc_info=True)
        raise HTTPException(500, _t("memories.scan_error", request, {"msg": str(e)}))


@router.get("/api/memories-thumb/{album_name}/{filename}")
async def get_memories_thumbnail(
    request   : Request,
    album_name: str,
    filename  : str,
    size      : str = Query("m", pattern="^(sm|m|xl)$"),
    space     : str = Query("shared", pattern="^(shared|personal)$"),
    v         : str = Query(None, max_length=40, pattern="^[0-9a-f]*$"),
):
    """
    Thumbnail endpoint für die Memories-Ansicht der nativen App
    (App-Vertrag: thumbnail_url aus /api/memories). space=personal löst
    im Personal-Pfad der EIGENEN Session auf (03.09.2026) — fremde
    Personal-Fotos sind unerreichbar.
    FilesystemPhotosAPI caches centrally in THUMB_CACHE_DIR/fs/.
    """
    session = _require_memories(request)
    if Path(filename).suffix.lower() not in _PHOTO_EXTS:
        raise HTTPException(415, _t("memories.no_photo_thumb", request, {"filename": filename}))

    base = session.personal_path if space == "personal" else PHOTO_PATH_SHARED
    api = FilesystemPhotosAPI(base=base)
    photo_id = f"{album_name}/{filename}"

    try:
        data = api.get_thumbnail(photo_id, "", size)
    except FileNotFoundError:
        raise HTTPException(404, _t("memories.file_not_found", request, {"photo_id": photo_id}))
    except Exception as e:
        logger.error(f"Memories thumb error: {e}", exc_info=True)
        raise HTTPException(500, _t("memories.thumb_error", request, {"msg": str(e)}))

    # ?v= wie bei /api/thumbnail: passende Kurzversion → immutable/1 Jahr
    cache = "public, max-age=86400"
    if v:
        from core.thumb_pipeline import etag_for
        src = base / album_name / filename
        if src.is_file() and etag_for(src).startswith(v):
            cache = "public, max-age=31536000, immutable"
    return Response(
        content    = data,
        media_type = "image/jpeg",
        headers    = {"Cache-Control": cache},
    )
