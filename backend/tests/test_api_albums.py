"""Album-API: Anlage, Lesen, Rollen, Pfad-Härtung."""
from urllib.parse import quote

from tests.conftest import ADMIN, VIEWER


def test_create_and_read_album(client, login, personal_album):
    r = client.get(f"/api/album/personal/{quote(personal_album)}")
    assert r.status_code == 200
    data = r.json()
    assert data["space"] == "personal"
    assert data["read_only"] is False
    files = {e["file"] for e in data["elements"]}
    assert files == {"p1.jpg", "p2.jpg"}
    assert all(e["type"] == "photo" for e in data["elements"])


def test_album_appears_in_listing(client, login, personal_album):
    r = client.get("/api/albums")
    assert r.status_code == 200
    names = [a.get("folder_name") or a.get("name") for a in r.json().get("albums", r.json() if isinstance(r.json(), list) else [])]
    assert any(personal_album in (n or "") for n in names), f"{personal_album} fehlt in {names}"


def test_viewer_darf_im_eigenen_bereich_anlegen(client, login):
    """Betrachter-Reparatur 06.09.2026: Der eigene Bereich gehoert dem
    Konto. Woran das Anlegen danach scheitert (fehlendes Cover o. ae.),
    ist hier egal — es darf nur kein 403 mehr sein."""
    login(user=VIEWER[0], pw=VIEWER[1])
    r = client.post("/api/album/create", json={
        "space": "personal", "folder_name": "egal", "thumbnail": "x.jpg",
    })
    assert r.status_code != 403


def test_viewer_cannot_create_album_in_shared(client, login):
    """Im Familienbestand bleibt er Zuschauer."""
    login(user=VIEWER[0], pw=VIEWER[1])
    r = client.post("/api/album/create", json={
        "space": "shared", "folder_name": "egal", "thumbnail": "x.jpg",
    })
    assert r.status_code == 403


def test_create_duplicate_album_409(client, login, personal_album):
    r = client.post("/api/album/create", json={
        "space": "personal", "folder_name": personal_album, "thumbnail": "p1.jpg",
    })
    assert r.status_code == 409


def test_album_path_traversal_blocked(client, login):
    login()
    r = client.get("/api/album/personal/" + quote("../geheim"))
    assert r.status_code in (400, 404)


def test_update_preserves_separator_style_and_map_layer(client, login, personal_album):
    """Regression: 'style' (separator) und 'layer' (map) fehlten in der
    ALLOWED-Whitelist von update_album — jeder Save hat die Trenner-Stile
    und die Kartengrundlage-Wahl still verworfen (gefunden beim
    App-Nachbau, 01.09.2026)."""
    url = f"/api/album/personal/{quote(personal_album)}"
    data = client.get(url).json()

    album = {
        "version": "1.4",
        "meta": data["meta"],
        "elements": [
            {"id": "0001", "type": "photo", "file": "p1.jpg"},
            {"id": "0002", "type": "separator", "style": "dashed", "label": "Abschnitt"},
            {"id": "0003", "type": "map", "title": "Karte", "layer": "topo",
             "pins": [{"lat": 43.77, "lon": 11.25}]},
        ],
    }
    r = client.post(f"{url}/update", json=album)
    assert r.status_code == 200, r.text

    saved = {e["id"]: e for e in client.get(url).json()["elements"]}
    assert saved["0002"].get("style") == "dashed"
    assert saved["0002"].get("label") == "Abschnitt"
    assert saved["0003"].get("layer") == "topo"


def _minimal_album(meta):
    return {
        "version": "1.4", "meta": meta,
        "elements": [{"id": "0001", "type": "photo", "file": "p1.jpg"}],
    }


def test_update_conflict_guard_opt_in(client, login, personal_album):
    """Opt-in-Konfliktschutz (09/2026): X-MPD-Base-Modified passt → 200,
    veraltet → 409, ohne Header → unverändertes Verhalten."""
    url = f"/api/album/personal/{quote(personal_album)}"
    data = client.get(url).json()

    # Passender Stand → gespeichert, Antwort liefert neuen Stand
    r = client.post(f"{url}/update", json=_minimal_album(data["meta"]),
                    headers={"X-MPD-Base-Modified": data["meta"]["modified"]})
    assert r.status_code == 200
    assert r.json()["modified"] == r.json()["timestamp"]

    # Veralteter Stand → 409 mit aktuellem Stand im Fehlertext
    r = client.post(f"{url}/update", json=_minimal_album(data["meta"]),
                    headers={"X-MPD-Base-Modified": "2000-01-01T00:00:00"})
    assert r.status_code == 409
    assert "geändert" in r.json()["detail"]

    # Ohne Header: Last-Write-Wins wie bisher (Web-Verhalten)
    r = client.post(f"{url}/update", json=_minimal_album(data["meta"]))
    assert r.status_code == 200

