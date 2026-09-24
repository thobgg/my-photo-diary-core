"""Verfallsregeln (core/retention.py, E1).

Geprüft wird das Verhalten, auf das man sich verlassen können muss:
die Aufräumregel darf eine Ablage nie leeren, und der Kehrer darf nie
löschen, wenn er die Wahrheit nicht kennt.
"""
import os
import time

import config
import pytest

from core import retention


def _datei(pfad, alter_tage=0, inhalt=b"x"):
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_bytes(inhalt)
    if alter_tage:
        t = time.time() - alter_tage * 86400
        os.utime(pfad, (t, t))
    return pfad


# ── prune_dir: N neueste UND Höchstalter ────────────────────────────────

def test_behaelt_die_n_neuesten(tmp_path):
    for i in range(6):
        _datei(tmp_path / f"s{i}.sqlite", alter_tage=i)
    r = retention.prune_dir(tmp_path, keep_newest=3, pattern="*.sqlite")
    assert r["geloescht"] == 3
    rest = sorted(p.name for p in tmp_path.iterdir())
    assert rest == ["s0.sqlite", "s1.sqlite", "s2.sqlite"]


def test_loescht_zu_altes_auch_innerhalb_der_anzahl(tmp_path):
    _datei(tmp_path / "neu.sqlite", alter_tage=1)
    _datei(tmp_path / "alt.sqlite", alter_tage=200)
    r = retention.prune_dir(tmp_path, keep_newest=10, max_age_days=90,
                            pattern="*.sqlite")
    assert r["geloescht"] == 1
    assert (tmp_path / "neu.sqlite").exists()
    assert not (tmp_path / "alt.sqlite").exists()


def test_juengster_stand_bleibt_immer(tmp_path):
    """Auch wenn ALLES älter ist als die Grenze — eine Regel, die eine
    Ablage leeren kann, ist keine."""
    _datei(tmp_path / "a.sqlite", alter_tage=300)
    _datei(tmp_path / "b.sqlite", alter_tage=400)
    r = retention.prune_dir(tmp_path, keep_newest=2, max_age_days=90,
                            pattern="*.sqlite")
    assert r["geloescht"] == 1
    assert (tmp_path / "a.sqlite").exists()   # der jüngste der beiden


def test_ohne_grenzen_wird_nichts_geloescht(tmp_path):
    for i in range(4):
        _datei(tmp_path / f"s{i}.sqlite", alter_tage=i * 100)
    r = retention.prune_dir(tmp_path, keep_newest=0, max_age_days=0,
                            pattern="*.sqlite")
    assert r["geloescht"] == 0
    assert len(list(tmp_path.iterdir())) == 4


def test_trockenlauf_zaehlt_aber_loescht_nicht(tmp_path):
    for i in range(5):
        _datei(tmp_path / f"s{i}.sqlite", alter_tage=i, inhalt=b"y" * 100)
    r = retention.prune_dir(tmp_path, keep_newest=2, pattern="*.sqlite",
                            dry_run=True)
    assert r["geloescht"] == 3
    assert r["bytes"] == 300
    assert len(list(tmp_path.iterdir())) == 5


def test_muster_trennt_fremde_dateien(tmp_path):
    for i in range(4):
        _datei(tmp_path / f"s{i}.sqlite", alter_tage=i)
    _datei(tmp_path / "liesmich.txt", alter_tage=500)
    retention.prune_dir(tmp_path, keep_newest=1, pattern="*.sqlite")
    assert (tmp_path / "liesmich.txt").exists()


def test_fehlender_ordner_ist_kein_fehler(tmp_path):
    r = retention.prune_dir(tmp_path / "gibtsnicht", keep_newest=1)
    assert r["geloescht"] == 0


# ── sweep_thumb_cache: gegen die gültigen Schlüssel kehren ──────────────

def _cache_datei(name, alter_tage=0, size="thumb"):
    p = config.THUMB_CACHE_DIR / size / name[:2] / f"{name}.webp"
    return _datei(p, alter_tage=alter_tage, inhalt=b"z" * 10)


