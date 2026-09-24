"""Löschen legt in den Papierkorb, statt hart zu löschen (06.09.2026).

Vorher rief der Endpunkt `unlink()` und meldete trotzdem `recycled: true`,
sobald es irgendwo einen `#recycle`-Ordner GAB. Beide Oberflächen haben
daraus „die Datei landet im Papierkorb" gemacht — eine Zusage, die nicht
eingelöst wurde. Diese Tests halten fest, dass das Feld jetzt heißt, was
es sagt.
"""
from pathlib import Path

import config

from routers.album_files import find_recycle_dir, recycle_target_dir
from tests.conftest import ADMIN


# ── Wo der Papierkorb gesucht wird ──────────────────────────────────────

def test_findet_papierkorb_in_der_freigabe(tmp_path):
    """Familienbestand: base IST die Freigabe."""
    (tmp_path / "#recycle").mkdir()
    assert find_recycle_dir(tmp_path) == tmp_path / "#recycle"


def test_findet_papierkorb_eine_ebene_hoeher(tmp_path):
    """Persönlicher Bereich: base zeigt IN die Freigabe hinein
    (<home>/Photos), DSM führt den Papierkorb unter <home>/#recycle."""
    home = tmp_path / "anna"
    (home / "Photos").mkdir(parents=True)
    (home / "#recycle").mkdir()
    assert find_recycle_dir(home / "Photos") == home / "#recycle"


def test_ohne_papierkorb_kein_treffer(tmp_path):
    (tmp_path / "album").mkdir()
    assert find_recycle_dir(tmp_path / "album") is None


def test_sucht_nicht_bis_zur_wurzel(tmp_path):
    """Genau zwei Ebenen — sonst landet die Suche in fremden Freigaben."""
    tief = tmp_path / "a" / "b" / "c" / "d"
    tief.mkdir(parents=True)
    (tmp_path / "#recycle").mkdir()
    assert find_recycle_dir(tief) is None


def test_nimmt_nicht_den_papierkorb_der_ganzen_homes_freigabe(tmp_path):
    """Der Fall, der am 06.09.2026 live auffiel: Ein persönlicher Bereich
    OHNE eigenen Papierkorb darf nicht den der homes-Freigabe erwischen —
    dorthin kommt nur der Administrator, die Datei wäre aus dem Bereich
    ihres Besitzers heraus in fremde Sicht gewandert."""
    homes = tmp_path / "homes"
    (homes / "#recycle").mkdir(parents=True)          # nur Admin sieht das
    eigener = homes / "mpd-demo" / "Photos"
    eigener.mkdir(parents=True)                       # KEIN eigener Korb
    assert find_recycle_dir(eigener) is None


# ── Der Endpunkt ────────────────────────────────────────────────────────

def test_loeschen_verschiebt_in_den_papierkorb(client, login, tmp_path):
    """End-to-end gegen den Shared-Space der Testumgebung."""
    login()
    base = config.PHOTO_PATH_SHARED
    album_dir = base / "2026 - Papierkorbtest"
    album_dir.mkdir(parents=True, exist_ok=True)
    (album_dir / "bild.jpg").write_bytes(b"\xff\xd8\xff\xdb" + b"x" * 40)
    recycle = base / "#recycle"
    recycle.mkdir(exist_ok=True)

    r = client.request("DELETE",
                       "/api/album/shared/2026%20-%20Papierkorbtest/file",
                       json={"filename": "bild.jpg"})
    assert r.status_code == 200, r.text
    assert r.json()["recycled"] is True
    assert not (album_dir / "bild.jpg").exists()
    assert (recycle / "2026 - Papierkorbtest" / "bild.jpg").is_file()


def test_ohne_papierkorb_wird_endgueltig_geloescht_und_ehrlich_gemeldet(client, login):
    login()
    base = config.PHOTO_PATH_SHARED
    recycle = base / "#recycle"
    if recycle.exists():
        import shutil as _sh
        _sh.rmtree(recycle)
    album_dir = base / "2026 - Ohne Papierkorb"
    album_dir.mkdir(parents=True, exist_ok=True)
    (album_dir / "weg.jpg").write_bytes(b"\xff\xd8\xff\xdb" + b"x" * 40)

    r = client.request("DELETE",
                       "/api/album/shared/2026%20-%20Ohne%20Papierkorb/file",
                       json={"filename": "weg.jpg"})
    assert r.status_code == 200, r.text
    assert r.json()["recycled"] is False        # keine falsche Zusage
    assert not (album_dir / "weg.jpg").exists()


