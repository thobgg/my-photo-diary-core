"""
diary_memories/scheduler.py – daily job
=======================================
Wired up by main.py at startup: `register_jobs()` hängt die Memories-Jobs
an die Kern-Instanz in core/scheduler.py (seit 19.09.2026; vorher lag die
Instanz hier, siehe dort).

Job: daily (configurable via mpd.env: MEMORIES_HOUR, MEMORIES_MINUTE)
  1. scanner.scan() → memories for today (reads photos directly from FS)
  2. Eintrag in den In-App-Benachrichtigungs-Store (core/notifydb) —
     die native App pollt /api/notify/pull und zeigt die Android-
     Benachrichtigung.

ntfy wurde am 03.09.2026 ausgebaut (Container abgeschaltet):
der Notify-Store ist seither der einzige Push-Weg. Der Scheduler läuft
deshalb IMMER — nicht mehr nur, wenn eine ntfy-URL konfiguriert ist.

Scanner needs no Photos API (only FS access to album folders).
"""

import logging
from datetime import date

from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)


def build_memories_message(results: list) -> str:
    """Meldungstext aus den Scan-Ergebnissen (bis 09/2026 in notifier.py,
    mit dem ntfy-Ausbau hierher gezogen)."""
    total_photos = sum(len(b["photos"]) for b in results)
    years        = [b["years_ago"] for b in results]

    if len(years) == 1:
        years_text = f"vor {years[0]} Jahren"
    else:
        years_text = f"vor {min(years)} bis {max(years)} Jahren"

    return f"Heute {years_text} – {total_photos} Erinnerung{'en' if total_photos > 1 else ''} 📷"


def register_jobs(photo_base, hour: int = None, minute: int = None):
    # hour/minute are required — passed from main.py via config.py.
    # None defaults turn a missing arg into a clear error rather than a silent 6:30.
    """Memories-Jobs an den Kern-Scheduler hängen (core/scheduler.py).

    Bis zum 19.09.2026 stand hier die ganze Scheduler-Instanz samt
    Aufräumen, Rettungskopie und Benachrichtigungen — fehlte dieses Modul,
    liefen auch die Kern-Jobs nicht. Seither liegen sie im Kern, und hier
    bleibt nur, was Memories selbst braucht."""
    from core.scheduler import add_job

    add_job(
        func    = _run_daily_job,
        trigger = CronTrigger(hour=hour, minute=minute),
        kwargs  = {"photo_base": photo_base},
        id      = "diary_memories_daily",
        name    = "DiaryMemories – Heute vor X Jahren",
    )
    add_job(
        func    = _prewarm_job,
        trigger = CronTrigger(hour=0, minute=5),
        id      = "memories_prewarm_daily",
        name    = "Memories-Cache-Vorwärmen (Tageswechsel)",
    )
    log.info("DiaryMemories jobs registered – daily at %02d:%02d", hour, minute)


async def _prewarm_job():
    from routers.memories import prewarm_memories
    await prewarm_memories("tageswechsel")


def _store_notification(results, today, recipients) -> None:
    """Scan-Ergebnis ins In-App-Benachrichtigungsregister schreiben
    (native App pollt /api/notify/pull) — gezielt an die übergebenen
    Empfänger (Fan-out)."""
    from core import notifydb

    notifydb.notify_many(
        recipients = recipients,
        type       = "memories",
        title      = "My Photo Diary",
        body       = build_memories_message(results),
        payload    = {
            "deep_link": "mpd://memories",
            "date"     : today.isoformat(),
            "photos"   : sum(len(b["photos"]) for b in results),
            "years_ago": [b["years_ago"] for b in results],
        },
    )
    log.info("In-App-Benachrichtigung (memories, %s) an %d Empfänger",
             today, len(recipients))


async def _run_daily_job(photo_base):
    """Daily job — produziert je nach memories_scope des Nutzers
    (users.json, 03.09.2026): EIN Shared-Scan für alle mit Scope shared,
    dazu je ein Personal-Scan für Nutzer mit Scope personal."""
    from core import settings as _settings
    from core.userdb import list_users, lookup
    from diary_memories.scanner import scan

    today = date.today()
    log.info("DiaryMemories job starting – %s", today.strftime("%d.%m.%Y"))

    if not _settings.get("memories_push_enabled"):
        log.info("Memories push globally disabled – skipped.")
        return

    try:
        shared_rcpt, personal_users = [], []
        for user in list_users():
            rec = lookup(user)
            if rec and rec.memories_scope == "personal":
                personal_users.append(rec)
            else:
                shared_rcpt.append(user)

        results = scan(
            photo_base   = photo_base,
            target_date  = today,
            current_year = today.year,
        )
        if results and shared_rcpt:
            _store_notification(results, today, shared_rcpt)
        elif not results:
            log.info("No shared memories for today")

        for rec in personal_users:
            if not rec.personal_path.is_dir():
                continue
            r = scan(
                photo_base   = rec.personal_path,
                target_date  = today,
                current_year = today.year,
                url_space    = "personal",
            )
            if r:
                _store_notification(r, today, [rec.username])
            else:
                log.info("No personal memories for %s", rec.username)

    except Exception as e:
        log.error("DiaryMemories job error: %s", e, exc_info=True)