def test_kehrt_verwaiste_und_schont_gueltige(monkeypatch):
    gueltig = "a" * 40
    verwaist = "b" * 40
    _cache_datei(gueltig, alter_tage=30)
    _cache_datei(verwaist, alter_tage=30)

    monkeypatch.setattr(retention, "known_spaces",
                        lambda: ([config.PHOTO_PATH_SHARED], []))
    monkeypatch.setattr(retention, "collect_valid_keys", lambda spaces: {gueltig})

    r = retention.sweep_thumb_cache(grace_days=7, dry_run=False)
    assert r["verwaist"] == 1
    assert r["geloescht"] == 1
    assert (config.THUMB_CACHE_DIR / "thumb" / gueltig[:2] / f"{gueltig}.webp").exists()
    assert not (config.THUMB_CACHE_DIR / "thumb" / verwaist[:2] / f"{verwaist}.webp").exists()


def test_schonfrist_schuetzt_frisches(monkeypatch):
    frisch = "c" * 40
    _cache_datei(frisch, alter_tage=0)
    monkeypatch.setattr(retention, "known_spaces",
                        lambda: ([config.PHOTO_PATH_SHARED], []))
    monkeypatch.setattr(retention, "collect_valid_keys", lambda spaces: {"d" * 40})

    r = retention.sweep_thumb_cache(grace_days=7, dry_run=False)
    assert r["verwaist"] == 1
    assert r["geloescht"] == 0
    assert (config.THUMB_CACHE_DIR / "thumb" / frisch[:2] / f"{frisch}.webp").exists()


def test_kehrt_nicht_wenn_ein_bestand_fehlt(monkeypatch):
    verwaist = "e" * 40
    _cache_datei(verwaist, alter_tage=99)
    monkeypatch.setattr(retention, "known_spaces",
                        lambda: ([config.PHOTO_PATH_SHARED], ["/volume1/homes/wer/Photos"]))

    r = retention.sweep_thumb_cache(grace_days=7, dry_run=False)
    assert "uebersprungen" in r
    assert r["geloescht"] == 0
    assert (config.THUMB_CACHE_DIR / "thumb" / verwaist[:2] / f"{verwaist}.webp").exists()


def test_kehrt_nicht_bei_leerer_schluesselmenge(monkeypatch):
    verwaist = "f" * 40
    _cache_datei(verwaist, alter_tage=99)
    monkeypatch.setattr(retention, "known_spaces",
                        lambda: ([config.PHOTO_PATH_SHARED], []))
    monkeypatch.setattr(retention, "collect_valid_keys", lambda spaces: set())

    r = retention.sweep_thumb_cache(grace_days=7, dry_run=False)
    assert "uebersprungen" in r
    assert r["geloescht"] == 0
    assert (config.THUMB_CACHE_DIR / "thumb" / verwaist[:2] / f"{verwaist}.webp").exists()


def test_trockenlauf_ist_die_vorgabe(monkeypatch):
    verwaist = "0" * 40
    _cache_datei(verwaist, alter_tage=99)
    monkeypatch.setattr(config, "THUMB_SWEEP_ENABLED", False)
    monkeypatch.setattr(retention, "known_spaces",
                        lambda: ([config.PHOTO_PATH_SHARED], []))
    monkeypatch.setattr(retention, "collect_valid_keys", lambda spaces: {"9" * 40})

    r = retention.sweep_thumb_cache()
    assert r["trockenlauf"] is True
    assert r["geloescht"] == 1          # gemeldet …
    assert (config.THUMB_CACHE_DIR / "thumb" / verwaist[:2] / f"{verwaist}.webp").exists()  # … aber da


# ── collect_valid_keys: findet, was es gibt ─────────────────────────────

def test_schluessel_kommen_aus_allen_ordnern(tmp_path):
    from core.thumb_pipeline import etag_for

    space = tmp_path / "space"
    foto = _datei(space / "2026 - Toskana" / "IMG_1.jpg")
    durchlauf = _datei(space / "2026" / "IMG_2.jpg")     # ohne album.json
    _datei(space / "2026" / "notiz.txt")                  # kein Medium
    _datei(space / "2026" / ".versteckt.jpg")             # Punktdatei

    keys = retention.collect_valid_keys([space])
    assert keys == {etag_for(foto), etag_for(durchlauf)}


