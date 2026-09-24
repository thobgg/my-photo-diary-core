"""
routers/album_files.py
File operations on album folders (non-metadata).

GET    /api/album/{space}/{album_name}/unassigned-photos
GET    /api/album/{space}/{album_name}/unassigned-audio
GET    /api/album/{space}/{album_name}/unassigned-documents
GET    /api/recycle-status/{space}
DELETE /api/album/{space}/{album_name}/file
PUT    /api/album/{space}/{album_name}/file/{filename}   (Upload, 09/2026)
"""
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from core import events
from core.i18n import t as _t
from core.media_types import is_editable, unsupported_in, unassigned_in
from core.timeline_index import invalidate_timeline_index
from routers.deps import (
    require_session, get_path, is_readonly, can_contribute, media_urls,
    _PHOTO_EXTS, _MEDIA_EXTS, _AUDIO_EXTS, _DOC_EXTS,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class DeletePhotoFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1)
    # Absicht des Aufrufs, nicht Eigenschaft der Ansicht (06.09.2026):
    # true = „ich loesche aus dem Album heraus, wo dieses Foto steht".
    # Der Zeitstrahl und das Vollbild im Ansehen-Kontext schicken es nie.
    # Bewusst NICHT `from_album` — das heisst bei import-file schon etwas
    # anderes (Quellalbum als Text).
    album_context: bool = False


@router.get("/api/album/{space}/{album_name}/unassigned-photos")
async def get_unassigned_photos(request: Request, space: str, album_name: str):
    session = require_session(request)
    try:
        base            = get_path(session, space)
        album_json_path = base / album_name / "album.json"
        if not album_json_path.exists():
            raise HTTPException(404, _t("album.not_found", request))

        with open(album_json_path, "r", encoding="utf-8") as f:
            album_data = json.load(f)

        album_dir  = base / album_name
        unassigned = unassigned_in(album_dir, album_data)   # s. core/media_types
        # Thumbnail-Adressen mitgeben (08.09.2026): Das Web haengt das
        # Element nach dem Einfuegen sofort in die Ansicht, und der
        # Renderer verlangt seit April `thumbnails` — ohne sie brach das
        # Zeichnen ab und das Album stand leer, bis F5 sie vom Server
        # holte. Dasselbe Schema wie in der Album-Antwort (media_urls).
        from core.thumb_pipeline import etag_for
        for u in unassigned:
            try:
                u["thumbnails"], u["original"] = media_urls(
                    space, album_name, u["file"],
                    version=etag_for(album_dir / u["file"])[:16])
            except OSError:
                pass

        # Was der Ordner enthaelt, MPD aber nicht anzeigen kann. Ohne
        # das steht im Dialog „alle Fotos sind bereits im Album", waehrend
        # zwanzig Dateien danebenliegen und niemand erfaehrt es.
        skipped = unsupported_in(album_dir)

        return {
            "album"        : album_name,
            "space"        : space,
            "unassigned"   : unassigned,
            "count"        : len(unassigned),
            "skipped"      : skipped,
            "skipped_count": sum(e["count"] for e in skipped),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("common.error_generic", request, {"msg": str(e)}))


@router.get("/api/album/{space}/{album_name}/unassigned-audio")
async def get_unassigned_audio(request: Request, space: str, album_name: str):
    session = require_session(request)
    try:
        base            = get_path(session, space)
        album_json_path = base / album_name / "album.json"
        if not album_json_path.exists():
            raise HTTPException(404, _t("album.not_found", request))

        with open(album_json_path, "r", encoding="utf-8") as f:
            album_data = json.load(f)

        referenced = {
            e["file"] for e in album_data.get("elements", [])
            if e.get("type") == "audio" and "file" in e
        }

        album_dir  = base / album_name
        unassigned = []
        for f in sorted(album_dir.iterdir()):
            if f.suffix.lower() in _AUDIO_EXTS and f.name not in referenced and not f.name.startswith('.'):
                unassigned.append({"file": f.name, "type": "audio"})

        return {"album": album_name, "space": space, "unassigned": unassigned, "count": len(unassigned)}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("common.error_generic", request, {"msg": str(e)}))


@router.get("/api/album/{space}/{album_name}/unassigned-documents")
async def get_unassigned_documents(request: Request, space: str, album_name: str):
    session = require_session(request)
    try:
        base            = get_path(session, space)
        album_json_path = base / album_name / "album.json"
        if not album_json_path.exists():
            raise HTTPException(404, _t("album.not_found", request))
        with open(album_json_path, "r", encoding="utf-8") as f:
            album_data = json.load(f)
        referenced = {
            e["file"] for e in album_data.get("elements", [])
            if e.get("type") == "document" and "file" in e
        }
        album_dir  = base / album_name
        unassigned = []
        for f in sorted(album_dir.iterdir()):
            if f.suffix.lower() in _DOC_EXTS and f.name not in referenced and not f.name.startswith('.'):
                unassigned.append({"file": f.name, "type": "document"})
        return {"album": album_name, "space": space, "unassigned": unassigned, "count": len(unassigned)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("common.error_generic", request, {"msg": str(e)}))


