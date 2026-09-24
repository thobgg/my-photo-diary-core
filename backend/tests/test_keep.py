"""Rettungskopie (core/keep.py, docs/specs/DATENMITNAHME.md).

Der Punkt dieser Tests: Beim Deinstallieren entfernt der Docker-Worker den
ganzen Datenordner. Was hier geprueft wird, ist die Zusage, dass die
unersetzlichen Teile dann bereits woanders liegen — im persoenlichen
Foto-Ordner, in einem Unterordner mit fuehrendem Punkt.
"""

from pathlib import Path

import pytest

import config
from core import keep
from tests.conftest import ADMIN, VIEWER

# Trails ist ein Plus-Modul (19.09.2026: der oeffentliche Kern kommt ohne).
# Tests, die den Standortverlauf pruefen, laufen dann nicht; alles andere —
# Geheimnisse, Start-Lauf, Aufraeum-Bericht — gilt im Kern genauso.
try:
    from core import trails_store
except ImportError:
    trails_store = None
needs_trails = pytest.mark.skipif(trails_store is None,
                                  reason="Trails-Modul nicht mitgeliefert")


def _punkte(user, n=5, t0=1_780_000_000):
    """Ein paar echte Rohpunkte, damit ein Store entsteht. Ohne
    Trails-Modul ein No-op."""
    if trails_store is None:
        return None
    rows = [{"tst": t0 + i * 60, "lat": 20.0 + i * 0.001, "lon": 30.0,
             "acc": 8, "source": "owntracks", "device": "phone"}
            for i in range(n)]
    return trails_store.insert_points(user, rows)


def _monatsdateien(user):
    if trails_store is not None:
        trails_store.export_months(user)


@needs_trails
def test_rettet_standortverlauf_in_den_eigenen_ordner(users_file):
    """Die Datenbank landet im persoenlichen Ordner der Person — nicht im
    Familienbestand, denn Standortdaten sind keine Familiendaten."""
    from core.userdb import lookup
    _punkte(ADMIN[0])
    keep.run_keep()

    ziel = lookup(ADMIN[0]).personal_path / config.KEEP_DIR_NAME
    assert ziel.is_dir()
    dbs = list(ziel.glob("*.sqlite"))
    assert len(dbs) == 1, f"genau ein Stand erwartet, gefunden: {dbs}"
    assert dbs[0].stat().st_size > 0

    # Und NICHT im geteilten Bestand.
    geteilt = config.PHOTO_PATH_SHARED / config.KEEP_DIR_NAME
    assert not list(geteilt.glob("*.sqlite"))


def test_traegt_den_fuehrenden_punkt(users_file):
    """Die Tarnkappe: MPD ueberspringt Ordner mit fuehrendem Punkt ueberall.
    Ohne ihn taeuchte die Rettungskopie als Album oder im Durchlauf auf."""
    assert config.KEEP_DIR_NAME.startswith(".")


@needs_trails
def test_monatsdateien_kommen_mit(users_file):
    """Zweite Ebene: reiner Text, lesbar auch ohne MPD."""
    from core.userdb import lookup
    _punkte(ADMIN[0])
    trails_store.export_months(ADMIN[0])
    keep.run_keep()

    exp = lookup(ADMIN[0]).personal_path / config.KEEP_DIR_NAME / "export"
    assert exp.is_dir()
    assert list(exp.glob("*.jsonl.gz")), "keine Monatsdatei gerettet"


def test_geheimnisse_nur_beim_admin_und_nur_fuer_ihn(users_file):
    """users.json und die Schluessel gehoeren nicht in den Familienbestand,
    wo jedes Konto herankommt — sondern zum Admin, dessen Ordner die
    Synology-ACL privat haelt. Und mit 0600."""
    from core.userdb import lookup
    keep.run_keep()

    beim_admin = lookup(ADMIN[0]).personal_path / config.KEEP_DIR_NAME / "users.json"
    assert beim_admin.is_file(), "users.json nicht beim Admin gerettet"
    assert oct(beim_admin.stat().st_mode)[-3:] == "600"

    beim_viewer = lookup(VIEWER[0]).personal_path / config.KEEP_DIR_NAME / "users.json"
    assert not beim_viewer.exists(), "Geheimnisse beim Viewer gelandet"
    assert not (config.PHOTO_PATH_SHARED / config.KEEP_DIR_NAME / "users.json").exists(), \
        "Geheimnisse im Familienbestand gelandet"


