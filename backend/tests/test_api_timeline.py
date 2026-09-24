"""GET /api/timeline (additiv 09/2026): chronologische Medienansicht.

Voller Roundtrip durch die echte App: Datumsquellen (filename/mtime),
referenced-Flag für unkuratierte Dateien, Cursor-Paging, Invalidierung
nach Album-Update, Modul-Gating, URL-Schema wie in der Album-Antwort."""
from urllib.parse import quote

from core import timeline_index
from tests.conftest import ADMIN, make_jpeg
from tests.test_api_modules import _upsert_user


def _items(client, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = client.get("/api/timeline" + (f"?{q}" if q else ""))
    assert r.status_code == 200, r.text
    return r.json()


def test_timeline_requires_auth(client):
    assert client.get("/api/timeline").status_code == 401


def test_timeline_lists_album_photos(client, login, personal_album):
    timeline_index.invalidate_timeline_index()
    login()
    data = _items(client)
    mine = [i for i in data["items"] if i["album"] == personal_album]
    assert {i["file"] for i in mine} == {"p1.jpg", "p2.jpg"}
    for i in mine:
        # p1/p2 haben weder Datums-Dateinamen noch EXIF → mtime
        assert i["date_source"] == "mtime"
        assert i["type"] == "photo"
        assert i["referenced"] is True
        assert i["locked"] is False
        assert i["space"] == "personal"
        enc = f"{quote(personal_album)}/{i['file']}"
        assert i["thumbnails"]["full"].startswith(
            f"/api/thumbnail/personal/{enc}?size=full")  # + &v=<cachekey>
        assert i["original"] == f"/api/photo/personal/{enc}/download"
    # absteigend sortiert
    dates = [i["date"] for i in data["items"]]
    assert dates == sorted(dates, reverse=True)


def test_timeline_unreferenced_and_filename_date(client, login, personal_album):
    """Datei im Ordner ohne album.json-Eintrag: taucht mit
    referenced=false auf; Datums-Dateiname liefert Uhrzeit + Quelle."""
    import config
    base = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}" / personal_album
    make_jpeg(base / "2019-07-03_14-25-36.jpg")
    timeline_index.invalidate_timeline_index()

    login()
    data = _items(client)
    extra = next(i for i in data["items"]
                 if i["album"] == personal_album and i["file"] == "2019-07-03_14-25-36.jpg")
    assert extra["referenced"] is False
    assert extra["date_source"] == "filename"
    assert extra["date"] == "2019-07-03T14:25:36"


def test_timeline_cursor_paging(client, login, personal_album):
    timeline_index.invalidate_timeline_index()
    login()
    seen = []
    cursor = None
    for _ in range(500):
        data = _items(client, limit=1, **({"cursor": cursor} if cursor else {}))
        if not data["items"]:
            break
        assert data["count"] == 1
        seen.append(tuple(data["items"][0][k] for k in ("date", "space", "album", "file")))
        cursor = data["next_cursor"]
        if cursor is None:
            break
    # keine Duplikate, alles abgegrast, Reihenfolge stabil absteigend
    assert len(seen) == len(set(seen)) >= 2
    full = _items(client, limit=500)["items"]
    assert [tuple(i[k] for k in ("date", "space", "album", "file")) for i in full] == seen
    assert client.get("/api/timeline?cursor=%%%").status_code == 400


def test_timeline_invalidated_by_album_update(client, login, personal_album):
    login()
    url = f"/api/album/personal/{quote(personal_album)}"
    _items(client)  # Index aufbauen

    # p2 aus dem Album referenzieren wir ab jetzt nicht mehr
    data = client.get(url).json()
    album = {
        "version": "1.4", "meta": data["meta"],
        "elements": [{"id": "0001", "type": "photo", "file": "p1.jpg"}],
    }
    assert client.post(f"{url}/update", json=album).status_code == 200

    after = {i["file"]: i for i in _items(client)["items"] if i["album"] == personal_album}
    assert after["p1.jpg"]["referenced"] is True
    assert after["p2.jpg"]["referenced"] is False  # Datei bleibt, nur unkuratiert


def test_timeline_is_base_package(client, users_file):
    """Grundpaket (03.09.2026): Auch ein Konto OHNE Zusatzmodule sieht
    den Zeitstrahl — er ist die Grund-Blätteransicht."""
    _upsert_user("timo", "timo1234567", "viewer", ["stats"])
    r = client.post("/api/login", json={"username": "timo", "password": "timo1234567"})
    assert r.status_code == 200
    assert client.get("/api/timeline").status_code == 200


def test_timeline_filter_space_album_referenced(client, login, personal_album):
    """Additiv 08.09.2026: space/album/referenced fuer die Durchlauf-Ansicht."""
    from tests.conftest import _TMP
    base = _TMP / f"personal-{ADMIN[0]}"
    make_jpeg(base / "2034" / "2034-03-03_10-00-00.jpg")        # Durchlauf
    make_jpeg(base / personal_album / "lose.jpg")                # unzugeordnet im Album
    timeline_index.invalidate_timeline_index()
    login()

    unref = _items(client, space="personal", referenced="false")["items"]
    files = {(i["album"], i["file"]) for i in unref}
    assert ("2034", "2034-03-03_10-00-00.jpg") in files
    assert (personal_album, "lose.jpg") in files
    assert not any(i["referenced"] for i in unref)
    assert all(i["space"] == "personal" for i in unref)

    nur_r = _items(client, space="personal", album="2034")
    assert [i["file"] for i in nur_r["items"]] == ["2034-03-03_10-00-00.jpg"]
    assert nur_r["total"] == 1

    ref = _items(client, space="personal", album=personal_album, referenced="true")["items"]
    assert {i["file"] for i in ref} == {"p1.jpg", "p2.jpg"}

    r = client.get("/api/timeline?space=egal")
    assert r.status_code == 400


def test_timeline_folder_kind_und_filter(client, login, personal_album):
    """Ordnerart je Eintrag (08.09.2026): album / durchlauf / folder — und
    ein kopierter Ordner ohne album.json zaehlt NICHT als Durchlauf-Rest."""
    from tests.conftest import _TMP
    base = _TMP / f"personal-{ADMIN[0]}"
    make_jpeg(base / "2038" / "a.jpg")
    make_jpeg(base / "2038 - Kopiert" / "b.jpg")
    timeline_index.invalidate_timeline_index()
    login()
    alle = _items(client, space="personal", referenced="false")["items"]
    kinds = {(i["album"], i["file"]): i["folder"] for i in alle}
    assert kinds[("2038", "a.jpg")] == "durchlauf"
    assert kinds[("2038 - Kopiert", "b.jpg")] == "folder"
    ref = _items(client, space="personal", album=personal_album)["items"]
    assert ref and all(i["folder"] == "album" for i in ref)

    nur = _items(client, space="personal", referenced="false", folder="durchlauf,album")["items"]
    assert ("2038 - Kopiert", "b.jpg") not in {(i["album"], i["file"]) for i in nur}
    assert ("2038", "a.jpg") in {(i["album"], i["file"]) for i in nur}
    assert client.get("/api/timeline?folder=egal").status_code == 400
