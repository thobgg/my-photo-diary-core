"""Memories nach dem ntfy-/Token-Ausbau (03.09.2026): nur noch normale
Session-Auth; die Web-Seite /memories existiert nicht mehr."""
from tests.conftest import ADMIN


def test_memories_requires_session(client):
    assert client.get("/api/memories").status_code == 401
    # Der alte Token-Bypass ist tot — Query-Token ändert nichts
    assert client.get("/api/memories", params={"token": "egal"}).status_code == 401


def test_memories_with_session(client, login):
    login()
    r = client.get("/api/memories")
    assert r.status_code == 200
    body = r.json()
    assert "years" in body and "total" in body


def test_memories_web_page_removed(client, login):
    login()
    assert client.get("/memories").status_code == 404


# ── Memories-Scope pro Nutzer (03.09.2026) ────────────────────────────────

def test_settings_me_roundtrip(client, login):
    login()
    assert client.get("/api/settings/me").json() == {"memories_scope": "shared"}
    r = client.put("/api/settings/me", json={"memories_scope": "personal"})
    assert r.status_code == 200
    assert client.get("/api/settings/me").json() == {"memories_scope": "personal"}
    # ungültiger Wert / fremdes Feld → 400
    assert client.put("/api/settings/me", json={"memories_scope": "alles"}).status_code == 400
    assert client.put("/api/settings/me", json={"role": "admin"}).status_code == 400
    # zurück auf Default (Test-Hygiene: users.json ist session-scoped)
    assert client.put("/api/settings/me", json={"memories_scope": "shared"}).status_code == 200


def test_memories_personal_scope(client, login, personal_album):
    """Scope personal: Scan läuft über den eigenen Personal-Space,
    thumbnail_url trägt space=personal, memories-thumb liefert."""
    from datetime import date as _d
    from urllib.parse import quote
    import config
    from tests.conftest import ADMIN, make_jpeg

    today = _d.today()
    fn = f"{today.year - 1:04d}-{today.month:02d}-{today.day:02d}_10-00-00.jpg"
    base = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}" / personal_album
    make_jpeg(base / fn)

    login()
    url = f"/api/album/personal/{quote(personal_album)}"
    meta = client.get(url).json()["meta"]
    r = client.post(f"{url}/update", json={
        "version": "1.4", "meta": meta,
        "elements": [{"id": "0001", "type": "photo", "file": fn}],
    })
    assert r.status_code == 200, r.text

    assert client.put("/api/settings/me",
                      json={"memories_scope": "personal"}).status_code == 200
    try:
        r = client.get("/api/memories")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1
        photo = body["years"][0]["photos"][0]
        assert "space=personal" in photo["thumbnail_url"]

        r = client.get(photo["thumbnail_url"])
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
    finally:
        client.put("/api/settings/me", json={"memories_scope": "shared"})


def test_memories_stale_serve_and_prewarm(client, login, personal_album):
    """Stale-Serve (03.09.): abgelaufener Cache liefert sofort den alten
    Stand; prewarm_memories füllt den Tages-Schlüssel."""
    import asyncio as _asyncio
    import time as _time
    from datetime import date as _d
    from routers import memories as mem

    login()
    r = client.get("/api/memories")
    assert r.status_code == 200
    key = mem._cache_key_for(_d.today(), "shared", ADMIN[0])
    assert key in mem._MEMORIES_CACHE

    # Ablauf simulieren → Antwort kommt trotzdem sofort (alter Stand)
    ts, results = mem._MEMORIES_CACHE[key]
    mem._MEMORIES_CACHE[key] = (_time.monotonic() - 10_000, results)
    r = client.get("/api/memories")
    assert r.status_code == 200

    # Prewarm füllt den Schlüssel frisch
    mem._MEMORIES_CACHE.clear()
    _asyncio.run(mem.prewarm_memories("test"))
    assert key.rsplit("|", 1)[0] + "" in " ".join(mem._MEMORIES_CACHE) or mem._MEMORIES_CACHE
    assert any(k.startswith(_d.today().isoformat()) for k in mem._MEMORIES_CACHE)


def test_memories_limit_und_total(client, login, personal_album):
    """limit schneidet je Jahr ab, years[].total und total bleiben voll,
    photos tragen time."""
    from datetime import date as _d
    from urllib.parse import quote
    import config
    from tests.conftest import ADMIN, make_jpeg

    today = _d.today()
    y = today.year - 2
    names = [f"{y:04d}-{today.month:02d}-{today.day:02d}_{hh:02d}-00-00.jpg" for hh in (7, 9, 13, 15, 19, 23, 8, 10)]
    base = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}" / personal_album
    for n in names:
        make_jpeg(base / n)
    login()
    url = f"/api/album/personal/{quote(personal_album)}"
    meta = client.get(url).json()["meta"]
    r = client.post(f"{url}/update", json={
        "version": "1.4", "meta": meta,
        "elements": [{"id": f"{i+1:04d}", "type": "photo", "file": n} for i, n in enumerate(names)],
    })
    assert r.status_code == 200, r.text
    assert client.put("/api/settings/me", json={"memories_scope": "personal"}).status_code == 200
    try:
        from routers import memories as _m
        _m._MEMORIES_CACHE.clear()
        full = client.get("/api/memories").json()
        block = next(b for b in full["years"] if b["year"] == y)
        assert block["total"] == 8 and len(block["photos"]) == 8
        # Die Rangfolge geht reihum durch die Tagesabschnitte: je ein Foto,
        # dann die naechste Runde. WELCHES Foto aus einem Abschnitt genommen
        # wird, wuerfelt der Scanner seit df5b62d (13.09.2026) mit dem
        # angezeigten Datum als Startwert — damit der 13. September naechstes
        # Jahr andere Bilder zeigt. Eine feste Reihenfolge zu erwarten hiesse
        # also, den Test vom Kalender abhaengig zu machen; er war es bis
        # heute und waere irgendwann grundlos rot geworden.
        #
        # Geprueft wird deshalb die Eigenschaft, auf die es ankommt: Alle acht
        # Fotos sind da, und die ersten Plaetze verteilen sich ueber den Tag,
        # statt sich in einem Abschnitt zu draengen.
        zeiten = [p["time"] for p in block["photos"]]
        assert sorted(zeiten) == ["07:00", "08:00", "09:00", "10:00",
                                  "13:00", "15:00", "19:00", "23:00"]

        def abschnitt(hhmm):
            h = int(hhmm[:2])
            for name, von, bis in (("morgen", 6, 9), ("vormittag", 9, 12),
                                   ("mittag", 12, 14), ("nachmittag", 14, 18),
                                   ("abend", 18, 24)):
                if von <= h < bis:
                    return name
            return "nacht"

        # Fuenf Abschnitte sind belegt (nachts liegt kein Testfoto), also
        # muessen die ersten fuenf Plaetze fuenf verschiedene treffen.
        assert len({abschnitt(t) for t in zeiten[:5]}) == 5, zeiten

        cut = client.get("/api/memories?limit=6").json()
        block6 = next(b for b in cut["years"] if b["year"] == y)
        assert len(block6["photos"]) == 6 and block6["total"] == 8
        assert cut["total"] == full["total"]
        assert [p["filename"] for p in block6["photos"]] == [p["filename"] for p in block["photos"][:6]]
    finally:
        client.put("/api/settings/me", json={"memories_scope": "shared"})