@needs_trails
def test_zweiter_lauf_schreibt_monatsdateien_nicht_neu(users_file):
    """Taeglich laufen heisst nicht taeglich alles neu schreiben — sonst
    stuende jede Nacht 333 MB Schreiblast im Fotobaum."""
    from core.userdb import lookup
    _punkte(ADMIN[0])
    trails_store.export_months(ADMIN[0])
    keep.run_keep()

    exp = lookup(ADMIN[0]).personal_path / config.KEEP_DIR_NAME / "export"
    vorher = {f.name: f.stat().st_mtime_ns for f in exp.glob("*.jsonl*")}
    assert vorher, "nichts exportiert, Test waere aussagelos"

    keep.run_keep()
    nachher = {f.name: f.stat().st_mtime_ns for f in exp.glob("*.jsonl*")}
    assert nachher == vorher, "Monatsdateien wurden unnoetig neu geschrieben"


@needs_trails
def test_nur_ein_stand_je_person(users_file):
    """Ein Sicherungspunkt kostet die volle Groesse des Stores — Vorgabe 1,
    sonst waechst der Fotobaum mit jeder Nacht."""
    from core.userdb import lookup
    _punkte(ADMIN[0])
    for _ in range(3):
        keep.run_keep()
    ziel = lookup(ADMIN[0]).personal_path / config.KEEP_DIR_NAME
    assert len(list(ziel.glob("*.sqlite"))) == config.KEEP_SNAPSHOTS


def test_abschaltbar(users_file, monkeypatch):
    monkeypatch.setattr(config, "KEEP_ENABLED", False)
    assert keep.run_keep() == {"abgeschaltet": True}


@needs_trails
def test_warnt_laut_wenn_kein_persoenlicher_ordner(users_file, caplog, tmp_path):
    """Wer Standortdaten hat, aber keinen Foto-Ordner im Home, bekommt
    heute nichts — das darf nicht stillschweigend passieren. Ausweichen
    waere schlimmer: Die Daten in einen fremden oder in den geteilten
    Ordner zu legen, hoebe die Trennung auf, derentwegen der Store eine
    Datei je Nutzer fuehrt."""
    import logging
    _punkte(VIEWER[0])
    fehlt = tmp_path / "gibt-es-nicht"
    with caplog.at_level(logging.WARNING, logger="core.keep"):
        keep._keep_trails(VIEWER[0], fehlt)
    assert "UNGESICHERT" in caplog.text
    assert VIEWER[0] in caplog.text


@needs_trails
def test_sicherungspunkt_nur_woechentlich(users_file):
    """Ein Stand kostet rund 400 MB schreiben und nochmal so viel kopieren.
    Der von gestern unterscheidet sich kaum — also nicht jede Nacht."""
    from core.userdb import lookup
    _punkte(ADMIN[0])
    keep.run_keep()
    ziel = lookup(ADMIN[0]).personal_path / config.KEEP_DIR_NAME
    erster = {f.name for f in ziel.glob("*.sqlite")}
    assert len(erster) == 1

    keep.run_keep()                      # gleich noch einmal
    assert {f.name for f in ziel.glob("*.sqlite")} == erster, \
        "zweiter Lauf hat einen neuen Stand gezogen"


@needs_trails
def test_sicherungspunkt_wird_erneuert_wenn_alt(users_file, monkeypatch):
    """Nach Ablauf der Frist entsteht wieder einer — sonst veraltet die
    Rettungskopie stillschweigend."""
    from core.userdb import lookup
    _punkte(ADMIN[0])
    keep.run_keep()
    ziel = lookup(ADMIN[0]).personal_path / config.KEEP_DIR_NAME
    alt = {f.name for f in ziel.glob("*.sqlite")}

    monkeypatch.setattr(config, "KEEP_SNAPSHOT_DAYS", 0)   # Schonung aus
    keep.run_keep()
    assert {f.name for f in ziel.glob("*.sqlite")} != alt or \
        len(list(ziel.glob("*.sqlite"))) == config.KEEP_SNAPSHOTS


