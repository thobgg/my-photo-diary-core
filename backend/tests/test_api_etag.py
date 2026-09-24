"""ETag-Validator auf /api/thumbnail und /api/photo/…/download
(additiv 09/2026, offener Punkt aus CLAUDE.md): 200 mit ETag +
Cache-Control no-cache, 304 bei If-None-Match, neuer ETag nach
mtime-Änderung, Zusammenspiel mit Range."""
import os
from urllib.parse import quote

import config
from tests.conftest import ADMIN


def _thumb_url(album, filename="p1.jpg"):
    return f"/api/thumbnail/personal/{quote(album)}/{filename}?size=sm"


def _dl_url(album, filename="p1.jpg"):
    return f"/api/photo/personal/{quote(album)}/{filename}/download"


def _source(album, filename="p1.jpg"):
    return config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}" / album / filename


def test_thumbnail_sends_etag_and_no_cache(client, login, personal_album):
    r = client.get(_thumb_url(personal_album))
    assert r.status_code == 200
    etag = r.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["content-type"] == "image/webp"


def test_thumbnail_304_on_if_none_match(client, login, personal_album):
    etag = client.get(_thumb_url(personal_album)).headers["etag"]

    r = client.get(_thumb_url(personal_album), headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag

    # Weak-Präfix und Listen müssen ebenfalls treffen
    r = client.get(_thumb_url(personal_album),
                   headers={"If-None-Match": f'W/{etag}, "anderes"'})
    assert r.status_code == 304

    # Fremder ETag → normale 200-Antwort
    r = client.get(_thumb_url(personal_album), headers={"If-None-Match": '"veraltet"'})
    assert r.status_code == 200


def test_thumbnail_etag_changes_on_mtime(client, login, personal_album):
    """Edit-Szenario: mtime ändert sich → neuer ETag, alter Validator
    liefert wieder 200 (das frische Bild), nicht 304."""
    old = client.get(_thumb_url(personal_album)).headers["etag"]

    src = _source(personal_album)
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    r = client.get(_thumb_url(personal_album), headers={"If-None-Match": old})
    assert r.status_code == 200
    assert r.headers["etag"] != old


def test_download_etag_and_304(client, login, personal_album):
    r = client.get(_dl_url(personal_album))
    assert r.status_code == 200
    etag = r.headers["etag"]
    assert r.headers["cache-control"] == "no-cache"

    r = client.get(_dl_url(personal_album), headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""

    # If-None-Match gewinnt vor Range (RFC 9110)
    r = client.get(_dl_url(personal_album),
                   headers={"If-None-Match": etag, "Range": "bytes=0-9"})
    assert r.status_code == 304

    # Range ohne passenden Validator → 206 wie gehabt, ETag liegt bei
    r = client.get(_dl_url(personal_album), headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.headers["etag"] == etag
