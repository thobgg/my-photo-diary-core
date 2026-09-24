"""Share-Tokens: Erzeugen, öffentlicher Zugriff, Widerruf, Rollen."""
from tests.conftest import VIEWER


def _create_share(client, album, space="personal"):
    r = client.post("/api/share/create", json={"space": space, "album_name": album})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_share_create_and_public_access(client, login, personal_album, _app):
    login()
    token = _create_share(client, personal_album)
    assert len(token) > 20  # urlsafe-Token, kein Platzhalter

    # Öffentlicher Zugriff: KEIN Login, frischer Client
    from fastapi.testclient import TestClient
    anon = TestClient(_app)
    r = anon.get(f"/share/{token}/api/album")
    assert r.status_code == 200
    files = {e["file"] for e in r.json()["elements"]}
    assert files == {"p1.jpg", "p2.jpg"}


def test_share_invalid_token_404(client):
    r = client.get("/share/kein-echter-token/api/album")
    assert r.status_code == 404


def test_share_revoke_kills_access(client, login, personal_album, _app):
    login()
    token = _create_share(client, personal_album)

    r = client.post("/api/share/revoke", json={"token": token})
    assert r.status_code == 200

    from fastapi.testclient import TestClient
    anon = TestClient(_app)
    assert anon.get(f"/share/{token}/api/album").status_code == 404


def test_viewer_cannot_share_shared_space(client, login):
    login(user=VIEWER[0], pw=VIEWER[1])
    r = client.post("/api/share/create", json={"space": "shared", "album_name": "egal"})
    assert r.status_code == 403


def test_share_create_requires_login(client):
    r = client.post("/api/share/create", json={"space": "personal", "album_name": "x"})
    assert r.status_code == 401