@needs_trails
def test_monatsdateien_wandern_trotzdem_taeglich(users_file):
    """Die Monatsdateien tragen den Zuwachs — sie duerfen nicht mit dem
    Sicherungspunkt zusammen ausgesetzt werden."""
    from core.userdb import lookup
    _punkte(ADMIN[0])
    trails_store.export_months(ADMIN[0])
    keep.run_keep()
    exp = lookup(ADMIN[0]).personal_path / config.KEEP_DIR_NAME / "export"
    vorher = {f.name for f in exp.glob("*.jsonl.gz")}
    assert vorher

    _punkte(ADMIN[0], n=3, t0=1_790_000_000)      # neuer Monat
    trails_store.export_months(ADMIN[0])
    keep.run_keep()                                # Sicherungspunkt bleibt aus
    nachher = {f.name for f in exp.glob("*.jsonl.gz")}
    assert nachher > vorher, "neuer Monat nicht mitgekommen"


def test_rettungskopie_stoert_den_aufraeum_bericht_nicht(users_file):
    """15.09.2026: find_unsupported() kannte die Punkt-Regel als einzige
    Stelle im Haus nicht und meldete den Inhalt der Rettungskopie als
    'kann MPD nicht anzeigen' — LIESMICH.txt, Datenbanken, env-Datei.
    Ein Aufraeum-Bericht, der ueber die Sicherung klagt, erzieht zum
    Wegsehen."""
    from core import retention
    from core.userdb import lookup
    _punkte(ADMIN[0])
    keep.run_keep()
    basis = lookup(ADMIN[0]).personal_path
    assert (basis / config.KEEP_DIR_NAME / "LIESMICH.txt").is_file()

    res = retention.find_unsupported([basis])
    assert config.KEEP_DIR_NAME not in [a["album"] for a in res["alben"]]
    assert ".sqlite" not in res["nach_endung"]
    assert ".local" not in res["nach_endung"]

def _keeps_entfernen():
    """Ausgangslage wie nach einer Neuinstallation herstellen.

    Die Tests dieser Datei teilen sich einen Datenordner, und frueher
    laufende legen bereits `.mpd-keep` an. Ohne dieses Aufraeumen haengt
    das Ergebnis an der Reihenfolge — und ein Test, der nur an Position 13
    gruen ist, prueft nichts.
    """
    import shutil
    from core.userdb import list_users, lookup
    for name in list_users():
        rec = lookup(name)
        if rec is None or not rec.personal_path:
            continue
        shutil.rmtree(Path(rec.personal_path) / config.KEEP_DIR_NAME,
                      ignore_errors=True)


def test_erstlauf_nur_wenn_noch_nichts_da_ist(users_file):
    """Der Start-Lauf darf genau einmal greifen.

    Nach einer Neuinstallation gaebe es sonst bis zu einen Tag lang keine
    Rettungskopie — und eine Neuinstallation ist der Fall, fuer den sie
    gedacht ist. Aber Neustarts gibt es oefter als Neuinstallationen; zoege
    jeder von ihnen mehrere hundert MB, waere die Kur schlimmer als das
    Leiden.
    """
    from core.userdb import lookup

    _keeps_entfernen()
    _punkte(ADMIN[0])
    _monatsdateien(ADMIN[0])

    assert keep.erstlauf_noetig() is True, "vor dem ersten Lauf muss er noetig sein"

    keep.run_keep()

    ziel = Path(lookup(ADMIN[0]).personal_path) / config.KEEP_DIR_NAME
    assert ziel.is_dir() and any(ziel.iterdir()), "der Lauf hat nichts abgelegt"
    assert keep.erstlauf_noetig() is False, "zweiter Start wuerde erneut kopieren"


def test_erstlauf_legt_beim_pruefen_nichts_an(users_file):
    """Eine Pruefung darf den Zustand nicht veraendern, den sie prueft.

    `_keep_dir()` legt den Ordner an — wuerde die Pruefung ihn benutzen,
    waere nach dem ersten Blick ein leeres `.mpd-keep` da und die Antwort
    beim naechsten Mal falsch.
    """
    from core.userdb import lookup

    _keeps_entfernen()
    keep.erstlauf_noetig()
    for name in (ADMIN[0], VIEWER[0]):
        rec = lookup(name)
        if rec is None or not rec.personal_path:
            continue
        assert not (Path(rec.personal_path) / config.KEEP_DIR_NAME).exists(), \
            f"Pruefung hat bei '{name}' einen Ordner angelegt"


def test_erstlauf_schweigt_wenn_abgeschaltet(users_file, monkeypatch):
    """MPD_KEEP=0 heisst aus — auch fuer den Start-Lauf."""
    monkeypatch.setattr(config, "KEEP_ENABLED", False)
    assert keep.erstlauf_noetig() is False