def test_gleicher_name_im_papierkorb_ueberschreibt_nicht(client, login):
    """Zweimal dieselbe Datei löschen darf die erste nicht auffressen."""
    login()
    base = config.PHOTO_PATH_SHARED
    recycle = base / "#recycle"
    recycle.mkdir(exist_ok=True)
    album_dir = base / "2026 - Zweimal"
    album_dir.mkdir(parents=True, exist_ok=True)

    for _ in range(2):
        (album_dir / "doppelt.jpg").write_bytes(b"\xff\xd8\xff\xdb" + b"y" * 40)
        r = client.request("DELETE", "/api/album/shared/2026%20-%20Zweimal/file",
                           json={"filename": "doppelt.jpg"})
        assert r.status_code == 200, r.text

    im_korb = list((recycle / "2026 - Zweimal").glob("doppelt*.jpg"))
    assert len(im_korb) == 2, [p.name for p in im_korb]


def test_recycle_status_meldet_den_gefundenen_korb(client, login):
    login()
    (config.PHOTO_PATH_SHARED / "#recycle").mkdir(exist_ok=True)
    r = client.get("/api/recycle-status/shared")
    assert r.status_code == 200
    assert r.json()["recycled"] is True


# ── Änderungszeitpunkt (06.09.2026) ─────────────────────────────────────

def test_loeschen_stempelt_den_aenderungszeitpunkt(client, login):
    """Der Löschpfad war der einzige Schreiber ohne Stempel. Folge:
    Clients hielten ihre alte Kopie für aktuell und zeigten das gelöschte
    Foto weiter — und der Konfliktschutz liess ein Speichern von VOR der
    Loeschung durch, wodurch das Element zurueckkam."""
    import json
    login()
    base = config.PHOTO_PATH_SHARED
    album_dir = base / "2026 - Stempeltest"
    album_dir.mkdir(parents=True, exist_ok=True)
    (album_dir / "eins.jpg").write_bytes(b"\xff\xd8\xff\xdb" + b"x" * 40)
    alt = "2020-01-01T00:00:00"
    (album_dir / "album.json").write_text(json.dumps({
        "version": "1.5",
        "meta": {"title": "Stempeltest", "year": 2026, "thumbnail": "eins.jpg",
                 "created": alt, "modified": alt},
        "elements": [{"id": "0010", "type": "photo", "file": "eins.jpg"}],
    }), encoding="utf-8")

    r = client.request("DELETE", "/api/album/shared/2026%20-%20Stempeltest/file",
                       json={"filename": "eins.jpg", "album_context": True})
    assert r.status_code == 200, r.text

    data = json.loads((album_dir / "album.json").read_text(encoding="utf-8"))
    assert data["elements"] == []
    assert data["meta"]["modified"] != alt, "Aenderungszeitpunkt nicht gestempelt"
    assert data["meta"]["created"] == alt, "created darf sich nicht aendern"


# ── Löschschutz (06.09.2026) ────────────────────────────────────────────
#
# Zwei verschiedene Antworten mit verschiedener Bedeutung:
# 403 = falsche Tür („im Album löschen"), überstimmbar mit album_context.
# 409 = Zustand steht entgegen („wird woanders benutzt"), nicht überstimmbar.

def _album_mit_json(base, name, elements, titel=None):
    import json
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "album.json").write_text(json.dumps({
        "version": "1.5",
        "meta": {"title": titel or name, "year": 2026, "thumbnail": "",
                 "created": "2026-01-01T00:00:00", "modified": "2026-01-01T00:00:00"},
        "elements": elements,
    }), encoding="utf-8")
    return d


def _del(client, album, filename, **body):
    from urllib.parse import quote
    return client.request("DELETE", f"/api/album/shared/{quote(album)}/file",
                          json={"filename": filename, **body})


def test_albumdatei_ohne_kontext_abgelehnt(client, login):
    login()
    d = _album_mit_json(config.PHOTO_PATH_SHARED, "2026 - Schutz",
                        [{"id": "0010", "type": "photo", "file": "drin.jpg"}])
    (d / "drin.jpg").write_bytes(b"\xff\xd8\xff\xdb" + b"x" * 40)
    r = _del(client, "2026 - Schutz", "drin.jpg")
    assert r.status_code == 403
    assert "Album" in r.json()["detail"]
    assert (d / "drin.jpg").exists(), "nichts angefasst"


def test_albumdatei_mit_kontext_geht(client, login):
    login()
    d = _album_mit_json(config.PHOTO_PATH_SHARED, "2026 - Schutz2",
                        [{"id": "0010", "type": "photo", "file": "drin.jpg"}])
    (d / "drin.jpg").write_bytes(b"\xff\xd8\xff\xdb" + b"x" * 40)
    r = _del(client, "2026 - Schutz2", "drin.jpg", album_context=True)
    assert r.status_code == 200, r.text
    assert not (d / "drin.jpg").exists()


