"""
diary_memories/scanner.py – "On this day, X years ago" scanner
==============================================================
Searches all PDX albums for photos taken on today's date (MM-DD)
in previous years.

Flow:
  1. Read all album.json files under PHOTO_PATH_SHARED
  2. Per photo: date from filename or EXIF
  3. MM-DD match against target date
  4. Per year: ALL photos, ranked (rank_photos): spread over the day,
     different places first, innerhalb der Gruppe gewuerfelt mit dem
     Datum als Startwert — der Client schneidet mit ?limit= ab, die
     Benachrichtigung nimmt die ersten drei
  5. GPS from EXIF → geocoder → city name
  6. Thumbnail URL from album name + filename (no Synology API call needed)

Result schema (per year block):
  {
    "year"     : 2005,
    "years_ago": 20,
    "photos"   : [
      {
        "filename"     : "2005-03-16_08-30-15.jpg",
        "album"        : "2005 - das Jahr",
        "thumbnail_url": "/api/thumbnail/shared/2005%20-%20das%20Jahr/...",
        "location"     : "Oberstdorf",   ← "" if no GPS
        "time"         : "08:30",        ← None if no time known
      }
    ],
    "total"    : 87                      ← all photos of that day in that year
  }
"""

import hashlib
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import quote

log = logging.getLogger(__name__)

# Path setup so imports work when invoked directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.exif import get_photo_meta_cached, photo_time
from core.thumb_pipeline import etag_for
from core.geocoder import get_location

# ── Rangfolge (08.09.2026) ───────────────────────────
# „Bei Erinnerungen sollten nicht 100 Bilder kommen, sondern eine
# Auswahl." Drei Regeln, in dieser Reihenfolge:
#   1. Nichts verlaesst die NAS, kein Inhalt wird angesehen — nur
#      Zeitstempel und Koordinaten, die der Scanner ohnehin hat.
#   2. Die Auswahl deckt den TAG ab: reihum je ein Foto aus Morgen,
#      Vormittag, Mittag, Nachmittag, Abend, Nacht.
#   3. Wo GPS da ist, verschiedene ORTE zuerst (auf ~500 m gerundet;
#      ohne GPS zaehlt das Album als Ort).
# Der Wuerfel (13.09.2026): Die drei Regeln bleiben, aber WELCHES Foto
# aus einer Gruppe (Tagesabschnitt x Ort) genommen wird, entscheidet ein
# fester Pseudo-Zufall — sonst zeigt der 13. September naechstes Jahr
# dieselben Bilder wie heute. Der Startwert ist das ANGEZEIGTE Datum
# samt Jahr (`scan()` gibt `YYYY-MM-DD|Jahrgang` herein):
#   * innerhalb eines Tages stabil — zweimal oeffnen, zweimal dasselbe,
#     auch wenn der 30-Minuten-Cache in `routers/memories.py` dazwischen
#     ablaeuft. Deshalb geht KEINE Uhrzeit in den Startwert ein.
#   * von Jahr zu Jahr verschieden — das Jahr steckt im Startwert.
# Ohne `seed` bleibt es bei der alten, rein datengetriebenen Rangfolge
# (Slot-Mitte); davon leben die reinen Tests.
# Vorher stand hier random.sample(candidates, 5) — bei jedem Cache-Ablauf
# ein anderes Bild, also mitten am Tag. Ausdruecklich NICHT als Kriterium:
# die Lage relativ zu Textelementen in der album.json (08.09.2026).
_SLOTS = (  # (Name, von-Stunde inkl., bis-Stunde exkl.), Reihenfolge = Rangfolge
    ("morgen",     6,  9),
    ("vormittag",  9, 12),
    ("mittag",    12, 14),
    ("nachmittag",14, 18),
    ("abend",     18, 24),
    ("nacht",      0,  6),
)


def _slot_of(dt) -> Optional[str]:
    if dt is None:
        return None
    h = dt.hour
    for name, a, b in _SLOTS:
        if a <= h < b:
            return name
    return None


def _place_key(photo: dict):
    gps = photo.get("gps")
    if gps:
        return ("gps", round(gps[0] / 0.005) * 0.005, round(gps[1] / 0.005) * 0.005)
    return ("album", photo.get("album"))


