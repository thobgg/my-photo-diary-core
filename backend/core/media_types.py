"""
core/media_types.py
Die EINE Liste der Dateiendungen, die MPD kennt — und was sie bedeuten.

Warum es diese Datei gibt (07.09.2026): Dieselbe Foto-Menge stand an vier
Stellen — `routers/deps.py`, `core/filesystem_photos.py`,
`stats_scanner.py` und als Literal mitten in `core/library_warmer.py`.
Solange es vier sind, erscheint nach jeder Erweiterung frueher oder
spaeter eine Datei in der Liste, die nicht vorgewaermt wird (oder
umgekehrt) — eine Falle, die einmal zuschlaegt und dann monatelang
niemandem auffaellt.

**Die Lehre vom selben Tag, bitte beim Erweitern beherzigen:** Eine
Endungsmenge und eine MIME-Karte sind zweierlei, und eine
Erlaubnisliste ist wieder etwas anderes. `share.py` hat `_DOC_MIME`
(= was als DOKUMENT durchgeht) als MIME-Auskunft benutzt und deshalb
HEIC-Fotos als `image/jpeg` ausgeliefert. Deshalb steht hier beides
nebeneinander, und ein Test haelt sie deckungsgleich.

Die vier Mengen und ihre Bedeutung:

- `PHOTO_EXTS` / `VIDEO_EXTS` — was MPD ANZEIGT: listen, vorwaermen,
  Vorschaubilder bauen, ausliefern.
- `EDITABLE_EXTS` — was MPD ueberschreiben darf (drehen, zuschneiden,
  Tonwerte). Echte Teilmenge von `PHOTO_EXTS`: Anzeigen ist harmlos,
  Zurueckschreiben nicht.
- `PHOTO_MIME` / `VIDEO_MIME` — wie die Datei beim Ausliefern heisst.
  Deckt die zugehoerige Endungsmenge vollstaendig ab.
"""
import re
from pathlib import Path
from typing import List

# ── Was MPD anzeigt ────────────────────────────────────────────────────
PHOTO_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp",
    # 07.09.2026 dazu, alles ohne neue Abhaengigkeit (Pillow kann es
    # nativ): .tif/.tiff sind das Format eingescannter Papierbilder —
    # ein digitalisiertes Familienarchiv besteht praktisch daraus.
    # .jfif ist eine JPEG-Datei mit anderer Endung.
    ".tif", ".tiff", ".bmp", ".jfif",
}

VIDEO_EXTS = {
    ".mp4", ".mov", ".3gp", ".avi", ".m4v", ".mkv", ".webm",
    # 07.09.2026 dazu. ffmpeg liegt seit jeher im Image (Dockerfile
    # Z. 8), diese Formate kosten also nur den Listeneintrag:
    # .mts/.m2ts ist AVCHD aus Camcordern (Familienarchive 2005-2015).
    ".mts", ".m2ts", ".wmv", ".mpg", ".mpeg",
}

MEDIA_EXTS = PHOTO_EXTS | VIDEO_EXTS

# ── Was MPD ueberschreiben darf ────────────────────────────────────────
# BEWUSST kleiner als PHOTO_EXTS. Die am 07.09. dazugekommenen Formate
# darf MPD zeigen, aber nicht ueberschreiben:
#   .tif  — kann mehrseitig sein. Pillow schriebe beim Speichern EINE
#           Seite zurueck; bei einem eingescannten Album waeren die
#           uebrigen Seiten weg. (Die .bak-Kette holte sie zurueck —
#           aber nur, wer merkt, dass etwas fehlt.)
#   .bmp  — Pillow schreibt es, aber ein BMP mit Alphakanal kaeme ohne
#           zurueck, und in einem Foto-Tagebuch bearbeitet das niemand.
#           Kostet eine Zeile, falls doch mal jemand fragt.
# `.jfif` steht dagegen DRIN (nachgetragen 07.09. auf Hinweis des
# der App): Es IST eine JPEG-Datei, Pillow liest und schreibt sie als
# JPEG, es gibt keinen Mehrseiten- oder Alpha-Grund. Vorsicht ohne Grund
# ist keine Vorsicht, sondern nur eine zweite Regel zum Vergessen.
EDITABLE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".heic", ".gif", ".webp"}

