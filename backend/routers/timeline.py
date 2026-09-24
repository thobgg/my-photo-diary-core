"""
routers/timeline.py
My Photo Diary v2 — GET /api/timeline: chronologische Medienansicht
(native App, additiv 09/2026). Vertrag: docs/API_CHANGES.md.

Grundpaket (Entscheidung 03.09.2026): Der Zeitstrahl ist die
Grund-Blätteransicht — KEIN Modul-Gating (das kurzlebige Modul
'timeline' vom 01.09. wurde wieder ausgebaut).
"""

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from core import timeline_index
from core.media_types import is_editable
from routers.deps import (
    available_spaces, get_path, media_urls, require_session,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _key(item: dict, space: str) -> tuple:
    return (item["dt"], space, item["album"], item["file"])


def _encode_cursor(key: tuple) -> str:
    raw = json.dumps(list(key), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple:
    try:
        parts = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        assert isinstance(parts, list) and len(parts) == 4
        return tuple(str(p) for p in parts)
    except Exception:
        raise HTTPException(400, "cursor ungueltig")


@router.get("/api/timeline")
async def get_timeline(
    request: Request,
    cursor : Optional[str] = None,
    limit  : int = Query(200, ge=1, le=500),
    date   : Optional[str] = Query(None, description="nur dieser Aufnahmetag, YYYY-MM-DD"),
    space  : Optional[str] = Query(None, description="nur dieser Bestand: shared | personal"),
    album  : Optional[str] = Query(None, description="nur dieser Ordner (Album oder Durchlauf)"),
    referenced: Optional[bool] = Query(None, description="true = nur kuratierte, false = nur unzugeordnete Dateien"),
    folder : Optional[str] = Query(None, description="Ordnerarten, kommagetrennt: album, durchlauf, folder"),
):
    """`date` (additiv 05.09.2026): filtert auf einen Aufnahmetag. Ohne
    ihn müsste ein Client für „die Fotos dieses Tages" durch alle Seiten
    blättern oder sich einen Cursor bauen — dessen Aufbau ist bewusst
    undokumentiert, damit wir ihn ändern können."""
    session = require_session(request)
    if date is not None and (len(date) != 10 or date[4] != "-" or date[7] != "-"):
        raise HTTPException(400, "date als YYYY-MM-DD angeben")
    # space/album/referenced (additiv 08.09.2026) tragen die Durchlauf-
    # Ansicht im Web: „alles Unzugeordnete in diesem Bestand". Ein Bestand,
    # den das Konto nicht sieht, ist kein 403, sondern schlicht leer —
    # available_spaces() entscheidet, wie ueberall.
    spaces = available_spaces(session)
    if space is not None:
        if space not in ("shared", "personal"):
            raise HTTPException(400, "space: shared oder personal")
        spaces = [s for s in spaces if s == space]

    merged: list = []  # (item, sp)
    for sp in spaces:
        base = Path(get_path(session, sp))
        if not base.is_dir():
            continue
        items = await asyncio.to_thread(timeline_index.get_index, base)
        merged.extend((it, sp) for it in items)

    if album is not None:
        merged = [t for t in merged if t[0].get("album") == album]
    if referenced is not None:
        merged = [t for t in merged if bool(t[0].get("referenced")) == referenced]
    if folder is not None:
        kinds = {k.strip() for k in folder.split(",") if k.strip()}
        if not kinds or not kinds <= {"album", "durchlauf", "folder"}:
            raise HTTPException(400, "folder: album, durchlauf, folder")
        merged = [t for t in merged if t[0].get("folder", "folder") in kinds]

    # Absteigend nach Aufnahmezeitpunkt; space/album/file als stabile
    # Tie-Breaker (identisch zum Cursor-Schlüssel).
    merged.sort(key=lambda t: _key(t[0], t[1]), reverse=True)

    if date:
        # item["dt"] ist "YYYY-MM-DDTHH:MM:SS" — Präfixvergleich reicht.
        merged = [t for t in merged if (t[0].get("dt") or "").startswith(date)]

    total = len(merged)   # nach den Filtern, vor dem Cursor — fuer Zaehler
    if cursor:
        ckey = _decode_cursor(cursor)
        merged = [t for t in merged if _key(t[0], t[1]) < ckey]

    page = merged[:limit]

    out = []
    for item, space in page:
        # URL-Schema zentral in deps.media_urls() — identisch zur Album-Antwort
        thumbnails, original = media_urls(space, item["album"], item["file"],
                                          version=item.get("v"))
        entry = {
            "date"       : item["dt"],
            "date_source": item["date_source"],
            "space"      : space,
            "album"      : item["album"],
            "folder"     : item.get("folder", "album"),
            "file"       : item["file"],
            "type"       : item["type"],
            "referenced" : item["referenced"],
            "locked"     : item["locked"],
            "thumbnails" : thumbnails,
            "original"   : original,
            # Format-Haelfte der Bearbeitbarkeit, wie in der
            # Album-Antwort. Die Rechte-Haelfte kennt der Client aus
            # `rights` (/api/whoami).
            "editable_format": is_editable(item["file"]),
        }
        if item.get("resolution"):
            entry["resolution"] = item["resolution"]
        if item.get("blurhash"):
            entry["blurhash"] = item["blurhash"]
        out.append(entry)

    return {
        "items"      : out,
        "count"      : len(out),
        "total"      : total,
        "next_cursor": _encode_cursor(_key(*page[-1]))
                       if len(merged) > limit else None,
    }
