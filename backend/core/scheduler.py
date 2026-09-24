"""
core/scheduler.py — die eine Scheduler-Instanz des Kerns
=========================================================
Wired up by main.py at startup. APScheduler, im selben FastAPI-Prozess.

Kern-Jobs (laufen immer, auch ohne ein einziges Plus-Modul):
  - Upload-Tages-Digest und Kuratier-Wochenbericht (core/notify_jobs.py)
  - Aufräumen nach den Verfallsregeln (core/retention.py)
  - Rettungskopie ausserhalb des Datenordners, nachts und einmal beim
    Start, falls noch keine existiert (core/keep.py)

Module hängen ihre eigenen Jobs über `add_job()` an dieselbe Instanz —
heute nur Memories (diary_memories/scheduler.py: Tagesmeldung,
Cache-Vorwärmen). Bis zum 19.09.2026 lag die ganze Instanz in
diary_memories/; fehlte das Modul, setzte main.py stille Platzhalter ein,
und mit Memories verschwanden auch Aufräumen, Rettungskopie und die
Benachrichtigungen — ohne Fehler, nur mit einer Warnzeile im Log
(docs/specs/VEROEFFENTLICHUNG.md §5, Punkt 7).
"""

import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

log = logging.getLogger(__name__)

_scheduler = None


def start_scheduler() -> AsyncIOScheduler:
    """Kern-Scheduler anlegen, Kern-Jobs eintragen und starten."""
    global _scheduler
    import config as _config
    from core import notify_jobs

    _scheduler = AsyncIOScheduler()

    # Benachrichtigungs-Erzeuger (Startpaket 09/2026).
    _scheduler.add_job(
        func    = notify_jobs.run_upload_digest,
        trigger = CronTrigger(hour=notify_jobs.DIGEST_HOUR, minute=0),
        id      = "notify_upload_digest",
        name    = "Upload-Tages-Digest",
        replace_existing = True,
    )
    _scheduler.add_job(
        func    = notify_jobs.run_curation_report,
        trigger = CronTrigger(day_of_week=notify_jobs.CURATION_DOW,
                              hour=notify_jobs.CURATION_HOUR, minute=0),
        id      = "notify_curation_report",
        name    = "Kuratier-Wochenbericht",
        replace_existing = True,
    )

    # Lizenz-Warnung (24.09.2026). Morgens um 09:00, nicht nachts: Eine
    # Meldung, die um drei Uhr früh entsteht, erreicht das Handy erst beim
    # Aufwachen — und sie ist eine Aufforderung zu handeln, kein Protokoll.
    _scheduler.add_job(
        func    = notify_jobs.run_license_check,
        trigger = CronTrigger(hour=9, minute=0),
        id      = "license_check",
        name    = "Lizenz: Ablauf und Karenz",
        replace_existing = True,
    )

    # Aufräumen (E1, core/retention.py). Bewusst vor dem nächtlichen
    # Trails-Lauf um 04:00.
    _scheduler.add_job(
        func    = _housekeeping_job,
        trigger = CronTrigger(hour=_config.RETENTION_HOUR,
                              minute=_config.RETENTION_MINUTE),
        id      = "housekeeping_retention",
        name    = "Aufräumen (Verfallsregeln)",
        replace_existing = True,
    )

    # Rettungskopie (core/keep.py, docs/specs/DATENMITNAHME.md).
    # Bewusst VOR dem Aufräumen: Erst in Sicherheit bringen, dann wegräumen.
    # Und vor dem Trails-Lauf um 04:00, damit der Sicherungspunkt nicht
    # mitten in den Export fällt.
    _scheduler.add_job(
        func    = _keep_job,
        trigger = CronTrigger(hour=_config.KEEP_HOUR,
                              minute=_config.KEEP_MINUTE),
        id      = "keep_rescue_copy",
        name    = "Rettungskopie ausserhalb des Datenordners",
        replace_existing = True,
    )

    # Start-Lauf, EINMALIG und nur wenn noch keine Kopie existiert.
    # Sonst stuende man nach einer Neuinstallation bis zu einen Tag ohne
    # da — und eine Neuinstallation ist genau der Fall, fuer den die
    # Rettungskopie gedacht ist.
    # Zwei Minuten Verzug: Beim Hochfahren laeuft der Vorwaermer ueber
    # alle Alben; mehrere hundert MB gleichzeitig zu kopieren waere auf
    # einer kleinen NAS unhoeflich.
    _scheduler.add_job(
        func    = _keep_start_job,
        trigger = DateTrigger(run_date=datetime.now() + timedelta(minutes=2)),
        id      = "keep_rescue_first",
        name    = "Rettungskopie – Start-Lauf, falls noch keine da ist",
        replace_existing = True,
    )

    _scheduler.start()
    log.info("Scheduler started (Upload-Digest %02d:00, Kuratier-Bericht %s %02d:00, "
             "Aufräumen %02d:%02d, Rettungskopie %02d:%02d, Lizenz 09:00)",
             notify_jobs.DIGEST_HOUR, notify_jobs.CURATION_DOW,
             notify_jobs.CURATION_HOUR, _config.RETENTION_HOUR,
             _config.RETENTION_MINUTE, _config.KEEP_HOUR, _config.KEEP_MINUTE)
    return _scheduler


def add_job(**kwargs):
    """Für Module: einen Job an die Kern-Instanz hängen. Muss nach
    `start_scheduler()` gerufen werden."""
    if _scheduler is None:
        raise RuntimeError("core.scheduler: start_scheduler() zuerst")
    kwargs.setdefault("replace_existing", True)
    return _scheduler.add_job(**kwargs)


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
    _scheduler = None


async def _housekeeping_job():
    """Verfallsregeln anwenden. Läuft im Thread — er stattet die halbe
    Bibliothek ab und darf die Event-Loop nicht blockieren."""
    from core import retention
    try:
        await asyncio.to_thread(retention.run_housekeeping)
    except Exception as e:
        log.warning("Aufräumen fehlgeschlagen: %s", e)


async def _keep_start_job():
    """Einmal beim Hochfahren — aber nur, wenn noch nichts da ist."""
    from core import keep
    try:
        if not await asyncio.to_thread(keep.erstlauf_noetig):
            return
        log.info("Rettungskopie: noch keine vorhanden — Start-Lauf")
        erg = await asyncio.to_thread(keep.run_keep)
        log.info("Rettungskopie (Start-Lauf): %s", erg)
    except Exception as e:
        log.warning("Rettungskopie (Start-Lauf) fehlgeschlagen: %s", e)


async def _keep_job():
    """Rettungskopie ziehen. Im Thread — ein Sicherungspunkt ist mehrere
    hundert MB und darf die Event-Loop nicht blockieren."""
    from core import keep
    try:
        await asyncio.to_thread(keep.run_keep)
    except Exception as e:
        log.warning("Rettungskopie fehlgeschlagen: %s", e)