def find_recycle_dir(base: Path):
    """Der Papierkorb der FREIGABE, von `base` aus aufwaerts gesucht.

    Warum aufwaerts: Im Familienbestand ist `base` die Freigabe selbst
    (`/volume1/photo`), der Papierkorb liegt also direkt darin. Im
    persoenlichen Bereich zeigt `base` aber IN die Freigabe hinein
    (`/volume1/homes/<nutzer>/Photos`), waehrend DSM den Papierkorb eine
    Ebene hoeher fuehrt (`/volume1/homes/<nutzer>/#recycle`). Wer nur in
    `base` nachsieht, findet dort nie einen — und meldet faelschlich
    „kein Papierkorb", obwohl einer existiert.

    GENAU ZWEI EBENEN, und das ist eine Sicherheitsgrenze, keine
    Bequemlichkeit: Mit drei Ebenen findet ein persoenlicher Bereich ohne
    eigenen Papierkorb den der GANZEN homes-Freigabe
    (`/volume1/homes/#recycle`). Dort kommt nur der Administrator hin —
    die geloeschte Datei eines Nutzers waere damit aus seinem Bereich
    heraus in fremde Sicht gewandert. Am 06.09.2026 live aufgefallen:
    Das Demo-Konto hat keinen eigenen Korb und bekam prompt den der
    Freigabe angeboten. Zwei Ebenen decken beide echten Faelle ab —
    Familienbestand (`base` IST die Freigabe) und persoenlicher Bereich
    (`<home>/#recycle`) — und keinen darueber hinaus.

    Kein Treffer heisst: endgueltig loeschen, und die Antwort sagt es.
    """
    d = base
    for _ in range(2):
        cand = d / "#recycle"
        if cand.is_dir():
            return cand
        if d.parent == d:
            break
        d = d.parent
    return None


def recycle_target_dir(recycle_dir: Path, album_dir: Path) -> Path:
    """Wohin im Papierkorb eine Datei aus `album_dir` gehoert.

    DSM legt im Korb den Pfad RELATIV ZUR FREIGABE ab — und die Freigabe
    ist das Elternverzeichnis des `#recycle`-Ordners, nicht MPDs `base`.
    Bis 07.09.2026 stand hier `recycle_dir / album_name`, und das stimmte
    nur im Familienbestand, wo `base` zufaellig die Freigabe IST. Im
    persoenlichen Bereich (`<home>/Photos/<album>`) fehlte das Segment
    `Photos/`; am Bestand nebeneinander gesehen:

        <home>/#recycle/Photos/DCIM/…          ← von DSM
        <home>/#recycle/2026 Wegwerf Edit-Test ← von MPD, falsch

    Eine Wiederherstellung ueber File Station haette die Datei damit
    neben `Photos/` gelegt statt hinein — ausserhalb von MPDs Blick.

    Eine Regel fuer beide Bereiche, kein Sonderfall fuer `personal`:
    Der Zielpfad ist der Pfad des Albums relativ zu `recycle_dir.parent`.
    Wie viele Ebenen `find_recycle_dir()` aufwaerts gegangen ist, steckt
    damit automatisch drin.

    Faellt `relative_to` durch (Korb nicht oberhalb des Albums — sollte
    nach `find_recycle_dir` nicht vorkommen), bleibt der alte Weg.
    """
    try:
        return recycle_dir / album_dir.relative_to(recycle_dir.parent)
    except ValueError:
        return recycle_dir / album_dir.name


@router.get("/api/recycle-status/{space}")
async def recycle_status(request: Request, space: str):
    session = require_session(request)
    try:
        base = get_path(session, space)
        return {"recycled": find_recycle_dir(base) is not None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _t("common.error_generic", request, {"msg": str(e)}))


def _used_by_other_albums(base: Path, album_name: str, filename: str) -> list:
    """Welche ANDEREN Alben verweisen auf diese Datei?

    „Fremd" heisst: Ein anderes Album zeigt per `source_album` hierher.
    Die Referenz des eigenen Ordners zaehlt nicht — sonst koennte man ein
    Foto nie aus seinem eigenen Album loeschen.

    Aufgeloest wird wie im Zeitstrahl-Index: Der Ort eines Elements ist
    `source_album` oder der Ordner seiner album.json. Ohne diese Pruefung
    bleibt beim Loeschen ein Element zurueck, das ins Leere zeigt — der
    Purge unten raeumt nur die eigene album.json auf.
    """
    treffer = []
    try:
        album_dirs = [d for d in sorted(base.iterdir()) if d.is_dir()]
    except OSError:
        return treffer
    for d in album_dirs:
        if d.name == album_name:
            continue                      # die eigene Referenz zaehlt nicht
        aj = d / "album.json"
        if not aj.is_file():
            continue
        try:
            data = json.loads(aj.read_text(encoding="utf-8"))
        except Exception:
            continue
        for elem in data.get("elements", []):
            if elem.get("file") != filename:
                continue
            if elem.get("source_album") == album_name:
                treffer.append({"album": d.name,
                                "title": (data.get("meta") or {}).get("title")})
                break
    return treffer