# ── Wie die Datei beim Ausliefern heisst ───────────────────────────────
PHOTO_MIME = {
    ".jpg" : "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png" : "image/png",
    ".heic": "image/heic",
    ".gif" : "image/gif",
    ".webp": "image/webp",
    ".tif" : "image/tiff",
    ".tiff": "image/tiff",
    ".bmp" : "image/bmp",
    ".jfif": "image/jpeg",   # ist eine JPEG-Datei
}

# ── Was MPD sonst noch kennt ───────────────────────────────────────────
AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".wav", ".aac", ".flac"}
# .txt und .md seit 15.09.2026. Zwei Wirkungen auf einmal: Sie werden beim
# Einfuegen angeboten (album_files.py), und sie verschwinden aus der
# Aufraeum-Meldung "kann MPD nicht anzeigen" — denn genau dort tauchten sie
# bisher auf, obwohl sie in Albumordnern voellig normal sind.
DOC_EXTS   = {".pdf", ".txt", ".md"}
GPX_EXTS   = {".gpx"}

#: Begleitdateien: gehoeren zu einem Foto, sind aber selbst keines.
#: Am 07.09.2026 am Bestand gemessen und danach eingefuehrt — ohne sie
#: haette der neue Hinweis im Einfuege-Dialog als Erstes „396 mal .json"
#: gemeldet: Google-Takeout-Beipackzettel
#: (`20231014_043426.jpg.supplemental-metadata.json`), 396 Stueck in
#: einem einzigen Album und 173 in einem zweiten. Technisch waere die
#: Meldung wahr gewesen — MPD kann sie nicht anzeigen — und praktisch
#: irrefuehrend: Es fehlt kein Foto, es liegt nur Beiwerk daneben.
#: Ein Hinweis, der beim ersten Blick 590 Fehlalarme wirft, wird nie
#: wieder gelesen.
SIDECAR_EXTS = {
    ".json",   # Google Takeout, album.json, .mpd_stats.json
    ".xmp",    # Lightroom/darktable
    ".aae",    # Apple: Bearbeitungsschritte neben dem Original
    ".thm",    # Kamera-Vorschau neben dem Video
    ".ini",    # desktop.ini (DSM/Windows)
    ".db",     # Thumbs.db
}

#: Alles, was in einem Albumordner einen Sinn hat. Grundlage dafuer,
#: den Rest zu MELDEN statt ihn wortlos zu uebergehen.
KNOWN_EXTS = MEDIA_EXTS | AUDIO_EXTS | DOC_EXTS | GPX_EXTS | SIDECAR_EXTS

VIDEO_MIME = {
    ".mp4" : "video/mp4",
    ".mov" : "video/quicktime",
    ".m4v" : "video/mp4",
    ".mkv" : "video/x-matroska",
    ".webm": "video/webm",
    ".avi" : "video/x-msvideo",
    ".3gp" : "video/3gpp",
    ".mts" : "video/mp2t",   # AVCHD = MPEG-2-Transportstrom
    ".m2ts": "video/mp2t",
    ".wmv" : "video/x-ms-wmv",
    ".mpg" : "video/mpeg",
    ".mpeg": "video/mpeg",
}


