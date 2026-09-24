"""Media-API: HTTP-Range-Antworten (206) und full-Thumbnail-URL (App-Anbindung 09/2026).

Starlette 0.35 beantwortet Range in FileResponse nicht selbst; die
206-Logik sitzt additiv in core/http_range.py. Hier läuft sie durch die
echte App inklusive Session-Auth.
"""
from urllib.parse import quote


def _download_url(album, filename="p1.jpg"):
    return f"/api/photo/personal/{quote(album)}/{filename}/download"


def test_download_without_range_unchanged(client, login, personal_album):
    """Bisheriges Verhalten: 200, kompletter Body, attachment."""
    r = client.get(_download_url(personal_album))
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    assert len(r.content) > 0


def test_download_range_206(client, login, personal_album):
    full = client.get(_download_url(personal_album)).content
    r = client.get(_download_url(personal_album), headers={"Range": "bytes=0-49"})
    assert r.status_code == 206
    assert r.content == full[:50]
    assert r.headers["content-range"] == f"bytes 0-49/{len(full)}"
    assert r.headers["content-length"] == "50"
    assert r.headers["accept-ranges"] == "bytes"


def test_download_range_open_end(client, login, personal_album):
    """ExoPlayer-typischer Startrequest: bytes=<offset>-"""
    full = client.get(_download_url(personal_album)).content
    r = client.get(_download_url(personal_album), headers={"Range": "bytes=10-"})
    assert r.status_code == 206
    assert r.content == full[10:]
    assert r.headers["content-range"] == f"bytes 10-{len(full) - 1}/{len(full)}"


def test_download_range_unsatisfiable_416(client, login, personal_album):
    full = client.get(_download_url(personal_album)).content
    r = client.get(
        _download_url(personal_album),
        headers={"Range": f"bytes={len(full)}-"},
    )
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(full)}"


def test_download_range_malformed_ignored(client, login, personal_album):
    """Kaputter Range-Header → volle 200-Antwort (RFC 9110)."""
    full = client.get(_download_url(personal_album)).content
    r = client.get(
        _download_url(personal_album), headers={"Range": "bytes=abc-def"}
    )
    assert r.status_code == 200
    assert r.content == full


def test_album_elements_have_full_thumbnail(client, login, personal_album):
    """Additives Feld thumbnails.full (4096 px) für die native App;
    sm/m/xl bleiben unverändert erhalten."""
    r = client.get(f"/api/album/personal/{quote(personal_album)}")
    assert r.status_code == 200
    for elem in r.json()["elements"]:
        thumbs = elem["thumbnails"]
        assert set(thumbs) >= {"sm", "m", "xl", "full"}
        assert "?size=full" in thumbs["full"]  # + &v=<cachekey>
        # full-Variante ist auch tatsächlich abrufbar
        rt = client.get(thumbs["full"])
        assert rt.status_code == 200
        assert rt.headers["content-type"] == "image/webp"
        break
