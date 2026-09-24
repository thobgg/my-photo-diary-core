"""Performance-Paket 03.09.2026: versionierte immutable Thumb-URLs (?v=),
BlurHash/v in der Timeline, Stale-Serve des Index, Upload-Thumb-Warm."""
import io
import re
from urllib.parse import quote

from PIL import Image

import config
from core import timeline_index
from core.thumb_pipeline import get_thumb_path, SIZES
from tests.conftest import ADMIN


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 120, 60)).save(buf, "JPEG")
    return buf.getvalue()


def test_album_response_carries_v(client, login, personal_album):
    login()
    r = client.get(f"/api/album/personal/{quote(personal_album)}")
    elem = next(e for e in r.json()["elements"] if e.get("thumbnails"))
    url = elem["thumbnails"]["sm"]
    m = re.search(r"[&?]v=([0-9a-f]{16})$", url)
    assert m, url

    # Passendes v → immutable/1 Jahr; ETag bleibt
    r = client.get(url)
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]
    assert "etag" in r.headers

    # Veraltetes v → defensiv no-cache (kein Poisoning alter URLs)
    stale = url.replace(m.group(1), "0" * 16)
    r = client.get(stale)
    assert r.headers["cache-control"] == "no-cache"

    # Ohne v: unverändert no-cache (Bestandsvertrag)
    r = client.get(url.split("&v=")[0])
    assert r.headers["cache-control"] == "no-cache"


def test_timeline_carries_v_and_blurhash(client, login, personal_album):
    login()
    url = f"/api/album/personal/{quote(personal_album)}"
    meta = client.get(url).json()["meta"]
    r = client.post(f"{url}/update", json={
        "version": "1.4", "meta": meta,
        "elements": [{"id": "0001", "type": "photo", "file": "p1.jpg",
                      "blurhash": "LEHV6nWB2yk8pyo0adR*.7kCMdnj"}],
    })
    assert r.status_code == 200

    items = client.get("/api/timeline?limit=500").json()["items"]
    mine = next(i for i in items
                if i["album"] == personal_album and i["file"] == "p1.jpg")
    assert "&v=" in mine["thumbnails"]["sm"]
    assert mine["blurhash"] == "LEHV6nWB2yk8pyo0adR*.7kCMdnj"


def test_timeline_index_stale_serve_on_ttl(client, login, personal_album):
    """TTL-Ablauf: alter Stand wird sofort geliefert, Neubau läuft im
    Hintergrund. (Schreib-Invalidierung löscht dagegen hart — eigene
    Änderungen sind sofort sichtbar, siehe test_api_timeline.)"""
    login()
    base = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}"
    items_before = timeline_index.get_index(base)
    assert items_before

    # TTL künstlich ablaufen lassen
    with timeline_index._lock:
        timeline_index._index[str(base)] = (0.0, items_before)
    served = timeline_index.get_index(base)
    assert served is items_before  # identisches Objekt = Stale-Serve

    # Hintergrund-Neubau landet irgendwann als frischer Stand
    import time
    for _ in range(50):
        with timeline_index._lock:
            ts, _items = timeline_index._index[str(base)]
        if ts > 0:
            break
        time.sleep(0.1)
    assert ts > 0


def test_upload_warms_thumbs(client, login, personal_album):
    login()
    fn = "2026-09-03_21-00-00.jpg"
    r = client.put(f"/api/album/personal/{quote(personal_album)}/file/{quote(fn)}",
                   content=_jpeg())
    assert r.status_code == 200
    assert "&v=" in r.json()["thumbnails"]["sm"]

    # TestClient läuft die Hintergrund-Task synchron aus, bevor die
    # Response zurückkommt? Nicht garantiert — kurz auf die Dateien warten.
    import time
    base = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}" / personal_album
    src = base / fn
    for _ in range(50):
        if all(get_thumb_path(src, s).is_file() for s in SIZES):
            break
        time.sleep(0.1)
    assert all(get_thumb_path(src, s).is_file() for s in SIZES)