def test_share_video_supports_range(client, login, personal_album, _app):
    """Audit-Fund: Share versprach Accept-Ranges, lieferte aber kein 206."""
    import config
    from fastapi.testclient import TestClient
    from tests.conftest import ADMIN

    base = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}" / personal_album
    (base / "clip.mp4").write_bytes(bytes(range(200)))

    login()
    # Share liefert nur referenzierte Dateien — Video erst ins Album
    from urllib.parse import quote
    url_a = f"/api/album/personal/{quote(personal_album)}"
    meta = client.get(url_a).json()["meta"]
    r = client.post(f"{url_a}/update", json={
        "version": "1.4", "meta": meta,
        "elements": [{"id": "0001", "type": "video", "file": "clip.mp4"}],
    })
    assert r.status_code == 200, r.text
    token = _create_share(client, personal_album)
    anon = TestClient(_app)
    url = f"/share/{token}/video/{personal_album}/clip.mp4"
    r = anon.get(url, headers={"Range": "bytes=0-49"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 0-49/200"
    assert r.content == bytes(range(50))


# ── Teilen v3 (04.09.2026): Einzelfoto-Shares ─────────────────────────────

def _create_photo_share(client, album, file, **extra):
    r = client.post("/api/share/create", json={
        "space": "personal", "album_name": album, "file": file, **extra,
    })
    return r


def test_photo_share_serves_only_that_photo(client, login, personal_album, _app):
    login()
    r = _create_photo_share(client, personal_album, "p1.jpg")
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    from fastapi.testclient import TestClient
    anon = TestClient(_app)
    # Gastseite ist die Foto-Seite (Download-Link drin)
    r = anon.get(f"/share/{token}")
    assert r.status_code == 200
    assert f"/share/{token}/download" in r.text
    # Thumbnail des geteilten Fotos kommt …
    r = anon.get(f"/share/{token}/thumbnail/{personal_album}/p1.jpg?size=sm")
    assert r.status_code == 200
    # … das Nachbar-Foto und das Album-JSON nicht
    assert anon.get(f"/share/{token}/thumbnail/{personal_album}/p2.jpg?size=sm").status_code == 404
    assert anon.get(f"/share/{token}/api/album").status_code == 404
    # Original-Download als attachment
    r = anon.get(f"/share/{token}/download")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")


def test_photo_share_rejects_bad_files(client, login, personal_album):
    login()
    # kein Foto
    assert _create_photo_share(client, personal_album, "album.json").status_code == 400
    # existiert nicht
    assert _create_photo_share(client, personal_album, "nixda.jpg").status_code == 404
    # Traversal
    assert _create_photo_share(client, personal_album, "..%2Fusers.jpg").status_code in (400, 404)


def test_photo_share_respects_locked(client, login, personal_album):
    login()
    # p1 als privat markieren
    url = f"/api/album/personal/{personal_album}"
    data = client.get(url).json()
    for e in data["elements"]:
        if e.get("file") == "p1.jpg":
            e["locked"] = True
    r = client.post(f"{url}/update", json={
        "version": data.get("version", "1.4"),
        "meta": data["meta"], "elements": data["elements"],
    })
    assert r.status_code == 200, r.text
    # Teilen wird verweigert
    assert _create_photo_share(client, personal_album, "p1.jpg").status_code == 403
    # p2 (nicht gesperrt) geht weiter
    assert _create_photo_share(client, personal_album, "p2.jpg").status_code == 200


def test_photo_share_listed_with_file(client, login, personal_album):
    login()
    assert _create_photo_share(client, personal_album, "p2.jpg").status_code == 200
    r = client.get(f"/api/share/list?space=personal&album_name={personal_album}")
    assert r.status_code == 200
    files = [s.get("file") for s in r.json()["shares"]]
    assert "p2.jpg" in files


# ── Besitzer-Liste ohne Album (05.09.2026) ────────────────────────────────

def test_share_list_without_album_returns_all_own(client, login, personal_album):
    login()
    # Album-Share + Einzelfoto-Share im selben Album
    _create_share(client, personal_album)
    assert _create_photo_share(client, personal_album, "p1.jpg").status_code == 200

    r = client.get("/api/share/list")          # ohne space/album_name
    assert r.status_code == 200, r.text
    shares_ = r.json()["shares"]
    assert len(shares_) >= 2
    # Jeder Eintrag trägt jetzt space + album_name — sonst wäre er nicht anzeigbar
    for s in shares_:
        assert s["space"] and s["album_name"]
    files = {s.get("file") for s in shares_ if s["album_name"] == personal_album}
    assert None in files and "p1.jpg" in files   # Album- UND Foto-Share dabei


def test_share_list_with_album_unchanged(client, login, personal_album):
    login()
    _create_share(client, personal_album)
    r = client.get(f"/api/share/list?space=personal&album_name={personal_album}")
    assert r.status_code == 200
    assert all(s["album_name"] == personal_album for s in r.json()["shares"])


def test_share_list_rejects_half_parameters(client, login, personal_album):
    login()
    assert client.get("/api/share/list?space=personal").status_code == 400
    assert client.get(f"/api/share/list?album_name={personal_album}").status_code == 400


def test_share_list_is_owner_scoped(client, login, personal_album):
    """Fremde Links sieht niemand — auch nicht in der Gesamtliste."""
    login()
    _create_share(client, personal_album)
    client.cookies.clear()
    login(VIEWER[0], VIEWER[1])
    assert client.get("/api/share/list").json()["shares"] == []


# ---------------------------------------------------------------------------
# MIME-Typ beim Einzelfoto-Download (07.09.2026)
# ---------------------------------------------------------------------------

def test_photo_mime_covers_every_photo_extension():
    """Jede Endung aus _PHOTO_EXTS muss in _PHOTO_MIME stehen.

    Der Fehler, der dahinter steckt: `share_photo_download` benutzte
    `_DOC_MIME` — eine ERLAUBNISLISTE fuer Dokumente (pdf/jpg/jpeg/png,
    seit 15.09.2026 auch txt/md), keine MIME-Auskunft. `.heic`, `.gif` und `.webp` fielen durch und
    gingen mit dem Rueckfall `image/jpeg` raus. Dieser Test faengt den
    naechsten Fall: wer _PHOTO_EXTS erweitert, ohne _PHOTO_MIME
    nachzuziehen, bekommt es hier gesagt statt beim Empfaenger.
    """
    from routers.deps import _PHOTO_EXTS, _PHOTO_MIME
    fehlend = _PHOTO_EXTS - set(_PHOTO_MIME)
    assert not fehlend, f"ohne MIME-Typ: {sorted(fehlend)}"
    assert all(v.startswith("image/") for v in _PHOTO_MIME.values())
    # Die drei, die es getroffen hat — namentlich, damit der Test die
    # Regression benennt und nicht nur die Regel.
    assert _PHOTO_MIME[".heic"] == "image/heic"
    assert _PHOTO_MIME[".gif"]  == "image/gif"
    assert _PHOTO_MIME[".webp"] == "image/webp"


def test_doc_erlaubnisliste_bleibt_eine_erlaubnisliste():
    """_DOC_MIME entscheidet, was /api/document ueberhaupt ausliefert (N3
    aus dem Sicherheits-Audit: vorher kam JEDE Datei des Ordners raus —
    album.json, .bak, EXIF-Cache).

    Am 15.09.2026 kamen .txt und .md dazu, damit Textdateien im Albumordner
    sichtbar werden. Dieser Test haelt zwei Zusagen fest: Text geht als
    text/plain raus und NICHT als etwas, das ein Browser auswertet; und die
    Liste bleibt eine Liste — was nicht drinsteht, kommt nicht durch.
    """
    from routers.deps import _DOC_MIME

    assert _DOC_MIME[".txt"].startswith("text/plain")
    assert _DOC_MIME[".md"].startswith("text/plain")
    # Kein Typ, den ein Browser als Auszeichnung oder Skript ausfuehrt.
    for endung, typ in _DOC_MIME.items():
        assert "html" not in typ and "javascript" not in typ and "svg" not in typ, \
            f"{endung} → {typ} waere im Browser ausfuehrbar"
    # Und die Liste bleibt geschlossen.
    for verboten in (".exe", ".sh", ".json", ".py", ".html", ".svg", ".bak"):
        assert verboten not in _DOC_MIME, f"{verboten} darf kein Dokument sein"