def test_pdx15_heading_source_and_version(client, login, personal_album):
    """PDX v1.5: heading-Stil und quote-source überleben das Speichern,
    die Version folgt dem Inhalt (1.5 nur bei genutzter Fähigkeit)."""
    url = f"/api/album/personal/{quote(personal_album)}"
    meta = client.get(url).json()["meta"]

    album = {
        "version": "1.5", "meta": meta,
        "elements": [
            {"id": "0001", "type": "text", "text": "Kapitel Eins", "style": "heading"},
            {"id": "0002", "type": "text", "text": "Ein *kursives* und **fettes** Zitat",
             "style": "quote", "source": "Anna, 2026"},
            {"id": "0003", "type": "photo", "file": "p1.jpg"},
        ],
    }
    assert client.post(f"{url}/update", json=album).status_code == 200

    saved = client.get(url).json()
    import json as _json, config
    base = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}"
    raw = _json.loads((base / personal_album / "album.json").read_text(encoding="utf-8"))
    assert raw["version"] == "1.5"
    by_id = {e["id"]: e for e in saved["elements"]}
    assert by_id["0001"]["style"] == "heading"
    assert by_id["0002"]["source"] == "Anna, 2026"
    assert "**fettes**" in by_id["0002"]["text"]  # Server rendert/strippt nichts

    # Ohne v1.5-Fähigkeiten fällt die Version wieder zurück
    album["elements"] = [{"id": "0003", "type": "photo", "file": "p1.jpg"}]
    assert client.post(f"{url}/update", json=album).status_code == 200
    raw = _json.loads((base / personal_album / "album.json").read_text(encoding="utf-8"))
    assert raw["version"] == "1.4"


def test_durchlauf_ist_kein_album_kandidat(client, login):
    """Nackte Jahreszahl = Durchlauf (02.09.2026). Er steht nicht unter
    „Album hinzufuegen" und laesst sich auch per API nicht in ein Album
    verwandeln — sonst haengt der naechste Kamera-Upload als Element
    direkt hinein."""
    from tests.conftest import _TMP, make_jpeg
    base = _TMP / f"personal-{ADMIN[0]}"
    make_jpeg(base / "2031" / "2031-01-01_10-00-00.jpg")
    make_jpeg(base / "2031 - Echtes Album" / "a.jpg")
    login()

    r = client.get("/api/albums/without-json")
    assert r.status_code == 200
    names = [f["folder_name"] for f in r.json()["folders"]]
    assert "2031" not in names, names
    assert "2031 - Echtes Album" in names, names

    r = client.post("/api/album/create", json={
        "space": "personal", "folder_name": "2031", "thumbnail": "2031-01-01_10-00-00.jpg",
    })
    assert r.status_code == 400, r.text
    r = client.post("/api/album/create-empty", json={"space": "personal", "folder_name": "2032"})
    assert r.status_code == 400, r.text
    assert not (base / "2032").exists()


def test_unassigned_count_in_album_antwort(client, login, personal_album):
    """Dezenter Hinweis (08.09.2026): Die Album-Antwort sagt, wie viele
    Mediendateien im Ordner noch in keinem Element stehen. Begleitdateien
    zaehlen nicht."""
    from tests.conftest import _TMP, make_jpeg
    base = _TMP / f"personal-{ADMIN[0]}" / personal_album
    r = client.get(f"/api/album/personal/{quote(personal_album)}")
    assert r.json()["unassigned_count"] == 0
    make_jpeg(base / "p3.jpg")
    (base / "p3.jpg.supplemental-metadata.json").write_text("{}")
    r = client.get(f"/api/album/personal/{quote(personal_album)}")
    assert r.json()["unassigned_count"] == 1
    u = client.get(f"/api/album/personal/{quote(personal_album)}/unassigned-photos").json()
    assert [x["file"] for x in u["unassigned"]] == ["p3.jpg"] and u["count"] == 1


def test_unassigned_photos_traegt_thumbnails(client, login, personal_album):
    """08.09.2026: der Einfuege-Dialog bekommt thumbnails/original mit —
    das Web haengt das Element sofort in die Ansicht."""
    from tests.conftest import _TMP, make_jpeg
    base = _TMP / f"personal-{ADMIN[0]}" / personal_album
    make_jpeg(base / "neu.jpg")
    login()
    u = client.get(f"/api/album/personal/{quote(personal_album)}/unassigned-photos").json()
    e = next(x for x in u["unassigned"] if x["file"] == "neu.jpg")
    assert e["thumbnails"]["sm"].startswith(f"/api/thumbnail/personal/{quote(personal_album)}/neu.jpg?size=sm")
    assert "&v=" in e["thumbnails"]["sm"]
    assert e["original"].endswith("/neu.jpg/download")