@pytest.fixture(autouse=True)
def _leerer_cache():
    """Jeder Test startet mit leerem Zwischenspeicher."""
    import shutil
    if config.THUMB_CACHE_DIR.exists():
        shutil.rmtree(config.THUMB_CACHE_DIR)
    config.THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    yield


# ── Elemente, die ins Leere zeigen ──────────────────────────────────────

def _album(root, name, elements, cover=None):
    import json
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "album.json").write_text(json.dumps({
        "version": "1.5",
        "meta": {"title": name, "year": 2026, "thumbnail": cover or "",
                 "created": "2026-01-01T00:00:00", "modified": "2026-01-01T00:00:00"},
        "elements": elements,
    }), encoding="utf-8")
    return d


def test_findet_fehlende_datei(tmp_path):
    a = _album(tmp_path, "album", [{"id": "0010", "type": "photo", "file": "fehlt.jpg"}])
    assert a.exists()
    tot = retention.find_dangling([tmp_path])
    assert len(tot) == 1 and tot[0]["id"] == "0010"


def test_vorhandene_datei_ist_kein_treffer(tmp_path):
    a = _album(tmp_path, "album", [{"id": "0010", "type": "photo", "file": "da.jpg"}])
    (a / "da.jpg").write_bytes(b"x")
    assert retention.find_dangling([tmp_path]) == []


def test_source_album_wird_aufgeloest(tmp_path):
    """Das Foto wohnt woanders — dort muss gesucht werden, nicht im Album."""
    quelle = _album(tmp_path, "quelle", [])
    (quelle / "gast.jpg").write_bytes(b"x")
    _album(tmp_path, "gast-album", [{"id": "0010", "type": "photo",
                                     "file": "gast.jpg", "source_album": "quelle"}])
    assert retention.find_dangling([tmp_path]) == []


def test_fehlendes_cover_wird_gemeldet(tmp_path):
    _album(tmp_path, "album", [], cover="weg.jpg")
    tot = retention.find_dangling([tmp_path])
    assert len(tot) == 1 and tot[0]["typ"] == "cover"


def test_textelemente_stoeren_nicht(tmp_path):
    _album(tmp_path, "album", [{"id": "0010", "type": "text", "text": "<p>hallo</p>"}])
    assert retention.find_dangling([tmp_path]) == []


# ---------------------------------------------------------------------------
# Was NICHT ins Log darf (07.09.2026)
# ---------------------------------------------------------------------------

def test_treffer_tragen_ihre_herkunft(tmp_path, monkeypatch):
    """Der Nachtlauf geht ueber ALLE Bestaende — auch ueber die
    persoenlichen Bereiche anderer Menschen. Album- und Dateinamen von
    dort duerfen nicht ins Log: Die Datei liegt im Repo, geht in die
    Sicherung und wird von Menschen gelesen, die dort keinen Zugriff
    haben. Deshalb traegt jeder Treffer, woher er kommt; benannt wird nur
    der Familienbestand.
    """
    import config
    from core import retention

    shared = tmp_path / "photo"
    fremd  = tmp_path / "homes" / "jemand" / "Photos"
    (shared / "2020 - Gemeinsam").mkdir(parents=True)
    (fremd / "2021 - Privat").mkdir(parents=True)
    (shared / "2020 - Gemeinsam" / "roh.cr2").write_bytes(b"x")
    (fremd / "2021 - Privat" / "roh.cr2").write_bytes(b"x")

    monkeypatch.setattr(config, "PHOTO_PATH_SHARED", shared)

    u = retention.find_unsupported([shared, fremd])
    assert u["gesamt"] == 2                      # gezaehlt wird beides
    nach_herkunft = {a["album"]: a["shared"] for a in u["alben"]}
    assert nach_herkunft["2020 - Gemeinsam"] is True
    assert nach_herkunft["2021 - Privat"] is False
