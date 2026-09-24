"""
core/retention.py

Ein Muster fuer alle Ablagen, die von selbst wachsen — E1 aus
`docs/specs/DATEIABLAGE.md`.

Vorher standen im Haus vier Regeln nebeneinander: rollend nach Anzahl bei
den Album-Sicherungen, nach Ablaufzeit bei den Sitzungen, nach Alter beim
geplanten Zugriffsprotokoll — und **gar keine** beim Vorschau-Zwischen-
speicher und bei den Trails-Sicherungspunkten. Genau die beiden letzten
wachsen deshalb unbegrenzt; ein Sicherungspunkt ist eine vollstaendige
Kopie des Stores — bei langer Historie mehrere hundert Megabyte —, und
verwaiste Vorschaubilder werden seit jeher nie geloescht.

Hier stehen **zwei** Mechanismen, weil es zwei Arten von Ablagen gibt —
das Papier wirft sie zusammen, der Code darf das nicht:

1. `prune_dir()` fuer **Staende, die man aufhebt** (Album-Sicherungen,
   Trails-Sicherungspunkte, spaeter das Zugriffsprotokoll).
   Behalten wird, was zu den N neuesten gehoert UND juenger als D Tage
   ist; beides einzeln abschaltbar (0 = keine Grenze). Der **juengste
   Stand bleibt immer** — eine Aufraeumregel, die eine Ablage leeren
   kann, ist keine Aufraeumregel.

2. `sweep_thumb_cache()` fuer den **Vorschau-Zwischenspeicher**.
   Dort waere „die 30 neuesten behalten" sinnlos: Jede Datei gehoert zu
   genau einem Foto, und die Frage ist nicht ihr Alter, sondern ob das
   Foto noch existiert. Der Schluessel ist `sha1(Pfad + mtime_ns)` — nach
   jeder Bearbeitung und jeder Umbenennung zeigt der alte Eintrag ins
   Leere und bleibt liegen. Gekehrt wird deshalb gegen die Menge der
   gueltigen Schluessel, nicht gegen die Uhr.

   Das Kehren ist der gefaehrliche Teil: Wer die Schluesselmenge
   unvollstaendig bildet, loescht gueltige Vorschaubilder. Drei Riegel:
   fehlt einer der konfigurierten Bestaende auf der Platte, wird gar
   nicht gekehrt; ist die Schluesselmenge leer, ebenfalls nicht; und
   frisch geschriebene Dateien sind durch eine Schonfrist geschuetzt
   (sonst raeumt der Kehrer weg, was gerade entsteht).
   Deshalb ist es per Vorgabe **aus** und meldet nur, was es taete
   (`MPD_THUMB_SWEEP=1` schaltet scharf).

Nicht angefasst: der zweite Thumbnail-Satz unter `thumb-cache/fs/`
(Pipeline B in `core/filesystem_photos.py`, eigene Groessen, eigener
Ordner).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable, List, Set, Tuple

# ── Was in dieses Log NICHT hineingehoert (07.09.2026) ─────────────────
# Der Aufraeumlauf geht ueber ALLE Bestaende, auch ueber die
# persoenlichen Bereiche anderer Menschen. Album- und Dateinamen von dort
# duerfen NICHT ins Log: Die Datei liegt im Repo, geht jede Nacht in die
# Sicherung und wird von Menschen gelesen, die auf diese Bereiche keinen
# Zugriff haben — und niemand kann eine Freigabe dafuer erteilen, denn es
# sind nicht die eigenen Daten.
#
# Deshalb tragen die Treffer ein `shared`-Merkmal, und benannt wird nur,
# was aus dem Familienbestand stammt. Aus persoenlichen Bereichen geht
# ausschliesslich eine Anzahl ins Log, ohne Bestand, ohne Album, ohne
# Nutzer. Der Anlass war ein Fehler in genau diese Richtung: Beim Zaehlen
# der Dateiformate „fiel nebenbei mit heraus", was in einem fremden
# Bereich liegt — und wurde weitergegeben. Eine Auswertung ueber alle
# Bestaende ist genau der Weg, auf dem das unbemerkt passiert.

import config

logger = logging.getLogger(__name__)

_DAY = 86400.0


def _mtime(f: Path) -> float:
    try:
        return f.stat().st_mtime
    except OSError:
        return 0.0


def _size(f: Path) -> int:
    try:
        return f.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# 1. Staende, die man aufhebt: N neueste UND Hoechstalter
# ---------------------------------------------------------------------------

def _album_dirs(base: Path) -> List[Path]:
    """Die Ordner eines Bestands, die ueberhaupt Alben sein koennen.

    Uebersprungen wird, was im ganzen Haus als „kein Inhalt" gilt:
    Synology-Beiwerk (`@eaDir`, `#recycle`) und alles mit fuehrendem Punkt
    — dieselbe Regel wie in `routers/albums.py`, `core/timeline_index.py`,
    `core/library_warmer.py` und `core/media_types.py`.

    Nachgezogen am 15.09.2026: `find_unsupported()` kannte sie als
    einzige nicht und meldete daraufhin den Inhalt der Rettungskopie
    (`.mpd-keep`) als „kann MPD nicht anzeigen" — 3x LIESMICH.txt,
    2 Datenbanken, eine env-Datei. Ein Aufraeum-Bericht, der ueber die
    Sicherung klagt, erzieht zum Wegsehen.
    """
    try:
        return [d for d in sorted(base.iterdir())
                if d.is_dir()
                and not d.name.startswith((".", "@"))
                and d.name != "#recycle"]
    except OSError:
        return []


def prune_dir(directory, *, keep_newest: int = 0, max_age_days: int = 0,
              pattern: str = "*", dry_run: bool = False) -> dict:
    """Raeumt einen Ordner nach dem Hausmuster auf.

    Behalten wird, was zu den `keep_newest` neuesten gehoert UND nicht
    aelter als `max_age_days` ist. 0 schaltet die jeweilige Grenze ab.
    Der juengste Eintrag bleibt in jedem Fall stehen.

    Gibt Zahlen zurueck statt zu schweigen — der Nachtjob loggt sie, und
    ein Trockenlauf (`dry_run`) liefert dieselben Zahlen, ohne zu loeschen.
    """
    d = Path(directory)
    out = {"ordner": str(d), "geprueft": 0, "geloescht": 0, "bytes": 0}
    if not d.is_dir():
        return out

    try:
        files = [f for f in d.glob(pattern) if f.is_file()]
    except OSError as e:
        logger.warning("Aufraeumen: %s nicht lesbar: %s", d, e)
        return out

    files.sort(key=_mtime, reverse=True)
    out["geprueft"] = len(files)
    now = time.time()

    for idx, f in enumerate(files):
        if idx == 0:
            continue  # der juengste Stand bleibt immer
        zu_viel = keep_newest > 0 and idx >= keep_newest
        zu_alt = max_age_days > 0 and (now - _mtime(f)) > max_age_days * _DAY
        if not (zu_viel or zu_alt):
            continue
        b = _size(f)
        if not dry_run:
            try:
                f.unlink()
            except OSError as e:
                logger.warning("Aufraeumen: %s nicht loeschbar: %s", f, e)
                continue
        out["geloescht"] += 1
        out["bytes"] += b

    return out


# ---------------------------------------------------------------------------
# 2. Vorschau-Zwischenspeicher: gegen die gueltigen Schluessel kehren
# ---------------------------------------------------------------------------

def known_spaces() -> Tuple[List[Path], List[str]]:
    """Alle Bestaende, in denen Vorschaubilder entstehen koennen.

    Quelle ist `users.json` (der konfigurierte persoenliche Pfad je Konto)
    plus der Familienbestand — nicht die Erkennung aus dem Warmer: die
    verlangt mindestens eine `album.json` und uebersaehe einen Bestand,
    der nur einen Durchlauf-Ordner hat.

    Zweiter Rueckgabewert sind die konfigurierten Pfade, die es auf der
    Platte nicht gibt. Ist die Liste nicht leer, darf nicht gekehrt
    werden — ein nicht eingehaengter Bestand sieht sonst aus wie
    lauter verwaiste Eintraege.
    """
    from core import userdb

    spaces: List[Path] = []
    missing: List[str] = []

    shared = Path(config.PHOTO_PATH_SHARED)
    if shared.is_dir():
        spaces.append(shared)
    else:
        missing.append(str(shared))

    seen = {str(shared)}
    try:
        for username in userdb.list_users():
            rec = userdb.lookup(username)
            if rec is None:
                continue
            p = Path(rec.personal_path)
            if str(p) in seen:
                continue
            seen.add(str(p))
            if p.is_dir():
                spaces.append(p)
            else:
                # Ein Konto ohne angelegten Ordner ist normal (frisch
                # eingerichtet, nie benutzt) — nur wenn dort schon einmal
                # etwas lag, waere das Fehlen verdaechtig. Das koennen wir
                # hier nicht unterscheiden, also zaehlt es als fehlend.
                missing.append(str(p))
    except Exception as e:  # users.json unlesbar → lieber nicht kehren
        logger.warning("Aufraeumen: Kontenliste nicht lesbar: %s", e)
        missing.append("users.json")

    return spaces, missing


def collect_valid_keys(spaces: Iterable[Path]) -> Set[str]:
    """Schluessel aller Mediendateien, die es gibt — eine Ebene unter dem
    Bestand (Album- und Durchlauf-Ordner), so wie Warmer und Zeitstrahl
    auch laufen."""
    from core.thumb_pipeline import etag_for
    # Bewusst aus core, nicht aus routers/deps: core kennt kein FastAPI
    # (ARCHITEKTUR.md Abschnitt 5). Die beiden Listen sind inhaltlich
    # gleich — sie stehen in der Doppelungs-Tabelle.
    from core.filesystem_photos import _MEDIA_EXTS

    keys: Set[str] = set()
    for base in spaces:
        album_dirs = _album_dirs(base)
        if not album_dirs and not base.is_dir():
            logger.warning("Aufraeumen: %s nicht lesbar", base)
            continue
        for album_dir in album_dirs:
            try:
                for f in album_dir.iterdir():
                    if (f.is_file() and not f.name.startswith(".")
                            and f.suffix.lower() in _MEDIA_EXTS):
                        keys.add(etag_for(f))
            except OSError:
                continue
    return keys


def sweep_thumb_cache(*, grace_days: int = None, dry_run: bool = None) -> dict:
    """Kehrt verwaiste Vorschaubilder aus `THUMB_CACHE_DIR`.

    Verwaist heisst: Der Schluessel gehoert zu keiner existierenden Datei
    mehr — das Foto ist geloescht, umbenannt oder bearbeitet worden.
    """
    from core.thumb_pipeline import SIZES

    if grace_days is None:
        grace_days = config.THUMB_SWEEP_GRACE_DAYS
    if dry_run is None:
        dry_run = not config.THUMB_SWEEP_ENABLED

    out = {"geprueft": 0, "verwaist": 0, "geloescht": 0, "bytes": 0,
           "trockenlauf": dry_run}

    spaces, missing = known_spaces()
    if missing:
        out["uebersprungen"] = f"Bestand nicht erreichbar: {', '.join(missing)}"
        logger.warning("Kehren uebersprungen — %s", out["uebersprungen"])
        return out

    keys = collect_valid_keys(spaces)
    if not keys:
        out["uebersprungen"] = "keine gueltigen Schluessel gefunden"
        logger.warning("Kehren uebersprungen — %s", out["uebersprungen"])
        return out
    out["schluessel"] = len(keys)

    cache = Path(config.THUMB_CACHE_DIR)
    now = time.time()
    for sub in list(SIZES) + ["video"]:
        d = cache / sub
        if not d.is_dir():
            continue
        for shard in d.iterdir():
            if not shard.is_dir():
                continue
            for f in shard.iterdir():
                if not f.is_file():
                    continue
                out["geprueft"] += 1
                if f.stem in keys:
                    continue
                out["verwaist"] += 1
                # Schonfrist: was gerade erst entstanden ist, gehoert
                # womoeglich zu einer Datei, die zwischen Schluesselbildung
                # und Kehren dazugekommen ist.
                if (now - _mtime(f)) < grace_days * _DAY:
                    continue
                b = _size(f)
                if not dry_run:
                    try:
                        f.unlink()
                    except OSError as e:
                        logger.warning("Kehren: %s nicht loeschbar: %s", f, e)
                        continue
                out["geloescht"] += 1
                out["bytes"] += b

    return out


# ---------------------------------------------------------------------------
# 3b. Dateien, die MPD nicht anzeigen kann
# ---------------------------------------------------------------------------

def find_unsupported(spaces: Iterable[Path]) -> dict:
    """Wie viele Dateien in den Albumordnern kann MPD nicht anzeigen?

    Gegenstueck zu find_dangling: Dort zeigt ein Element auf eine fehlende
    Datei, hier liegt eine Datei da, zu der es kein Element geben KANN.
    Beides war bisher stumm.

    Zurueck kommt {"gesamt": n, "nach_endung": {".cr2": 12, …},
    "alben": [{"album", "anzahl", "shared"} …]} — die Endungsverteilung
    ist die eigentliche Auskunft: Sie sagt, welches Format sich lohnen
    wuerde.

    `shared` sagt, ob der Treffer aus dem Familienbestand stammt. Es
    entscheidet, ob der Albumname ins Log darf — s. run_housekeeping().
    """
    from core.media_types import unsupported_in

    nach_endung: dict = {}
    alben: List[dict] = []
    gesamt = 0
    for base in spaces:
        for album_dir in _album_dirs(base):
            treffer = unsupported_in(album_dir)
            if not treffer:
                continue
            n = sum(t["count"] for t in treffer)
            gesamt += n
            alben.append({"album": album_dir.name, "anzahl": n,
                          "shared": Path(base) == Path(config.PHOTO_PATH_SHARED)})
            for t in treffer:
                nach_endung[t["ext"]] = nach_endung.get(t["ext"], 0) + t["count"]
    alben.sort(key=lambda a: -a["anzahl"])
    return {"gesamt": gesamt, "nach_endung": nach_endung, "alben": alben}


# ---------------------------------------------------------------------------
# 3. Elemente, die ins Leere zeigen
# ---------------------------------------------------------------------------

_VERWEIS_TYPEN = {"photo", "video", "audio", "document", "tour"}


def find_dangling(spaces: Iterable[Path]) -> List[dict]:
    """Elemente, deren Datei es nicht (mehr) gibt.

    Eine `album.json` darf auf Dateien zeigen, die fehlen — geprueft wird
    das nirgends, auffallen tut es erst als kaputte Kachel. Entstehen kann
    es, wenn jemand am Server vorbei aufraeumt (SMB, File Station) oder
    eine Sicherung unvollstaendig zurueckkommt.

    Gemeldet, NICHT entfernt: Eine fehlende Datei kann eine noch nicht
    zurueckgespielte Sicherung sein. Ein Element zu loeschen, dessen Bild
    vielleicht wiederkommt, waere der schlimmere Fehler — dann waere die
    Stelle im Album verloren, an der es stand.
    """
    import json

    treffer: List[dict] = []
    for base in spaces:
        # Nur Treffer aus dem Familienbestand duerfen ihren Albumnamen
        # ins Log tragen (s. run_housekeeping).
        ist_shared = Path(base) == Path(config.PHOTO_PATH_SHARED)
        for album_dir in _album_dirs(base):
            aj = album_dir / "album.json"
            if not aj.is_file():
                continue
            try:
                data = json.loads(aj.read_text(encoding="utf-8"))
            except Exception as e:
                treffer.append({"album": album_dir.name, "id": None,
                                "shared": ist_shared,
                                "grund": f"album.json unlesbar: {e}"})
                continue
            for elem in data.get("elements", []):
                if elem.get("type") not in _VERWEIS_TYPEN:
                    continue
                datei = elem.get("file")
                if not datei:
                    continue
                # source_album beachten: das Foto wohnt dann woanders
                ort = base / elem["source_album"] if elem.get("source_album") else album_dir
                if not (ort / datei).is_file():
                    treffer.append({"album": album_dir.name,
                                    "id": elem.get("id"),
                                    "typ": elem.get("type"),
                                    "shared": ist_shared,
                                    "grund": "Datei fehlt"})
            cover = (data.get("meta") or {}).get("thumbnail")
            if cover and not (album_dir / cover).is_file():
                treffer.append({"album": album_dir.name, "id": None,
                                "typ": "cover", "shared": ist_shared,
                                "grund": "Cover fehlt"})
    return treffer


# ---------------------------------------------------------------------------
# Nachtjob
# ---------------------------------------------------------------------------

def _mb(b: int) -> str:
    return f"{b / 1024 / 1024:.0f} MB"


def run_housekeeping() -> dict:
    """Taeglicher Aufraeumlauf. Synchron — der Aufrufer schickt ihn in
    einen Thread."""
    ergebnis = {}

    # Trails-Sicherungspunkte: je Nutzer ein Ordner, je Punkt eine
    # vollstaendige Kopie des Stores (schnell mehrere hundert MB).
    snap_root = Path(config.TRAILS_DIR) / "snapshot"
    gesamt = {"geloescht": 0, "bytes": 0}
    if snap_root.is_dir():
        for user_dir in sorted(p for p in snap_root.iterdir() if p.is_dir()):
            r = prune_dir(user_dir,
                          keep_newest=config.TRAILS_SNAPSHOT_KEEP,
                          max_age_days=config.TRAILS_SNAPSHOT_DAYS,
                          pattern="*.sqlite")
            gesamt["geloescht"] += r["geloescht"]
            gesamt["bytes"] += r["bytes"]
    ergebnis["sicherungspunkte"] = gesamt
    if gesamt["geloescht"]:
        logger.info("Aufraeumen: %d Trails-Sicherungspunkte entfernt (%s)",
                    gesamt["geloescht"], _mb(gesamt["bytes"]))

    # Vorschau-Zwischenspeicher kehren (Vorgabe: nur melden).
    try:
        sweep = sweep_thumb_cache()
        ergebnis["vorschau"] = sweep
        if sweep.get("uebersprungen"):
            pass  # schon als Warnung geloggt
        elif sweep["trockenlauf"]:
            logger.info(
                "Aufraeumen (Trockenlauf): %d von %d Vorschaubildern sind "
                "verwaist, %d davon aelter als die Schonfrist — das waeren "
                "%s. Scharfschalten mit MPD_THUMB_SWEEP=1.",
                sweep["verwaist"], sweep["geprueft"], sweep["geloescht"],
                _mb(sweep["bytes"]))
        elif sweep["geloescht"]:
            logger.info("Aufraeumen: %d verwaiste Vorschaubilder entfernt (%s)",
                        sweep["geloescht"], _mb(sweep["bytes"]))
    except Exception as e:
        logger.warning("Kehren fehlgeschlagen: %s", e)

    # Elemente, die ins Leere zeigen — nur melden.
    try:
        spaces, missing = known_spaces()
        if missing:
            ergebnis["leerverweise"] = {"uebersprungen": ", ".join(missing)}
        else:
            tot = find_dangling(spaces)
            ergebnis["leerverweise"] = {"anzahl": len(tot)}
            if tot:
                logger.warning("Aufraeumen: %d Element(e) zeigen ins Leere "
                               "— gemeldet, nicht entfernt", len(tot))
                oeffentlich = [t for t in tot if t.get("shared")]
                privat      = len(tot) - len(oeffentlich)
                for t in oeffentlich[:20]:
                    logger.warning("  %s / Element %s (%s): %s",
                                   t["album"], t.get("id"), t.get("typ"), t["grund"])
                if len(oeffentlich) > 20:
                    logger.warning("  … und %d weitere im Familienbestand",
                                   len(oeffentlich) - 20)
                if privat:
                    logger.warning("  dazu %d in persoenlichen Bereichen "
                                   "(ohne Namen, s. Kopf dieser Datei)", privat)
    except Exception as e:
        logger.warning("Pruefung auf Leerverweise fehlgeschlagen: %s", e)

    # Dateien, die MPD nicht anzeigen kann — nur melden, nie anfassen.
    try:
        spaces, missing = known_spaces()
        if missing:
            ergebnis["nicht_anzeigbar"] = {"uebersprungen": ", ".join(missing)}
        else:
            u = find_unsupported(spaces)
            ergebnis["nicht_anzeigbar"] = u
            if u["gesamt"]:
                verteilung = ", ".join(
                    "%s %s" % (n, e) for e, n in
                    sorted(u["nach_endung"].items(), key=lambda kv: (-kv[1], kv[0]))[:10])
                logger.warning(
                    "Aufraeumen: %d Datei(en) in %d Album(en) kann MPD nicht "
                    "anzeigen — %s. Gemeldet, nicht angefasst.",
                    u["gesamt"], len(u["alben"]), verteilung)
                oeffentlich = [a for a in u["alben"] if a.get("shared")]
                privat = [a for a in u["alben"] if not a.get("shared")]
                for a in oeffentlich[:20]:
                    logger.warning("  %s: %d", a["album"], a["anzahl"])
                if len(oeffentlich) > 20:
                    logger.warning("  … und %d weitere Alben im Familienbestand",
                                   len(oeffentlich) - 20)
                if privat:
                    logger.warning("  dazu %d Datei(en) in %d Album(en) "
                                   "persoenlicher Bereiche (ohne Namen, "
                                   "s. Kopf dieser Datei)",
                                   sum(a["anzahl"] for a in privat), len(privat))
    except Exception as e:
        logger.warning("Pruefung auf nicht anzeigbare Dateien fehlgeschlagen: %s", e)

    return ergebnis
