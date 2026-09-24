"""
core/timeline_index.py
My Photo Diary v2 — chronologischer Medien-Index für /api/timeline
(native App, additiv 09/2026).

Google-Photos-artige Zeitleiste über alle Medien eines Space — auch
Dateien, die in keinem album.json referenziert sind (referenced=false):
das ist der Kern der Ansicht und macht sie fürs kommende Kamera/Upload-
Feature wertvoll.

Aufbau je Space (= Basis-Pfad), gecacht im Prozess-RAM:
  1. Referenz-Karte aus allen album.json (inkl. source_album-Auflösung):
     liefert referenced/locked/resolution je (Ordner, Datei).
  2. Alle Mediendateien der Album-Ordner mit Aufnahmezeitpunkt:
     Dateiname (YYYY-MM-DD_HH-MM-SS…, einzige Quelle mit Uhrzeit;
     bevorzugt wie überall in core/exif.py) → EXIF-Datum aus dem
     persistenten Cache (get_photo_meta_cached, Tagesgenauigkeit)
     → Datei-mtime. Quelle steht in date_source.

Kein Scan pro Request: Invalidierung über invalidate_timeline_index()
an allen Schreibstellen (Album create/update/delete, Datei-Löschung,
Foto-Editor) — plus TTL, weil Dateien auch an der API vorbei dazukommen
(Synology-Photos-Upload direkt ins Dateisystem).
"""

import json
import re
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from core.exif import filename_datetime, get_photo_meta_cached
from core.thumb_pipeline import etag_for
from routers.deps import _MEDIA_EXTS, _PHOTO_EXTS

logger = logging.getLogger(__name__)

_TTL_SECONDS = 300  # Zugänge außerhalb der API spätestens nach 5 min sichtbar

# base-Pfad (str) → (built_at, items). Shared-Index gilt für alle Nutzer,
# Personal-Indizes je Home — der Schlüssel ist einfach der Pfad.
_index: Dict[str, Tuple[float, List[dict]]] = {}
_lock = threading.Lock()

_SKIP_DIRS = {"@eaDir", "#recycle"}


_building: set = set()


def invalidate_timeline_index() -> None:
    """Harte Invalidierung nach Schreibvorgängen: Index löschen, der
    nächste Aufruf baut synchron — der Nutzer muss seine eigene
    Änderung (Upload, Einsortieren, Album-Save) sofort sehen.
    Stale-Serve gibt es bewusst NUR beim TTL-Ablauf (get_index):
    dort bremst der Neubau sonst regelmäßig das Laden, ohne dass
    jemand auf frische Daten wartet."""
    with _lock:
        if _index:
            _index.clear()
            logger.info("Timeline index invalidated")


def _rebuild_in_background(base: Path, key: str) -> None:
    with _lock:
        if key in _building:
            return
        _building.add(key)

    def _job():
        try:
            items = _build(base)
            with _lock:
                _index[key] = (time.time(), items)
        except Exception as e:
            logger.error("Timeline rebuild failed (%s): %s", key, e)
        finally:
            with _lock:
                _building.discard(key)

    threading.Thread(target=_job, name="timeline-rebuild", daemon=True).start()


def get_index(base: Path) -> List[dict]:
    """Index für einen Space holen. Frisch → direkt; abgelaufen → alten
    Stand sofort liefern + Hintergrund-Neubau; noch nie gebaut →
    synchron (einmalig). Blockierend nur im letzten Fall — im Router
    via asyncio.to_thread aufrufen."""
    key = str(base)
    now = time.time()
    with _lock:
        hit = _index.get(key)
    if hit:
        if now - hit[0] >= _TTL_SECONDS:
            _rebuild_in_background(base, key)
        return hit[1]
    items = _build(base)
    with _lock:
        _index[key] = (time.time(), items)
    return items


