"""Der Kern ohne Plus-Module (19.09.2026, Vorbereitung des öffentlichen Kerns).

Zwei Zusagen, die vorher nicht galten:

1. Was nicht mitgeliefert ist, ist für niemanden frei — auch nicht für den
   Admin. Sonst zeigt das Menü Einträge, deren Seiten fehlen.
2. Die Nachtjobs des Kerns (Aufräumen, Rettungskopie, Benachrichtigungen)
   hängen nicht an Memories. Bis heute lag der Scheduler in
   diary_memories/; ohne das Modul fielen sie still weg. Memories selbst
   gehoert seit 20.09.2026 zum Kern, der Scheduler bleibt trotzdem dort,
   wo er hingehoert.
"""
import asyncio

from tests.conftest import ADMIN


def test_admin_bekommt_nur_installierte_module(client, login, monkeypatch):
    from routers import deps
    monkeypatch.setattr(deps, "_INSTALLED", frozenset({"stats"}))
    login()
    r = client.get("/api/whoami")
    assert r.status_code == 200
    assert r.json()["user"] == ADMIN[0]
    assert r.json()["modules"] == ["stats"]


def test_ohne_jedes_modul_ist_nichts_frei(client, login, monkeypatch):
    from routers import deps
    monkeypatch.setattr(deps, "_INSTALLED", frozenset())
    login()
    assert client.get("/api/whoami").json()["modules"] == []


def test_installed_modules_kennt_nur_modulnamen():
    from routers import deps
    assert deps.installed_modules() <= deps.MODULE_KEYS


def test_kern_scheduler_traegt_seine_jobs_allein():
    """Die Kern-Jobs stehen, bevor irgendein Modul etwas anhängt."""
    from core import scheduler

    async def _lauf():
        s = scheduler.start_scheduler()
        try:
            return {j.id for j in s.get_jobs()}
        finally:
            scheduler.stop_scheduler()

    ids = asyncio.run(_lauf())
    assert {"notify_upload_digest", "notify_curation_report",
            "housekeeping_retention", "keep_rescue_copy",
            "keep_rescue_first"} <= ids
    assert "diary_memories_daily" not in ids


def test_kern_importiert_kein_plus_modul_fest():
    """main.py darf die Plus-Module nur optional laden (try/ImportError).
    Memories ist seit 20.09.2026 Kern und darf fest importiert werden."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    muster = r"^(\s*)from routers\.(stats|search|tours|trails)|^(\s*)from core import trails_"
    for m in re.finditer(muster, src, re.M):
        davor = src[:m.start()].rstrip().splitlines()[-1].strip()
        assert davor == "try:", f"ungeschützter Import: {m.group(0)!r}"


def test_version_nennt_die_ausgabe(client, monkeypatch):
    """/api/version sagt, ob Core oder Plus laeuft — aus dem laufenden
    Code, nicht aus dem Bau (Fusszeile, Supportfall)."""
    from routers import deps
    monkeypatch.setattr(deps, "_INSTALLED", frozenset())
    assert client.get("/api/version").json()["edition"] == "core"
    monkeypatch.setattr(deps, "_INSTALLED", frozenset({"trails"}))
    assert client.get("/api/version").json()["edition"] == "plus"
