"""
routers/photos_geo.py
Aufnahmeorte der Fotos gebündelt (05.09.2026, additiv).

GET /api/photos/geo — die Koordinaten vieler Fotos in einem Aufruf, als
Kartenebene über Tagesweg und Heatmap (angefordert von der App,
docs/API_CHANGES.md). Bisher gab es die Koordinaten nur einzeln
(/api/photo/…/exif, ein Aufruf je Bild) oder zu 5-km-Zellen verdichtet
(/api/stats/geo) — beides für eine Karte über Jahre untauglich.

Quelle ist der Statistik-Cache `.mpd_stats.json` je Album, den
stats_scanner ohnehin schreibt (Felder has_gps/lat/lon je Foto). Diese
Route **scannt nie selbst** — ein Scan als Nebenwirkung eines
Kartenaufrufs wäre teurer als der Nutzen. Stattdessen meldet sie mit
`stand` und `albums_unscanned` ehrlich, wie frisch der Bestand ist.

Kein Modul-Gate (Entscheidung 05.09.2026): dieselbe Koordinate
liefert /api/photo/…/exif heute ohne Sperre, nur einzeln — ein Gate hier
wäre Kulisse. Die kostenpflichtige Leistung ist die Karte (trails) bzw.
die Auswertung (stats), nicht die Rohkoordinate. Es gelten die normalen
Space-Rechte: get_path() entscheidet, wer den Familienbestand sieht.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from core.exif import filename_datetime
from core.i18n import t as _t
from routers.deps import available_spaces, get_path, require_session

logger = logging.getLogger(__name__)
router = APIRouter()

_CACHE_NAME = ".mpd_stats.json"
_MAX_LIMIT = 50000

# space-Schlüssel → (Signatur, Einträge, ältester_scan, ungescannt).
# Bewusst OHNE Zeitfrist: der Signaturvergleich kostet einen stat() je
# Album-Ordner, eine Frist würde einen frischen Scan nur künstlich
# verzögern (im ersten Entwurf war er bis zu 5 Minuten unsichtbar).
_INDEX: dict = {}
_LOCK = threading.Lock()


def _signature(base: Path) -> tuple:
    """Fingerabdruck aller Album-Caches: (Name, mtime_ns). Ändert sich
    einer, wird neu eingelesen — der Cache ist damit nie veraltet."""
    sig = []
    try:
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            c = d / _CACHE_NAME
            try:
                sig.append((d.name, c.stat().st_mtime_ns))
            except OSError:
                sig.append((d.name, 0))
    except OSError:
        return ()
    return tuple(sig)


def _dt_of(photo: dict, filename: str) -> Optional[str]:
    """Aufnahmezeitpunkt im Zeitstrahl-Format. Bevorzugt aus dem
    Dateinamen (sekundengenau, kostet nichts) — der Statistik-Cache hält
    die Uhrzeit nur stundengenau, weil er nur `hour` speichert."""
    dt = filename_datetime(filename)
    if dt is not None:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    date = photo.get("date")
    if not date:
        return None
    hour = photo.get("hour")
    return f"{date}T{int(hour):02d}:00:00" if isinstance(hour, int) else f"{date}T00:00:00"


def _build(space: str, base: Path) -> tuple:
    """Alle Fotos mit Koordinate eines Space aus den Album-Caches lesen."""
    entries = []
    oldest_scan = None
    unscanned = 0
    try:
        dirs = sorted([d for d in base.iterdir() if d.is_dir()])
    except OSError:
        dirs = []
    for d in dirs:
        if d.name.startswith((".", "@", "#")):
            continue
        cache_path = d / _CACHE_NAME
        if not cache_path.is_file():
            # Ordner mit Album, aber ohne Statistik-Lauf → ehrlich zählen.
            # Durchlauf-Ordner (ohne album.json) scannt stats_scanner nie,
            # die zählen hier deshalb nicht als „fehlend".
            if (d / "album.json").is_file():
                unscanned += 1
            continue
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception as e:
            logger.warning("photos/geo: %s unlesbar: %s", cache_path, e)
            unscanned += 1
            continue
        scanned = cache.get("scanned_at")
        if isinstance(scanned, str) and (oldest_scan is None or scanned < oldest_scan):
            oldest_scan = scanned
        for photo in cache.get("photos") or []:
            if not photo.get("has_gps"):
                continue
            lat, lon = photo.get("lat"), photo.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            fname = photo.get("file")
            if not fname:
                continue
            entries.append({
                "space": space,
                "album": d.name,
                "file" : fname,
                "lat"  : lat,
                "lon"  : lon,
                "dt"   : _dt_of(photo, fname),
                "date" : photo.get("date"),
            })
    entries.sort(key=lambda e: (e["dt"] or "", e["album"], e["file"]))
    return entries, oldest_scan, unscanned


def _index_for(space: str, base: Path) -> tuple:
    sig = _signature(base)
    with _LOCK:
        cached = _INDEX.get(space)
    if cached and cached[0] == sig:
        return cached[1], cached[2], cached[3]
    entries, oldest, unscanned = _build(space, base)
    with _LOCK:
        _INDEX[space] = (sig, entries, oldest, unscanned)
    logger.info("photos/geo Index %s: %d Fotos mit Koordinate (%d Alben ungescannt)",
                space, len(entries), unscanned)
    return entries, oldest, unscanned


@router.get("/api/photos/geo")
async def photos_geo(
    request: Request,
    date : Optional[str] = Query(None, description="Aufnahmetag YYYY-MM-DD"),
    from_: Optional[str] = Query(None, alias="from", description="Tag YYYY-MM-DD, einschließlich"),
    to   : Optional[str] = Query(None, description="Tag YYYY-MM-DD, einschließlich"),
    tz   : int = Query(0, description="wird angenommen, wirkt aber nicht — s. Doku"),
    space: Optional[str] = Query(None, description="shared | personal; ohne Angabe alle erlaubten"),
    bbox : Optional[str] = Query(None, description="min_lat,min_lon,max_lat,max_lon"),
    limit: int = Query(5000, ge=1, le=_MAX_LIMIT),
):
    """Fotos mit Aufnahmeort, gebündelt.

    Zeitfilter arbeiten auf dem **Aufnahmedatum in Ortszeit** (so steht es
    im EXIF) — deshalb hat `tz` hier keine Wirkung; es wird nur
    angenommen, damit die App dieselben Parameter schicken kann wie an
    /api/trails/points.
    """
    session = require_session(request)

    spaces = available_spaces(session)
    if space:
        if space not in spaces:
            raise HTTPException(403, _t("common.no_shared_access", request))
        spaces = [space]

    for v in (date, from_, to):
        if v is not None and (len(v) != 10 or v[4] != "-" or v[7] != "-"):
            raise HTTPException(400, "Datum als YYYY-MM-DD angeben")
    lo = date or from_
    hi = date or to
    if lo and hi and hi < lo:
        raise HTTPException(400, "to muss nach from liegen")

    box = None
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            box = parts
        except ValueError:
            raise HTTPException(400, "bbox als min_lat,min_lon,max_lat,max_lon")

    hits = []
    oldest_scan = None
    unscanned = 0
    for sp in spaces:
        try:
            base = get_path(session, sp, request)
        except HTTPException:
            continue
        entries, oldest, un = _index_for(sp, base)
        unscanned += un
        if isinstance(oldest, str) and (oldest_scan is None or oldest < oldest_scan):
            oldest_scan = oldest
        for e in entries:
            d = e.get("date")
            if lo and (d is None or d < lo):
                continue
            if hi and (d is None or d > hi):
                continue
            if box and not (box[0] <= e["lat"] <= box[2] and box[1] <= e["lon"] <= box[3]):
                continue
            hits.append(e)

    hits.sort(key=lambda e: (e["dt"] or "", e["space"], e["album"], e["file"]))
    total = len(hits)
    out = hits[:limit]
    return {
        "photos"          : [{k: e[k] for k in ("space", "album", "file", "lat", "lon", "dt")}
                             for e in out],
        "count"           : len(out),
        "total"           : total,
        "truncated"       : total > len(out),
        "stand"           : oldest_scan,
        "albums_unscanned": unscanned,
    }