def _resolve_datetime(f: Path, album_dir: Path) -> Tuple[datetime, str]:
    dt = filename_datetime(f.name)
    if dt:
        return dt, "filename"
    # EXIF aus dem persistenten Album-Cache (tagesgenau). Für Videos
    # schlägt der EXIF-Versuch einmal fehl und wird als None gecacht.
    try:
        meta = get_photo_meta_cached(f, album_dir)
        if meta.get("date"):
            d = meta["date"]
            return datetime(d.year, d.month, d.day), "exif"
    except Exception as e:
        logger.debug("Timeline EXIF error %s/%s: %s", album_dir.name, f.name, e)
    return datetime.fromtimestamp(f.stat().st_mtime), "mtime"


def _build(base: Path) -> List[dict]:
    t0 = time.monotonic()

    # ── 1. Referenz-Karte aus allen album.json ──────────────────────────
    # Schlüssel ist der AUFGELÖSTE Ort der Datei (source_album beachtet).
    # locked: nur True, wenn JEDE Referenz gesperrt ist — sobald eine
    # Referenz sichtbar ist, gilt die Datei als sichtbar.
    refs: Dict[Tuple[str, str], dict] = {}
    album_dirs = [
        d for d in sorted(base.iterdir())
        if d.is_dir() and d.name not in _SKIP_DIRS and not d.name.startswith(".")
    ]
    # Ordnerart (08.09.2026): "album" = hat album.json, "durchlauf" =
    # nackte Jahreszahl ohne album.json (Sammelordner der Kamera),
    # "folder" = weder noch — ein kopierter Ordner, der auf „Album
    # hinzufuegen" wartet. Die Durchlauf-Ansicht zeigt nur die ersten
    # beiden; die dritte Art ist ein fertiges Album, kein Rest.
    kinds: Dict[str, str] = {}
    for album_dir in album_dirs:
        aj = album_dir / "album.json"
        if not aj.is_file():
            kinds[album_dir.name] = "durchlauf" if re.fullmatch(r"\d{4}", album_dir.name) else "folder"
            continue
        kinds[album_dir.name] = "album"
        try:
            data = json.loads(aj.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Timeline: album.json unlesbar (%s): %s", album_dir.name, e)
            continue
        for elem in data.get("elements", []):
            if elem.get("type") not in ("photo", "video") or not elem.get("file"):
                continue
            target = elem.get("source_album") or album_dir.name
            ref = refs.setdefault(
                (target, elem["file"]),
                {"locked": True, "resolution": None, "blurhash": None},
            )
            if not elem.get("locked"):
                ref["locked"] = False
            if elem.get("resolution") and not ref["resolution"]:
                ref["resolution"] = elem["resolution"]
            if elem.get("blurhash") and not ref["blurhash"]:
                ref["blurhash"] = elem["blurhash"]

    # ── 2. Mediendateien einsammeln ─────────────────────────────────────
    items: List[dict] = []
    for album_dir in album_dirs:
        for f in album_dir.iterdir():
            if not f.is_file() or f.name.startswith("."):
                continue
            suffix = f.suffix.lower()
            if suffix not in _MEDIA_EXTS:
                continue
            dt, source = _resolve_datetime(f, album_dir)
            ref = refs.get((album_dir.name, f.name))
            items.append({
                "dt"         : dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "date_source": source,
                "album"      : album_dir.name,
                "folder"     : kinds.get(album_dir.name, "folder"),
                "file"       : f.name,
                "type"       : "photo" if suffix in _PHOTO_EXTS else "video",
                "referenced" : ref is not None,
                "locked"     : bool(ref["locked"]) if ref else False,
                "resolution" : ref["resolution"] if ref else None,
                "blurhash"   : ref.get("blurhash") if ref else None,
                # Kurz-Cache-Schlüssel → immutable-Thumb-URLs (?v=)
                "v"          : etag_for(f)[:16],
            })

    items.sort(key=lambda i: (i["dt"], i["album"], i["file"]), reverse=True)
    logger.info(
        "Timeline index built: %s — %d items in %.1fs",
        base, len(items), time.monotonic() - t0,
    )
    return items