@router.delete("/api/album/{space}/{album_name}/file")
async def delete_photo_file(request: Request, space: str, album_name: str, body: DeletePhotoFileRequest):
    session = require_session(request)
    # Kuratier-Modell C+ (02.09.2026): Volles Löschen ist Kurator-Sache
    # (is_readonly). Beitragende (Editor im Shared) dürfen zusätzlich
    # UNREFERENZIERTE Dateien löschen — den eigenen Ausschuss im
    # Durchlauf-Ordner. Die Referenz-Prüfung folgt unten, wenn die
    # album.json gelesen ist.
    contributor_only = is_readonly(session, space)
    if contributor_only and not can_contribute(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))

    filename = body.filename.strip()
    if not filename:
        raise HTTPException(400, _t("album.filename_missing", request))

    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400, _t("album.invalid_filename", request))

    suffix = Path(filename).suffix.lower()
    # Seit 09/2026 auch Videos: die Kamera lädt beide, der Ausschuss-
    # Handgriff im Zeitstrahl muss beide erreichen.
    if suffix not in _MEDIA_EXTS:
        raise HTTPException(400, _t("album.only_photos_allowed", request, {"exts": ", ".join(sorted(_MEDIA_EXTS))}))

    try:
        base      = get_path(session, space)
        album_dir = base / album_name
        file_path = album_dir / filename

        try:
            file_path.resolve().relative_to(album_dir.resolve())
        except ValueError:
            raise HTTPException(400, _t("album.invalid_filepath", request))

        if not file_path.exists():
            raise HTTPException(404, _t("album.file_not_found", request, {"file": filename}))
        if not file_path.is_file():
            raise HTTPException(400, _t("album.not_a_regular_file", request))

        # Schutz 1: Was in einem Albumordner liegt, wird im Album
        # geloescht — nicht aus dem Zeitstrahl oder dem Vollbild heraus.
        # Regel: Bilder loescht man dort, wo man sie sieht. Der
        # Server sieht die Ansicht nicht, deshalb muss der Aufruf seine
        # Absicht mitschicken.
        if not body.album_context and (album_dir / "album.json").is_file():
            raise HTTPException(403, _t("album.delete_in_album_folder", request,
                                        {"file": filename}))

        # Schutz 2: Fremdreferenzen. NICHT ueberstimmbar, auch nicht mit
        # album_context — eine Referenz zu brechen ist nichts, was man im
        # Vorbeigehen bestaetigt. Dasselbe Muster wie beim Einsortieren,
        # das mit 409 auf „erst dort entfernen" besteht.
        fremd = _used_by_other_albums(base, album_name, filename)
        if fremd:
            raise HTTPException(409, {
                "detail": _t("album.delete_used_elsewhere", request, {
                    "file": filename, "count": len(fremd),
                    "albums": ", ".join(a["title"] or a["album"] for a in fremd),
                }),
                "used_by": fremd,
            })

        recycle_dir = find_recycle_dir(base)

        album_json_path = album_dir / "album.json"
        was_thumbnail = False
        album_data = None
        if album_json_path.exists():
            try:
                with open(album_json_path, "r", encoding="utf-8") as f:
                    album_data = json.load(f)
                was_thumbnail = album_data.get("meta", {}).get("thumbnail") == filename
            except Exception:
                album_data = None

        # C+: Beitragende dürfen nur Unreferenziertes löschen (Ausschuss im
        # Durchlauf); referenzierte Dateien und Cover bleiben Kurator-Sache.
        if contributor_only:
            referenced = was_thumbnail or (
                album_data is not None and any(
                    e.get("file") == filename
                    for e in album_data.get("elements", [])
                )
            )
            if referenced:
                raise HTTPException(403, _t("album.delete_referenced", request))

        # In den Papierkorb verschieben statt loeschen — sofern die
        # Freigabe einen hat. Bis 06.09.2026 stand hier ein blankes
        # unlink(): Der DSM-Papierkorb haengt an der Dateifreigabe (SMB),
        # ein Loeschen aus dem Container geht daran vorbei. Die Antwort
        # meldete trotzdem `recycled: true`, sobald es irgendwo einen
        # Papierkorb-Ordner GAB — beide Oberflaechen haben daraus
        # „landet im Papierkorb" gemacht, und das war unwahr.
        recycled = False
        if recycle_dir is not None:
            # Pfad relativ zur Freigabe, nicht zu base — sonst findet
            # DSMs Wiederherstellen den Weg zurueck nicht (s. Helfer).
            ziel_dir = recycle_target_dir(recycle_dir, album_dir)
            ziel = ziel_dir / filename
            try:
                ziel_dir.mkdir(parents=True, exist_ok=True)
                if ziel.exists():
                    # Gleicher Name schon im Papierkorb: nicht ueberschreiben,
                    # sonst frisst die zweite Loeschung die erste.
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    ziel = ziel_dir / f"{Path(filename).stem}_{stamp}{Path(filename).suffix}"
                shutil.move(str(file_path), str(ziel))
                recycled = True
            except OSError as e:
                # Papierkorb nicht beschreibbar o. ae. — dann lieber
                # endgueltig loeschen als die Aktion scheitern lassen,
                # aber ehrlich melden.
                logger.warning("Papierkorb nicht nutzbar (%s), loesche endgueltig: %s",
                               recycle_dir, e)
        if not recycled:
            file_path.unlink()

        # Also delete editor backups, otherwise chaos on re-upload of same name.
        # Must match BACKUP_CAP=5 in core/photo_edit.py: .bak, .bak1..bak4.
        removed_baks = 0
        for i in range(5):
            bak_suffix = ".bak" if i == 0 else f".bak{i}"
            bak_path = album_dir / f"{filename}{bak_suffix}"
            try:
                if bak_path.exists():
                    bak_path.unlink()
                    removed_baks += 1
            except OSError:
                pass

        # Thumb cache self-invalidates (sha1 includes mtime; deleted file
        # is never queried again → orphan cache is ignored).

        # Remove entry from album.json so the viewer/lightbox doesn't see
        # ghost elements pointing to a no-longer-existing file after reload.
        removed_from_json = False
        if album_data is not None:
            try:
                before = len(album_data.get("elements", []))
                album_data["elements"] = [
                    e for e in album_data.get("elements", [])
                    if e.get("file") != filename
                ]
                if len(album_data["elements"]) != before:
                    # Aenderungszeitpunkt stempeln — wie update_album und
                    # _append_media_element es tun. Bis 06.09.2026 war das
                    # Loeschen der EINZIGE Schreiber ohne diesen Stempel,
                    # mit zwei Folgen: Clients, die daran erkennen, ob sich
                    # etwas getan hat, zeigten das geloeschte Foto weiter —
                    # und der Konfliktschutz (X-MPD-Base-Modified) verglich
                    # gegen einen Wert, der sich nicht geruehrt hatte, nahm
                    # also ein Speichern von VOR der Loeschung an und holte
                    # das Element damit still zurueck.
                    album_data.setdefault("meta", {})["modified"] = \
                        datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    # Wie ueberall beim Schreiben einer album.json: erst
                    # Sicherung, dann atomar. Hier stand bis 06.09.2026 ein
                    # blankes open(..., "w") — die einzige Schreibstelle im
                    # Haus ohne beides. Ein Abbruch mittendrin haette genau
                    # die halb geschriebene album.json hinterlassen, von der
                    # ARCHITEKTUR.md sagt, dass es sie nicht geben kann.
                    from routers.albums import (create_backup, _write_atomic,
                                                invalidate_albums_cache)
                    create_backup(album_json_path)
                    _write_atomic(album_json_path, album_data)
                    removed_from_json = True
                    # Die Elementliste hat sich geaendert: Albumliste (30 s
                    # TTL) und Suchindex wuerden sonst Geloeschtes zeigen.
                    invalidate_albums_cache()
                    events.emit("album_changed")
            except Exception as e:
                logger.warning("album.json update after delete failed %s: %s", album_name, e)

        logger.info(
            f"File deleted: {space}/{album_name}/{filename} "
            f"(recycle: {recycled}, .bak siblings: {removed_baks}, json-purge: {removed_from_json})"
        )
        # Timeline-Index listet auch unreferenzierte Dateien → verwerfen
        invalidate_timeline_index()

        return {
            "deleted"         : True,
            "filename"        : filename,
            "recycled"        : recycled,
            "was_thumbnail"   : was_thumbnail,
            "removed_from_json": removed_from_json,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"delete_photo_file error '{album_name}/{filename}': {e}", exc_info=True)
        raise HTTPException(500, _t("common.error_generic", request, {"msg": str(e)}))