def test_durchlauf_braucht_keinen_kontext(client, login):
    """Ordner ohne album.json ist der Durchlauf — dort ist Wegwerfen
    genau der vorgesehene Handgriff."""
    login()
    d = config.PHOTO_PATH_SHARED / "2026"
    d.mkdir(parents=True, exist_ok=True)
    (d / "ausschuss.jpg").write_bytes(b"\xff\xd8\xff\xdb" + b"x" * 40)
    assert _del(client, "2026", "ausschuss.jpg").status_code == 200


def test_fremdreferenz_blockt_auch_mit_kontext(client, login):
    """Nicht überstimmbar: sonst bleibt im anderen Album ein Element
    zurück, das ins Leere zeigt."""
    login()
    heimat = _album_mit_json(config.PHOTO_PATH_SHARED, "2026 - Heimat",
                             [{"id": "0010", "type": "photo", "file": "gast.jpg"}])
    (heimat / "gast.jpg").write_bytes(b"\xff\xd8\xff\xdb" + b"x" * 40)
    _album_mit_json(config.PHOTO_PATH_SHARED, "2026 - Gastgeber",
                    [{"id": "0020", "type": "photo", "file": "gast.jpg",
                      "source_album": "2026 - Heimat"}], titel="Best of 2026")

    r = _del(client, "2026 - Heimat", "gast.jpg", album_context=True)
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["used_by"][0]["album"] == "2026 - Gastgeber"
    assert body["used_by"][0]["title"] == "Best of 2026"
    assert (heimat / "gast.jpg").exists(), "nichts angefasst"


def test_eigene_referenz_blockt_nicht(client, login):
    """Das Album, in dem die Datei wohnt, darf sie natürlich löschen —
    sonst käme man nie an das eigene Foto heran."""
    login()
    d = _album_mit_json(config.PHOTO_PATH_SHARED, "2026 - Eigen",
                        [{"id": "0010", "type": "photo", "file": "meins.jpg"}])
    (d / "meins.jpg").write_bytes(b"\xff\xd8\xff\xdb" + b"x" * 40)
    assert _del(client, "2026 - Eigen", "meins.jpg",
                album_context=True).status_code == 200


# ── Ablage im Korb: relativ zur Freigabe, nicht zu base (07.09.2026) ────

def test_zielpfad_im_familienbestand_unveraendert(tmp_path):
    """base IST die Freigabe — dort aendert sich nichts."""
    recycle = tmp_path / "#recycle"
    album   = tmp_path / "2026 - Album"
    assert recycle_target_dir(recycle, album) == recycle / "2026 - Album"


def test_zielpfad_im_persoenlichen_bereich_traegt_photos():
    """<home>/Photos/<album> → <home>/#recycle/Photos/<album>, wie DSM
    es beim Wiederherstellen erwartet."""
    home    = Path("/volume1/homes/jemand")
    recycle = home / "#recycle"
    album   = home / "Photos" / "2026 - Album"
    assert recycle_target_dir(recycle, album) == recycle / "Photos" / "2026 - Album"


def test_zielpfad_faellt_auf_albumnamen_zurueck_wenn_korb_nicht_oberhalb():
    recycle = Path("/woanders/#recycle")
    album   = Path("/volume1/photo/2026 - Album")
    assert recycle_target_dir(recycle, album) == recycle / "2026 - Album"


def test_loeschen_im_persoenlichen_bereich_legt_unter_photos_ab(client, login, personal_album):
    """End-to-end: Korb eine Ebene ueber base, Ablage traegt das
    Zwischensegment. Der Korb wird hinterher entfernt, weil die
    Zwei-Ebenen-Suche ihn sonst auch vom Shared-Space aus faende und
    `test_ohne_papierkorb…` eine falsche Zusage bekaeme."""
    import shutil as _sh
    login()
    base    = Path(config.PHOTO_PATH_SHARED).parent / f"personal-{ADMIN[0]}"
    home    = base.parent                                                # = _TMP
    recycle = home / "#recycle"
    recycle.mkdir(exist_ok=True)
    try:
        assert find_recycle_dir(base) == recycle      # eine Ebene hoeher gefunden
        r = client.request("DELETE",
                           f"/api/album/personal/{personal_album}/file",
                           json={"filename": "p2.jpg", "album_context": True})
        assert r.status_code == 200, r.text
        assert r.json()["recycled"] is True
        erwartet = recycle / f"personal-{ADMIN[0]}" / personal_album / "p2.jpg"
        assert erwartet.is_file(), sorted(p.relative_to(recycle) for p in recycle.rglob("*"))
        assert not (recycle / personal_album / "p2.jpg").exists()   # der alte, falsche Ort
    finally:
        _sh.rmtree(recycle, ignore_errors=True)
