"""
core/notify_jobs.py
My Photo Diary v2 — Benachrichtigungs-Erzeuger (Startpaket 09/2026).

Zwei periodische Jobs, registriert vom Kern-Scheduler (core/scheduler.py):

- Upload-Tages-Digest (täglich DIGEST_HOUR): verdichtet die
  Einsortierungen des Tages (digest_journal) zu je einer Meldung pro
  Album und Verursacher — Empfänger: alle außer dem Verursacher.
  Bewusst am Einsortieren aufgehängt, nicht am Roh-Upload: die Familie
  soll fertige Alben sehen, nicht den Schuhkarton (03.09.2026).
- Kuratier-Wochenbericht (CURATION_DOW/HOUR): „N Dateien im Eingang,
  älteste seit X Tagen" für den Shared-Space — nur wenn N > 0, sonst
  Stille. Empfänger: alle mit Beitrags-Recht (admin + editor).
  Private Eingänge bleiben v1 außen vor (jeder sieht seinen in der
  Timeline selbst).

Adressierung per Fan-out (notifydb.notify_many): eine Zeile je
Empfänger — Pull/Ack der App bleiben unverändert.
"""

import logging
from datetime import datetime

from core import notifydb
from core.userdb import list_users, lookup

log = logging.getLogger(__name__)

# Zeitpunkte (bewusst Konstanten — bei Bedarf später in mpd.env heben)
DIGEST_HOUR     = 19   # täglich 19:00
CURATION_DOW    = "sun"
CURATION_HOUR   = 18   # sonntags 18:00


def _display(user: str) -> str:
    rec = lookup(user)
    return (rec.display_name if rec and rec.display_name else user)


def run_upload_digest() -> int:
    """Tages-Digest der Einsortierungen. Gibt die Zahl der erzeugten
    Meldungen zurück (für Tests)."""
    entries = notifydb.drain_journal()
    if not entries:
        return 0

    users = list_users()
    groups: dict = {}  # (user, space, album) -> [filenames]
    for e in entries:
        groups.setdefault((e["user"], e["space"], e["album"]), []).append(e["filename"])

    sent = 0
    for (actor, space, album), files in groups.items():
        recipients = [u for u in users if u != actor]
        if not recipients:
            continue
        n = len(files)
        word = "Datei" if n == 1 else "Dateien"
        body = f"{_display(actor)} hat {n} {word} in „{album}“ einsortiert"
        notifydb.notify_many(
            recipients = recipients,
            type       = "upload",
            title      = "My Photo Diary",
            body       = body,
            payload    = {
                "space": space, "album": album, "count": n,
                "files": sorted(files)[:20], "actor": actor,
            },
        )
        sent += 1
        log.info("Upload-Digest: %s → %s/%s (%d Dateien, %d Empfänger)",
                 actor, space, album, n, len(recipients))
    return sent


def run_curation_report() -> bool:
    """Wöchentlicher Kuratier-Bericht über den Shared-Eingang.
    True, wenn eine Meldung erzeugt wurde (für Tests)."""
    from config import PHOTO_PATH_SHARED
    from core.timeline_index import get_index

    items = get_index(PHOTO_PATH_SHARED)
    open_items = [i for i in items if not i["referenced"]]
    if not open_items:
        log.info("Kuratier-Bericht: Eingang leer — Stille.")
        return False

    oldest_dt   = min(i["dt"] for i in open_items)
    oldest_days = max(0, (datetime.now() - datetime.fromisoformat(oldest_dt)).days)
    n = len(open_items)

    recipients = [u for u, role in list_users().items() if role in ("admin", "editor")]
    if not recipients:
        return False
    notifydb.notify_many(
        recipients = recipients,
        type       = "curation",
        title      = "My Photo Diary",
        body       = (f"{n} {'Datei wartet' if n == 1 else 'Dateien warten'} "
                     f"im Eingang — die älteste seit {oldest_days} Tagen"),
        payload    = {"count": n, "oldest_days": oldest_days, "space": "shared"},
    )
    log.info("Kuratier-Bericht: %d offen, älteste %d Tage, %d Empfänger",
             n, oldest_days, len(recipients))
    return True


# ── Lizenz: rechtzeitig Bescheid sagen (24.09.2026) ──────────────────────
#
# Die Lizenzpruefung ist seit jeher stumm: Sie schaltet am Stichtag die
# Module ab, und der Erste, der es merkt, ist der Nutzer vor einem
# Schloss — bei Trails sogar das Handy, dessen Standortpunkte der Ingest
# mit 403 abweist. Deshalb meldet der Server vorher.
#
# Gemeldet wird an festen Schwellen, nicht taeglich: 14/7/3/1 Tage vorher,
# am Ablauftag selbst und dann am 1., 15. und letzten Karenztag. Der Job
# laeuft einmal taeglich, jede Schwelle faellt also genau einmal an — ohne
# dass irgendwo gemerkt werden muss, was schon gemeldet wurde. Wer jeden
# Tag dieselbe Meldung bekaeme, liest sie nach drei Tagen nicht mehr.
VORWARN_TAGE = (14, 7, 3, 1)


def run_license_check() -> bool:
    """Taeglich: warnt vor Ablauf und waehrend der Karenz.
    True, wenn eine Meldung erzeugt wurde (fuer Tests)."""
    import config
    from core import license as _license

    if not config.LICENSE_ENFORCE:
        return False                      # nicht scharf → nichts zu melden

    info = _license.license_info()
    if not info["valid"] and not info["in_grace"]:
        return False                      # keine/ungueltige Lizenz: das
                                          # sieht man im Einstellungsdialog
    tage = info["days_left"]
    if tage is None:
        return False                      # unbefristet

    karenz = config.LICENSE_GRACE_DAYS
    if tage in VORWARN_TAGE:
        body = (f"Die Lizenz für MPD Plus läuft in {tage} "
                f"{'Tag' if tage == 1 else 'Tagen'} ab ({info['expires']}).")
    elif tage == 0:
        body = (f"Die Lizenz für MPD Plus läuft heute ab. Die Module bleiben "
                f"noch {karenz} Tage nutzbar." if karenz else
                "Die Lizenz für MPD Plus läuft heute ab.")
    elif karenz and -tage in (1, 15, karenz):
        rest = karenz + tage              # tage ist negativ
        body = (f"Die Lizenz für MPD Plus ist abgelaufen. Die Module laufen "
                f"noch {rest} {'Tag' if rest == 1 else 'Tage'}, dann sind sie "
                f"gesperrt.")
    else:
        return False

    admins = [u for u, role in list_users().items() if role == "admin"]
    if not admins:
        log.warning("Lizenz-Warnung: kein Admin-Konto — niemand zu benachrichtigen")
        return False

    notifydb.notify_many(
        recipients = admins,
        type       = "alert",
        title      = "My Photo Diary",
        body       = body,
        payload    = {"kind": "license", "expires": info["expires"],
                      "days_left": tage, "in_grace": info["in_grace"]},
    )
    log.info("Lizenz-Warnung an %d Admin(s): %s", len(admins), body)
    return True
