"""POST /api/album/{space}/year-album — „Rest ins Jahresalbum" (08.09.2026):
Durchlauf <year> → Album „<year> - das Jahr", anlegen falls noetig,
Aufnahmereihenfolge, nichts ueberschreiben."""
import json
from urllib.parse import quote

from tests.conftest import ADMIN, VIEWER, _TMP, make_jpeg


def _durchlauf(user, year, names):
    base = _TMP / f"personal-{user}" / year
    for n in names:
        make_jpeg(base / n)
    return base


def test_jahresalbum_anlegen_und_fuellen(client, login):
    login()
    src = _durchlauf(ADMIN[0], "2035", ["2035-06-01_12-00-00.jpg", "2035-01-15_09-30-00.jpg", "notiz.txt"])
    r = client.post("/api/album/personal/year-album", json={"year": "2035"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["created"] is True and d["moved"] == 2 and d["skipped_exists"] == 0
    assert d["album"] == "2035 - das Jahr"

    dst = _TMP / f"personal-{ADMIN[0]}" / "2035 - das Jahr"
    data = json.loads((dst / "album.json").read_text(encoding="utf-8"))
    assert [e["file"] for e in data["elements"]] == ["2035-01-15_09-30-00.jpg", "2035-06-01_12-00-00.jpg"]
    assert data["meta"]["thumbnail"] == "2035-01-15_09-30-00.jpg"
    assert data["meta"]["year"] == 2035
    assert not (src / "2035-06-01_12-00-00.jpg").exists()
    assert (src / "notiz.txt").exists()          # keine Mediendatei, bleibt liegen

    # Zweiter Lauf: nichts mehr zu tun, Album bleibt
    r = client.post("/api/album/personal/year-album", json={"year": "2035"})
    assert r.status_code == 200 and r.json()["moved"] == 0 and r.json()["created"] is False

    # Nachschub: haengt hinten an, Kollision wird uebersprungen
    make_jpeg(src / "2035-12-24_18-00-00.jpg")
    make_jpeg(src / "2035-01-15_09-30-00.jpg")
    r = client.post("/api/album/personal/year-album", json={"year": "2035"})
    # Gleicher Name, gleiche Bytes → Dublette: Quelle weg, Ziel unveraendert
    assert r.json()["moved"] == 1 and r.json()["duplicates"] == 1 and r.json()["skipped_exists"] == 0
    assert not (src / "2035-01-15_09-30-00.jpg").exists()
    data = json.loads((dst / "album.json").read_text(encoding="utf-8"))
    assert data["elements"][-1]["file"] == "2035-12-24_18-00-00.jpg"
    assert len({e["id"] for e in data["elements"]}) == 3

    # Album ist im Zeitstrahl kuratiert, Durchlauf-Rest unzugeordnet
    r = client.get(f"/api/album/personal/{quote('2035 - das Jahr')}")
    assert r.status_code == 200 and r.json()["unassigned_count"] == 0


def test_jahresalbum_ohne_durchlauf_404(client, login):
    login()
    r = client.post("/api/album/personal/year-album", json={"year": "2099"})
    assert r.status_code == 404


def test_jahresalbum_rechte(client, login):
    """Anlegen ist Kuratieren: im Familienbestand nur Admin. Viewer darf
    im eigenen Bereich alles."""
    login(user=VIEWER[0], pw=VIEWER[1])
    _durchlauf(VIEWER[0], "2036", ["a.jpg"])
    r = client.post("/api/album/personal/year-album", json={"year": "2036"})
    assert r.status_code == 200 and r.json()["moved"] == 1
    r = client.post("/api/album/shared/year-album", json={"year": "2036"})
    assert r.status_code == 403


def test_jahresalbum_name_validierung(client, login):
    login()
    _durchlauf(ADMIN[0], "2037", ["a.jpg"])
    assert client.post("/api/album/personal/year-album", json={"year": "2037", "name": "2037"}).status_code == 400
    assert client.post("/api/album/personal/year-album", json={"year": "2037", "name": "../x"}).status_code == 400
    assert client.post("/api/album/personal/year-album", json={"year": "37"}).status_code == 422