def is_editable(filename) -> bool:
    """Darf MPD DIESE Datei ueberschreiben — rein nach dem Format?

    Getrennt von der Rechtefrage und ausdruecklich nur die halbe Antwort:
    Ob ein bestimmtes Konto ein bestimmtes Foto bearbeiten darf, sagt
    `may_edit_photo()` in routers/deps.py. Beides zusammen entscheidet,
    ob der Stift angeboten werden darf.

    Warum es die Funktion gibt: Am 07.09.2026 wurde zu Recht
    abgelehnt, `EDITABLE_EXTS` in der App nachzubauen. Am 06.09. war
    genau das mit den Rechteregeln passiert — richtig abgeschrieben und
    binnen sechs Stunden auseinandergelaufen. Die gefaehrliche Richtung
    ist dieselbe: faelschlich erlauben. Ein Stift, der in ein 400 fuehrt,
    faellt niemandem auf, der ihn nicht drueckt.
    """
    return Path(str(filename)).suffix.lower() in EDITABLE_EXTS


# ── Was MPD in einem Ordner NICHT anzeigen kann ────────────────────────
# .bak, .bak1 … .bak4 sind die Sicherungskette des Foto-Editors.
_BAK_RE = re.compile(r"\.bak\d*$", re.IGNORECASE)


def unassigned_in(album_dir: Path, album_data: dict) -> List[dict]:
    """Mediendateien im Ordner, auf die kein Element der album.json zeigt —
    das, was beim Kuratieren noch aussteht.

    Eine Quelle fuer den Einfuege-Dialog (/unassigned-photos) und den
    Hinweis in der Album-Antwort (unassigned_count), damit beide dieselbe
    Zahl nennen. Begleitdateien und nicht darstellbare Formate zaehlen
    nicht mit (MEDIA_EXTS); dafuer gibt es unsupported_in()."""
    referenced = {
        e["file"] for e in (album_data or {}).get("elements", [])
        if e.get("type") in ("photo", "video") and "file" in e
    }
    out: List[dict] = []
    try:
        eintraege = sorted(album_dir.iterdir())
    except OSError:
        return out
    for f in eintraege:
        ext = f.suffix.lower()
        if not f.is_file() or f.name.startswith(".") or ext not in MEDIA_EXTS:
            continue
        if f.name in referenced:
            continue
        out.append({"file": f.name, "type": "photo" if ext in PHOTO_EXTS else "video"})
    return out


def unsupported_in(album_dir: Path) -> List[dict]:
    """Was im Ordner liegt und von MPD nicht angezeigt werden kann —
    nach Endung, mit Anzahl.

    Der Grund (07.09.2026): Beim Hochladen sagt der Server sauber nein
    (415, mit Aufzaehlung). Eine Datei, die schon im Ordner LIEGT, wurde
    dagegen wortlos uebersprungen — kein Hinweis, keine Zahl. Fuer
    einen gepflegten Bestand folgenlos; fuer einen Fremden liest es sich
    wie Datenverlust: Der Ordner hat 250 Dateien, MPD zeigt 230, und
    nirgends steht warum. Selbstgehostete Bestaende wachsen ueber SMB
    und File Station, nicht ueber den Upload-Dialog — das ist der
    Normalweg, nicht der Sonderfall.

    Gemeldet wird nach Endung mit Anzahl, nicht Datei fuer Datei: Aus
    „20 Dateien fehlen" wird damit „20 mal .cr2, das kann MPD nicht" —
    aus einem Verlust eine Frage.

    Nicht mitgezaehlt: Verstecktes (`.`-Praefix, darunter
    `.mpd_stats.json` und die Sicherungsordner), `album.json` selbst,
    die `.bak`-Kette des Foto-Editors, die Begleitdateien aus
    SIDECAR_EXTS und alles Uebrige aus KNOWN_EXTS.
    """
    zaehler: dict = {}
    try:
        eintraege = list(album_dir.iterdir())
    except OSError:
        return []
    for f in eintraege:
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.name == "album.json" or _BAK_RE.search(f.name):
            continue
        if f.suffix.lower() in KNOWN_EXTS:
            continue
        endung = f.suffix.lower() or "(ohne Endung)"
        zaehler[endung] = zaehler.get(endung, 0) + 1
    return [{"ext": e, "count": n} for e, n in
            sorted(zaehler.items(), key=lambda kv: (-kv[1], kv[0]))]