# ---------------------------------------------------------------------------
# Upload — Kamera/Upload der nativen App (additiv 09/2026)
#
# Roher Body statt Multipart: keine neue Abhängigkeit (python-multipart),
# OkHttp streamt PUT-Bytes direkt, und der Server schreibt atomar
# (tmp + os.replace). Vertrag: docs/API_CHANGES.md.
# ---------------------------------------------------------------------------

def _same_bytes(a: Path, b: Path) -> bool:
    """Bytegleich? Erst Groesse, dann Inhalt in Bloecken — ohne die ganze
    Datei in den Speicher zu heben (Videos)."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ca, cb = fa.read(1 << 20), fb.read(1 << 20)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


def _free_name(dst_dir: Path, filename: str) -> str:
    """Naechster freier Name bei Kollision: name_1.ext, name_2.ext …
    (Regel vom 06.09.2026 fuer den Upload-Block: '_1 bei Kollision')."""
    stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 1
    while (dst_dir / f"{stem}_{n}{suffix}").exists():
        n += 1
    return f"{stem}_{n}{suffix}"


def _discard(file_path: Path, base: Path, album_dir: Path) -> bool:
    """Datei in den Papierkorb der Freigabe legen (wie beim Loeschen),
    sonst endgueltig entfernen. True = im Papierkorb."""
    recycle_dir = find_recycle_dir(base)
    if recycle_dir is not None:
        try:
            ziel_dir = recycle_target_dir(recycle_dir, album_dir)
            ziel_dir.mkdir(parents=True, exist_ok=True)
            ziel = ziel_dir / file_path.name
            if ziel.exists():
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                ziel = ziel_dir / f"{file_path.stem}_{stamp}{file_path.suffix}"
            shutil.move(str(file_path), str(ziel))
            return True
        except OSError as e:
            logger.warning("Papierkorb nicht nutzbar (%s), loesche endgueltig: %s", recycle_dir, e)
    try:
        file_path.unlink()
    except OSError as e:
        logger.warning("Dublette nicht loeschbar: %s: %s", file_path, e)
    return False


def _resolve_collision(src: Path, dst_dir: Path, filename: str):
    """Was tun, wenn im Ziel schon eine Datei dieses Namens liegt?

    Anlass 08.09.2026: Beim Einsortieren aus dem Durchlauf meldete der Server
    meldete 'existiert im Ziel bereits', die Datei blieb im Durchlauf —
    eine Sackgasse, aus der nur der Dateibrowser fuehrte. Regel wie beim
    Upload-Block (06.09.): bytegleich = Dublette, wird aufgeraeumt;
    anderer Inhalt = neuer Name mit _1.

    Rueckgabe: ("free", filename) | ("duplicate", filename) | ("renamed", neuer_name)
    """
    dst = dst_dir / filename
    if not dst.exists():
        return ("free", filename)
    # DERSELBE Ordner (lose Datei im Albumordner, Ziel = dieses Album):
    # kein Duplikat, sondern die Datei selbst. Vorfall 08.09.2026, 14:10:
    # _same_bytes(src, src) war True, die Datei wanderte in den Papierkorb,
    # und ein Element zeigte ins Leere. Aus dem Korb zurueckgeholt.
    try:
        if src.resolve() == dst.resolve():
            return ("same", filename)
    except OSError:
        pass
    if _same_bytes(src, dst):
        return ("duplicate", filename)
    return ("renamed", _free_name(dst_dir, filename))


def _append_media_element(album_json_path: Path, filename: str, suffix: str,
                          context: str, request: Request = None) -> dict:
    """Datei als photo/video-Element an eine album.json anhängen — Backup,
    atomares Schreiben, Cache-Invalidierung wie beim Album-Speichern.
    Gemeinsam genutzt von Upload (add_to_album) und Einsortieren (import)."""
    from routers.albums import create_backup, _write_atomic, invalidate_albums_cache
    try:
        with open(album_json_path, encoding="utf-8") as f:
            album_data = json.load(f)
        existing = [
            int(e["id"]) for e in album_data.get("elements", [])
            if e.get("id") and str(e["id"]).isdigit()
        ]
        elem = {
            "id"  : str(max(existing, default=0) + 1).zfill(4),
            "type": "photo" if suffix in _PHOTO_EXTS else "video",
            "file": filename,
        }
        album_data.setdefault("elements", []).append(elem)
        album_data.setdefault("meta", {})["modified"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        create_backup(album_json_path)
        _write_atomic(album_json_path, album_data)
        invalidate_albums_cache()
        events.emit("album_changed")
        return elem
    except Exception as e:
        # Die Datei liegt korrekt im Zielordner; nur das Anhängen scheiterte.
        logger.error("%s: Element-Anhängen fehlgeschlagen: %s", context, e)
        raise HTTPException(500, _t("album.entry_failed", request))


def _warm_in_background(path: Path) -> None:
    """Thumbs (alle Größen) nach Upload/Einsortieren sofort erzeugen —
    fire-and-forget im Threadpool; Fehler loggt warm_thumbs selbst."""
    import asyncio
    from core.thumb_pipeline import warm_thumbs
    try:
        asyncio.get_running_loop()
        asyncio.create_task(asyncio.to_thread(warm_thumbs, path))
    except RuntimeError:  # kein Loop (Tests/Sync-Kontext): synchron
        warm_thumbs(path)


_UPLOAD_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB — deckt auch lange Videos
_UPLOAD_CHUNK_LOG = 256 * 1024 * 1024       # Fortschritts-Log alle 256 MB

# Strenger als die Bestands-Validierung beim Album-Speichern (albums.py):
# Uploads erzeugen NEUE Dateien — kein Altbestand, den man schonen müsste.
# Kein führender Punkt, kein Markup, keine Pfadzeichen, max. 255 Zeichen.
_UPLOAD_NAME_RE = re.compile(r"^[^<>\"'/\\\x00-\x1f\s.][^<>\"'/\\\x00-\x1f]{0,254}$")


@router.put("/api/album/{space}/{album_name}/file/{filename}")
async def upload_file(
    request: Request,
    space: str,
    album_name: str,
    filename: str,
    add_to_album: bool = Query(False),
):
    """Mediendatei in einen Album-/Eingangsordner hochladen.

    - Ordner ohne album.json sind erlaubt und werden bei Bedarf angelegt
      (Durchlauf-Ordner = nackte Jahreszahl "2026"; Jahreszahl + Name
      wäre ein Album) — die Datei erscheint dann in der Timeline mit
      referenced:false.
    - add_to_album=true hängt die Datei zusätzlich als Element an die
      album.json an (Backup + atomares Schreiben wie beim Album-Speichern);
      erfordert ein existierendes Album.
    """
    from routers.albums import _is_safe_album_name  # lazy: kein Import-Zyklus

    session = require_session(request)
    # Beitrags-Recht statt Kurator-Recht: Editoren laden auch in den
    # Shared-Durchlauf (Kuratier-Modell C+, 02.09.2026)
    if not can_contribute(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))

    if not _is_safe_album_name(album_name):
        raise HTTPException(400, _t("common.invalid_path", request))
    if not _UPLOAD_NAME_RE.match(filename) or ".." in filename:
        raise HTTPException(400, _t("album.invalid_filename", request))
    suffix = Path(filename).suffix.lower()
    if suffix not in _MEDIA_EXTS:
        raise HTTPException(415, _t("album.only_photos_allowed", request, {"exts": ", ".join(sorted(_MEDIA_EXTS))}))

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _UPLOAD_MAX_BYTES:
        raise HTTPException(413, _t("album.upload_too_large", request))

    base      = get_path(session, space).resolve()
    album_dir = (base / album_name).resolve()
    if not album_dir.is_relative_to(base):
        raise HTTPException(400, _t("common.invalid_path", request))

    # Eingangsordner-Fall: Ordner bei Bedarf anlegen (eine Ebene, kein parents)
    if not album_dir.exists():
        album_dir.mkdir(parents=False)
        logger.info("Upload: Ordner angelegt: %s/%s", space, album_name)
    elif not album_dir.is_dir():
        raise HTTPException(400, _t("common.invalid_path", request))

    dest = album_dir / filename
    if dest.exists():
        raise HTTPException(409, _t("album.file_exists", request, {"file": filename}))

    # Streamen nach tmp im Zielordner, dann atomar umbenennen — ein
    # Verbindungsabbruch hinterlässt nie eine halbe Zieldatei.
    size = 0
    fd, tmp_path = tempfile.mkstemp(dir=album_dir, prefix=".upload_tmp.", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > _UPLOAD_MAX_BYTES:
                    raise HTTPException(413, _t("album.upload_too_large", request))
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, _t("album.upload_empty", request))
        os.replace(tmp_path, dest)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    elem = None
    if add_to_album:
        album_json_path = album_dir / "album.json"
        if not album_json_path.exists():
            raise HTTPException(400, _t("album.upload_needs_album", request))
        elem = _append_media_element(album_json_path, filename, suffix,
                                     context=f"Upload {album_name}", request=request)

    # Performance-Paket 03.09.2026: Thumbs sofort im Hintergrund erzeugen
    # (Google-Muster: alles entsteht beim Ingest, nichts beim Anschauen)
    if suffix in _PHOTO_EXTS:
        _warm_in_background(dest)
    invalidate_timeline_index()
    from core.thumb_pipeline import etag_for
    thumbnails, original = media_urls(space, album_name, filename,
                                      version=etag_for(dest)[:16])
    logger.info("Upload: %s/%s/%s (%d Bytes%s)", space, album_name, filename,
                size, ", ins Album" if add_to_album else "")
    return {
        "success"   : True,
        "filename"  : filename,
        "size"      : size,
        "type"      : "photo" if suffix in _PHOTO_EXTS else "video",
        "element"   : elem,
        "thumbnails": thumbnails,
        "original"  : original,
        # Format-Haelfte der Bearbeitbarkeit, wie in der Album-Antwort.
        "editable_format": is_editable(filename),
    }


# ---------------------------------------------------------------------------
# Einsortieren — Datei aus dem Eingangsordner in ein Album verschieben
# (Kuratier-Fluss der nativen App, additiv 09/2026)
#
# Modell (02.09.2026): Jedes Foto WOHNT in genau einem Album-/
# Ordner (Invariante); Einsortieren = physisches Verschieben ins Zielalbum
# plus Element-Eintrag. source_album-Referenzen bleiben das Werkzeug für
# Auftritte in weiteren Alben (Best-ofs), nicht für den Standardfluss.
# ---------------------------------------------------------------------------

class ImportFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_space: str = Field(pattern="^(shared|personal)$")
    from_album: str = Field(min_length=1)
    filename:   str = Field(min_length=1)


class YearAlbumRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: str = Field(pattern=r"^\d{4}$")
    name: Optional[str] = None      # Zielalbum, Vorgabe "<year> - das Jahr"


@router.post("/api/album/{space}/year-album")
async def year_album(request: Request, space: str, body: YearAlbumRequest):
    """„Rest ins Jahresalbum" (Kuratier-Modell 02.09.2026, gebaut 08.09.):
    Alle Mediendateien aus dem Durchlauf `<year>` in das Album
    `<year> - das Jahr` verschieben und als Elemente anhaengen, in
    Aufnahmereihenfolge. Fehlt das Album, wird es angelegt.

    Warum ueberhaupt: Memories, Suche und die Startseite lesen nur
    album.json. Was im Durchlauf liegt, gibt es fuer sie nicht — „heute
    vor drei Jahren" kann ein Kamerafoto erst zeigen, wenn es in einem
    Album steht. Der Knopf macht aus dem Rest eines Jahres ein Album.

    Rechte: Anhaengen ist Beitrag (can_contribute), Anlegen ist
    Kuratieren (is_readonly) — nur wenn das Album fehlt, braucht es
    das Kuratorrecht. Bestehende Dateien gleichen Namens im Ziel werden
    uebersprungen und gezaehlt, nicht ueberschrieben.

    Keine Vorschau-Erzeugung hier: ein Jahresrest sind Hunderte Dateien,
    das gehoert in den Warmer (naechster Start) oder auf Abruf, nicht
    in den Request.
    """
    from core.exif import filename_datetime
    from routers.albums import (_is_safe_album_name, _extract_year_from_name,
                                _write_atomic, create_backup,
                                invalidate_albums_cache, is_durchlauf)

    session = require_session(request)
    if not can_contribute(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))

    name = (body.name or f"{body.year} - das Jahr").strip()
    if not _is_safe_album_name(name) or is_durchlauf(name):
        raise HTTPException(400, _t("album.invalid_folder_name", request))

    base    = get_path(session, space).resolve()
    src_dir = (base / body.year).resolve()
    dst_dir = (base / name).resolve()
    if not src_dir.is_relative_to(base) or not dst_dir.is_relative_to(base):
        raise HTTPException(400, _t("common.invalid_path", request))
    if not src_dir.is_dir():
        raise HTTPException(404, _t("album.durchlauf_missing", request, {"year": body.year}))

    album_json_path = dst_dir / "album.json"
    created = False
    if album_json_path.is_file():
        with open(album_json_path, encoding="utf-8") as f:
            album_data = json.load(f)
    else:
        if is_readonly(session, space):
            raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))
        if dst_dir.exists():
            # Ordner ohne album.json: ein Scan-Kandidat, den jemand
            # bewusst so liegen hat — nicht stillschweigend uebernehmen.
            raise HTTPException(409, _t("album.folder_already_exists", request, {"folder": name}))
        dst_dir.mkdir(parents=False)
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        album_data = {
            "version": "1.4",
            "meta": {
                "title": name, "description": "",
                "year": _extract_year_from_name(name),
                "thumbnail": "", "created": now, "modified": now,
                "show_locked": False,
            },
            "elements": [],
        }
        created = True

    # Aufnahmereihenfolge: Zeitstempel im Dateinamen, sonst mtime.
    def _wann(entry):
        p = src_dir / entry["file"]
        dt = filename_datetime(entry["file"])
        if dt is not None:
            return (0, dt.timestamp(), entry["file"])
        try:
            return (1, p.stat().st_mtime, entry["file"])
        except OSError:
            return (2, 0.0, entry["file"])

    files = sorted(unassigned_in(src_dir, {}), key=_wann)
    existing = [int(e["id"]) for e in album_data.get("elements", [])
                if e.get("id") and str(e["id"]).isdigit()]
    next_id = max(existing, default=0) + 1
    moved, skipped, duplicates, renamed = [], [], [], []
    referenced = {e.get("file") for e in album_data.get("elements", []) if not e.get("source_album")}
    for entry in files:
        src = src_dir / entry["file"]
        fate, final_name = _resolve_collision(src, dst_dir, entry["file"])
        if fate == "duplicate":
            # Bytegleich im Ziel: Quelle in den Papierkorb, Element sicherstellen
            _discard(src, base, src_dir)
            duplicates.append(entry["file"])
            if entry["file"] not in referenced:
                album_data.setdefault("elements", []).append({
                    "id": str(next_id).zfill(4), "type": entry["type"], "file": entry["file"],
                })
                next_id += 1
                referenced.add(entry["file"])
            continue
        if fate == "renamed":
            renamed.append((entry["file"], final_name))
        dst = dst_dir / final_name
        try:
            shutil.move(str(src), str(dst))
        except OSError as e:
            logger.warning("Jahresalbum: %s nicht verschiebbar: %s", src, e)
            skipped.append(entry["file"])
            continue
        album_data.setdefault("elements", []).append({
            "id": str(next_id).zfill(4), "type": entry["type"], "file": final_name,
        })
        next_id += 1
        referenced.add(final_name)
        moved.append(final_name)

    meta = album_data.setdefault("meta", {})
    if moved or created or duplicates:
        if not meta.get("thumbnail"):
            first_photo = next((e["file"] for e in album_data["elements"] if e.get("type") == "photo"), None)
            if first_photo:
                meta["thumbnail"] = first_photo
        meta["modified"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        if not created:
            create_backup(album_json_path)
        _write_atomic(album_json_path, album_data)
        invalidate_albums_cache()
        events.emit("album_changed")
        invalidate_timeline_index()
    logger.info("Jahresalbum %s/%s: %d verschoben, %d Dubletten entfernt, %d umbenannt, %d uebersprungen%s",
                space, name, len(moved), len(duplicates), len(renamed), len(skipped),
                " (neu angelegt)" if created else "")
    return {
        "success"       : True,
        "album"         : name,
        "space"         : space,
        "created"       : created,
        "moved"         : len(moved),
        "duplicates"    : len(duplicates),
        "renamed"       : len(renamed),
        "skipped_exists": len(skipped),   # nur noch: nicht verschiebbar (Dateisystem)
        "elements"      : len(album_data.get("elements", [])),
    }


@router.post("/api/album/{space}/{album_name}/import-file")
async def import_file(request: Request, space: str, album_name: str, body: ImportFileRequest):
    """Verschiebt eine Mediendatei aus einem anderen Ordner (typisch: der
    Kamera-Eingangsordner) in dieses Album und hängt sie als Element an.

    - Quelle und Ziel dürfen in verschiedenen Spaces liegen (Eingang im
      Personal → Familienalbum im Shared); Schreibrechte braucht es für
      BEIDE Spaces (Verschieben entfernt die Datei aus der Quelle).
    - Ist die Datei noch in der album.json der Quelle referenziert → 409
      (erst dort entfernen; die Wohnsitz-Invariante bleibt explizit).
    """
    from routers.albums import _is_safe_album_name  # lazy: kein Import-Zyklus

    session = require_session(request)
    # Beitrags-Recht (C+): Einsortieren/Wiederbeleben dürfen auch Editoren —
    # inklusive Ziel im Shared-Space (Element anhängen ist Beitrag, kein
    # Gestalten). Nötig für BEIDE Spaces (Quelle verliert die Datei).
    if not can_contribute(session, space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": space, "role": session.role}))
    if not can_contribute(session, body.from_space):
        raise HTTPException(403, _t("album.space_readonly", request, {"space": body.from_space, "role": session.role}))

    filename = body.filename.strip()
    if not _is_safe_album_name(album_name) or not _is_safe_album_name(body.from_album):
        raise HTTPException(400, _t("common.invalid_path", request))
    if "/" in filename or "\\" in filename or ".." in filename or filename.startswith("."):
        raise HTTPException(400, _t("album.invalid_filename", request))
    suffix = Path(filename).suffix.lower()
    if suffix not in _MEDIA_EXTS:
        raise HTTPException(415, _t("album.only_photos_allowed", request, {"exts": ", ".join(sorted(_MEDIA_EXTS))}))

    src_base  = get_path(session, body.from_space).resolve()
    dst_base  = get_path(session, space).resolve()
    src_dir   = (src_base / body.from_album).resolve()
    dst_dir   = (dst_base / album_name).resolve()
    if not src_dir.is_relative_to(src_base) or not dst_dir.is_relative_to(dst_base):
        raise HTTPException(400, _t("common.invalid_path", request))

    src = src_dir / filename
    dst = dst_dir / filename
    if not src.is_file():
        raise HTTPException(404, _t("album.file_not_found", request, {"file": filename}))
    album_json_path = dst_dir / "album.json"
    if not album_json_path.is_file():
        raise HTTPException(400, _t("album.import_target_not_album", request))
    # Kollision im Ziel: Dublette aufraeumen oder umbenennen — kein 409
    # mehr (s. _resolve_collision). Die Referenzpruefung der Quelle
    # unten gilt unveraendert.
    fate, final_name = _resolve_collision(src, dst_dir, filename)

    # Wohnsitz-Invariante: referenziert die Quelle die Datei noch selbst?
    src_json = src_dir / "album.json"
    if src_json.is_file():
        try:
            with open(src_json, encoding="utf-8") as f:
                src_data = json.load(f)
            if any(e.get("file") == filename and not e.get("source_album")
                   for e in src_data.get("elements", [])):
                raise HTTPException(409, _t("album.import_source_referenced", request))
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("import-file: Quell-album.json unlesbar (%s): %s", body.from_album, e)

    if fate == "same":
        # Lose Datei im eigenen Albumordner adoptieren: nichts verschieben,
        # nur das Element anhaengen (falls es fehlt).
        with open(album_json_path, encoding="utf-8") as f:
            _dst_data = json.load(f)
        already = next((e for e in _dst_data.get("elements", [])
                        if e.get("file") == filename and not e.get("source_album")), None)
        elem = already if already is not None else _append_media_element(
            album_json_path, filename, suffix, context=f"Import {album_name}", request=request)
        invalidate_timeline_index()
        from core.thumb_pipeline import etag_for
        thumbnails, original = media_urls(space, album_name, filename,
                                          version=etag_for(dst)[:16])
        logger.info("Einsortiert (adoptiert, gleicher Ordner): %s/%s/%s", space, album_name, filename)
        return {
            "success"   : True,
            "filename"  : filename,
            "adopted"   : True,
            "element"   : elem,
            "thumbnails": thumbnails,
            "original"  : original,
            "editable_format": is_editable(filename),
        }

    if fate == "duplicate":
        # Bytegleich im Ziel: die Quelle ist eine Kopie. Sie wandert in den
        # Papierkorb; im Ziel muss nur noch das Element stehen.
        _discard(src, src_base, src_dir)
        with open(album_json_path, encoding="utf-8") as f:
            _dst_data = json.load(f)
        already = next((e for e in _dst_data.get("elements", [])
                        if e.get("file") == filename and not e.get("source_album")), None)
        if already is not None:
            elem = already
        else:
            elem = _append_media_element(album_json_path, filename, suffix,
                                         context=f"Import {album_name}", request=request)
        invalidate_timeline_index()
        from core.thumb_pipeline import etag_for
        thumbnails, original = media_urls(space, album_name, filename,
                                          version=etag_for(dst_dir / filename)[:16])
        logger.info("Einsortiert (Dublette entfernt): %s/%s/%s == %s/%s",
                    body.from_space, body.from_album, filename, space, album_name)
        return {
            "success"   : True,
            "filename"  : filename,
            "duplicate" : True,
            "element"   : elem,
            "thumbnails": thumbnails,
            "original"  : original,
            "editable_format": is_editable(filename),
        }

    renamed_from = None
    if fate == "renamed":
        renamed_from, filename = filename, final_name
        dst = dst_dir / filename

    # Verschieben: rename, über Mount-Grenzen (personal↔shared sind im
    # Container getrennte Bind-Mounts) fällt shutil.move auf copy+delete
    # zurück.
    shutil.move(str(src), str(dst))

    elem = _append_media_element(album_json_path, filename, suffix,
                                 context=f"Import {album_name}", request=request)
    # Fürs Upload-Tages-Digest vermerken (core/notify_jobs, 19:00-Job)
    try:
        from core import notifydb
        notifydb.journal_import(user=session.user, space=space,
                                album=album_name, filename=filename)
    except Exception as e:
        logger.warning("Digest-Journal fehlgeschlagen: %s", e)
    if suffix in _PHOTO_EXTS:
        _warm_in_background(dst)
    invalidate_timeline_index()
    from core.thumb_pipeline import etag_for
    thumbnails, original = media_urls(space, album_name, filename,
                                      version=etag_for(dst)[:16])
    logger.info("Einsortiert: %s/%s/%s → %s/%s%s", body.from_space,
                body.from_album, renamed_from or filename, space, album_name,
                f" (umbenannt: {filename})" if renamed_from else "")
    out = {
        "success"   : True,
        "filename"  : filename,
        "element"   : elem,
        "thumbnails": thumbnails,
        "original"  : original,
        "editable_format": is_editable(filename),
    }
    if renamed_from:
        out["renamed_from"] = renamed_from
    return out
