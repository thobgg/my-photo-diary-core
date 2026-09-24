"""GET /api/photos/geo — gebündelte Foto-Koordinaten (05.09.2026)
und der date-Filter auf /api/timeline."""
import json

import config
from tests.conftest import ADMIN, VIEWER, make_jpeg


def _write_stats_cache(album_dir, photos, scanned_at="2026-09-05T14:20:00"):
    """Statistik-Cache je Album schreiben — die Route liest ihn nur,
    sie scannt nie selbst."""
    (album_dir / ".mpd_stats.json").write_text(json.dumps({
        "album": album_dir.name, "photo_count": len(photos),
        "scanned_at": scanned_at, "photos": photos,
    }), encoding="utf-8")


def _album(name="2016 - Geo"):
    base = config.USERS_JSON_PATH.parent / f"personal-{ADMIN[0]}" / name
    make_jpeg(base / "2016-03-13_14-30-15-th.jpg")
    (base / "album.json").write_text(json.dumps({
        "version": "1.4", "meta": {"title": name}, "elements": [],
    }), encoding="utf-8")
    return base


def test_geo_returns_coordinates_and_honours_date(client, login):
    login()
    d = _album()
    _write_stats_cache(d, [
        {"file": "2016-03-13_14-30-15-th.jpg", "date": "2016-03-13", "hour": 14,
         "has_gps": True, "lat": 48.1372, "lon": 11.5755},
        {"file": "andere.jpg", "date": "2018-07-01", "hour": 9,
         "has_gps": True, "lat": 53.5, "lon": 10.0},
        {"file": "ohne-gps.jpg", "date": "2016-03-13", "hour": 15, "has_gps": False},
    ])
    r = client.get("/api/photos/geo?space=personal")
    assert r.status_code == 200, r.text
    data = r.json()
    # Nur Fotos MIT Koordinate
    assert data["count"] == 2 and data["total"] == 2
    assert data["truncated"] is False          # immer mitgeschickt
    assert data["stand"] == "2026-09-05T14:20:00"
    files = {p["file"] for p in data["photos"]}
    assert "ohne-gps.jpg" not in files

    # Zeitpunkt sekundengenau aus dem Dateinamen (Cache kennt nur Stunden)
    p = [x for x in data["photos"] if x["file"].startswith("2016-03-13")][0]
    assert p["dt"] == "2016-03-13T14:30:15"
    assert p["space"] == "personal" and p["album"] == d.name
    assert (p["lat"], p["lon"]) == (48.1372, 11.5755)

    # Tagesfilter
    r = client.get("/api/photos/geo?space=personal&date=2016-03-13")
    assert r.json()["count"] == 1
    # Zeitraum
    r = client.get("/api/photos/geo?space=personal&from=2017-01-01&to=2019-01-01")
    assert r.json()["count"] == 1 and r.json()["photos"][0]["file"] == "andere.jpg"


def test_geo_bbox_limit_and_truncated(client, login):
    login()
    d = _album("2020 - Geo Box")
    _write_stats_cache(d, [
        {"file": f"p{i}.jpg", "date": "2020-05-05", "hour": 10,
         "has_gps": True, "lat": 48.0 + i / 100, "lon": 11.0}
        for i in range(5)
    ])
    r = client.get("/api/photos/geo?space=personal&bbox=47.9,10.9,48.025,11.1")
    assert r.status_code == 200
    assert r.json()["count"] == 3          # 48.00, 48.01, 48.02

    r = client.get("/api/photos/geo?space=personal&limit=2")
    data = r.json()
    assert data["count"] == 2 and data["total"] >= 5
    assert data["truncated"] is True


def test_geo_never_scans_and_reports_unscanned(client, login):
    """Kein Scan als Nebenwirkung: ein Album ohne Cache liefert keine
    Koordinaten, wird aber ehrlich gezählt."""
    login()
    d = _album("2021 - Ungescannt")
    assert not (d / ".mpd_stats.json").exists()
    r = client.get("/api/photos/geo?space=personal")
    assert r.status_code == 200
    assert r.json()["albums_unscanned"] >= 1
    assert not (d / ".mpd_stats.json").exists()   # weiterhin kein Scan


def test_geo_respects_space_rights(client, login):
    """Viewer darf Shared lesen (B1), Demo nicht — und fremde Personal-
    Bereiche sieht sowieso niemand."""
    login(VIEWER[0], VIEWER[1])
    assert client.get("/api/photos/geo?space=shared").status_code == 200
    assert client.get("/api/photos/geo?space=quatsch").status_code == 403


def test_geo_requires_login(client):
    client.cookies.clear()
    assert client.get("/api/photos/geo").status_code == 401


def test_timeline_date_filter(client, login, personal_album):
    login()
    r = client.get("/api/timeline?limit=200")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "Zeitstrahl leer — Fixture kaputt?"
    day = items[0]["date"][:10]

    r = client.get(f"/api/timeline?date={day}")
    assert r.status_code == 200
    got = r.json()["items"]
    assert got and all(i["date"].startswith(day) for i in got)

    assert client.get("/api/timeline?date=1999-01-01").json()["items"] == []
    assert client.get("/api/timeline?date=quatsch").status_code == 400