def _dice(seed: str, photo: dict) -> str:
    """Fester Wuerfelwert je Foto und Startwert.

    Bewusst ein Hash je Foto statt `random.shuffle` ueber die Liste:
    das Ergebnis haengt nicht an der Reihenfolge der Eingabe, nicht am
    Zufallsgenerator der Python-Version — und ein Foto mehr im Album
    wuerfelt die anderen nicht neu."""
    raw = f"{seed}|{photo.get('album', '')}/{photo.get('filename', '')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def rank_photos(cands: list, seed: str = "") -> list:
    """Kandidaten eines Tages (ein Jahr) in Rangfolge — siehe Kopf.

    Jeder Kandidat traegt "dt" (datetime mit Uhrzeit oder None), "gps",
    "album", "filename". Reine Funktion, ohne Dateizugriff, testbar.

    `seed` (13.09.2026): nicht leer = innerhalb jeder Gruppe wird
    gewuerfelt, gleicher Startwert = gleiche Reihenfolge. Leer = die
    alte Rangfolge nach Slot-Mitte."""
    slots = {name: [] for name, _, _ in _SLOTS}
    slots["unbekannt"] = []
    for p in cands:
        slots[_slot_of(p.get("dt")) or "unbekannt"].append(p)

    def _mid(name):
        for n, a, b in _SLOTS:
            if n == name:
                return (a + b) * 30  # Minuten seit Mitternacht, Slot-Mitte
        return 0

    for name, lst in slots.items():
        if seed:
            # Gewuerfelt: welches Foto der Gruppe vorn steht, entscheidet
            # der Startwert des Tages — nicht die Uhrzeit.
            lst.sort(key=lambda p: _dice(seed, p))
        else:
            mid = _mid(name)
            lst.sort(key=lambda p: (
                abs((p["dt"].hour * 60 + p["dt"].minute) - mid) if p.get("dt") else 0,
                p.get("filename", ""),
            ))

    order = [n for n, _, _ in _SLOTS] + ["unbekannt"]
    ranked, seen_places = [], set()
    remaining = sum(len(l) for l in slots.values())
    while remaining:
        for name in order:
            lst = slots[name]
            if not lst:
                continue
            # Ein Foto von einem noch nicht vertretenen Ort zuerst; sonst
            # das erste der Liste — also das gewuerfelte bzw. ohne
            # Startwert das der Slot-Mitte naechste.
            pick = next((p for p in lst if _place_key(p) not in seen_places), lst[0])
            lst.remove(pick)
            seen_places.add(_place_key(pick))
            ranked.append(pick)
            remaining -= 1
    return ranked


# ENTSCHEIDUNG (präzisiert 03.09.2026): Die Memories-
# Quelle ist eine PRO-NUTZER-Einstellung (users.json: memories_scope
# shared|personal, Default shared). Die frühere Shared-only-Festlegung
# galt Alt-Bestaenden (Fotos mit geschätzten Aufnahmedaten) — für
# Konten mit sauberen EXIF-Daten (Sohn) ist der Personal-Scan gewollt.
# (Kennzeichnen geschätzter Daten bleibt vertagt: Der EXIF-Cache
# speichert nur das Datum, nicht seine Quelle.)


def scan(
    photo_base: Path,
    target_date: Optional[date] = None,
    current_year: Optional[int] = None,
    url_space: str = "shared",
) -> list:
    """
    Main function – returns a list of year blocks, sorted oldest year
    first (largest years_ago).

    Thumbnail URLs are built directly from album name + filename;
    the scanner no longer needs Photos API access.
    """
    if target_date is None:
        target_date = date.today()
    if current_year is None:
        current_year = date.today().year

    target_md = (target_date.month, target_date.day)
    log.info("Scanner: searching photos for %02d-%02d", *target_md)

    # Search all albums
    results_by_year = {}  # year → list of photo dicts

    for album_dir in sorted(photo_base.iterdir()):
        if not album_dir.is_dir():
            continue

        album_json = album_dir / "album.json"
        if not album_json.exists():
            continue

        album_name = album_dir.name

        try:
            with open(album_json, "r", encoding="utf-8") as f:
                album_data = json.load(f)
        except Exception as e:
            log.warning("album.json read error '%s': %s", album_name, e)
            continue

        elements = album_data.get("elements", [])
        photo_elements = [
            e for e in elements
            if e.get("type") == "photo" and e.get("file")
        ]

        if not photo_elements:
            continue

        # Photos in the album that match the target date
        matches = []
        for elem in photo_elements:
            filepath = album_dir / elem["file"]
            if not filepath.exists():
                continue

            meta = get_photo_meta_cached(filepath, album_dir)
            if meta["date"] is None:
                continue

            photo_date = meta["date"]

            # No match if same year as today
            if photo_date.year == current_year:
                continue

            # MM-DD match
            if (photo_date.month, photo_date.day) != target_md:
                continue

            matches.append({
                "filename" : elem["file"],
                "album"    : album_name,
                "year"     : photo_date.year,
                "gps"      : meta["gps"],
                "filepath" : filepath,
                # Uhrzeit nur fuer Treffer (wenige je Tag) — s. photo_time
                "dt"       : photo_time(filepath),
            })

        if not matches:
            continue

        log.info("Album '%s': %d hits for %02d-%02d",
                 album_name, len(matches), *target_md)

        # Group by year
        for match in matches:
            year = match["year"]
            if year not in results_by_year:
                results_by_year[year] = []
            results_by_year[year].append(match)

    if not results_by_year:
        log.info("No hits for %02d-%02d", *target_md)
        return []

    # Per year: ALL photos, ranked (see rank_photos). The API cuts with
    # ?limit=, the notification takes the first three.
    #
    # Startwert des Wuerfels: das ANGEZEIGTE Datum samt Jahr. `target_date`
    # traegt immer das laufende Jahr (auch bei ?date=MM-DD, siehe
    # routers/memories.py) — damit ist die Auswahl den ganzen Tag stabil und
    # am selben Kalendertag naechstes Jahr eine andere. Keine Uhrzeit, sonst
    # wechselte sie beim Ablauf des 30-Minuten-Caches mitten am Tag.
    day_seed = target_date.isoformat()
    output = []

    for year in sorted(results_by_year.keys()):
        candidates = results_by_year[year]
        # Jahrgang mit hinein: die Jahresbloecke wuerfeln unabhaengig.
        selected = rank_photos(candidates, seed=f"{day_seed}|{year}")

        # Thumbnail URLs + geocoding
        enriched = []

        for photo in selected:
            album_enc    = quote(photo["album"])
            filename_enc = quote(photo["filename"])

            thumbnail_url = (
                f"/api/memories-thumb/{album_enc}/{filename_enc}?size=m"
                f"&v={etag_for(photo['filepath'])[:16]}"
            )
            # Personal-Scan: space-Parameter, damit memories-thumb im
            # richtigen (eigenen) Space auflöst. Shared-URLs bleiben
            # byte-identisch zum Bestand.
            if url_space != "shared":
                thumbnail_url += f"&space={url_space}"

            # GPS → city name
            location = ""
            if photo["gps"]:
                city = get_location(photo["gps"][0], photo["gps"][1])
                location = city or ""

            enriched.append({
                "filename"     : photo["filename"],
                "album"        : photo["album"],
                "thumbnail_url": thumbnail_url,
                "location"     : location,
                "lat"          : photo["gps"][0] if photo["gps"] else None,
                "lon"          : photo["gps"][1] if photo["gps"] else None,
                "time"         : photo["dt"].strftime("%H:%M") if photo.get("dt") else None,
            })

        years_ago = current_year - year

        output.append({
            "year"     : year,
            "years_ago": years_ago,
            "photos"   : enriched,
            "total"    : len(enriched),
        })

    # Oldest first
    output.sort(key=lambda x: x["years_ago"], reverse=True)

    log.info("Scanner: %d year blocks found", len(output))
    return output


# ═══════════════════════════════════════════════════════════════════════════
# CLI test  (python3 diary_memories/scanner.py [MM-DD])
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )

    # Load config
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from config import PHOTO_PATH_SHARED

    # Date from argument, or today
    if len(sys.argv) > 1:
        try:
            m, d   = sys.argv[1].split("-")
            target = date(date.today().year, int(m), int(d))
        except ValueError:
            print("Format: MM-DD  (e.g. 03-16)")
            sys.exit(1)
    else:
        target = date.today()

    print(f"Searching photos for: {target.strftime('%d %B')} (all years)\n")

    results = scan(
        photo_base   = PHOTO_PATH_SHARED,
        target_date  = target,
        current_year = date.today().year,
    )

    if not results:
        print("No memories found for this day.")
    else:
        for block in results:
            print(f"\nVor {block['years_ago']} Jahren ({block['year']}):")
            for p in block["photos"]:
                loc = f" – {p['location']}" if p["location"] else ""
                print(f"  📷 {p['filename']}{loc}")
