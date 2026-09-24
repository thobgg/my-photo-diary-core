"""core/events (Block A, 09.09.2026): Der Kern meldet, Module haengen sich
ein — und der Kern importiert kein Modul mehr."""
import re
from pathlib import Path

import pytest

from core import events
from tests.conftest import modul_da

_OHNE_SUCHE = pytest.mark.skipif(not modul_da("search") or not modul_da("stats"),
                                 reason="Such-/Stats-Modul nicht mitgeliefert")


def test_subscribe_emit_und_fehler_werden_geschluckt():
    got = []
    def ok(**kw): got.append(kw)
    def kaputt(**kw): raise RuntimeError("Modul kaputt")
    events.subscribe("test_ev", ok)
    events.subscribe("test_ev", kaputt)
    events.subscribe("test_ev", ok)          # doppelt = einmal
    n = events.emit("test_ev", base="/x", album="A")
    assert n == 1                            # nur der heile Handler zaehlt
    assert got == [{"base": "/x", "album": "A"}]
    assert events.emit("gibt_es_nicht") == 0


def test_kern_importiert_keine_module():
    """Regression: albums/album_files/photo_edit duerfen weder das
    Suchmodul noch stats_geo importieren — sonst startet der Kern ohne
    das Modul nicht."""
    root = Path(__file__).resolve().parent.parent
    verboten = re.compile(r"^\s*(from|import)\s+(routers\.(search|stats|tours|trails|memories)|stats_geo|stats_scanner|stats_text)\b", re.M)
    for f in ("routers/albums.py", "routers/album_files.py", "routers/photo_edit.py",
              "routers/media.py", "routers/share.py", "routers/timeline.py"):
        src = (root / f).read_text(encoding="utf-8")
        assert not verboten.search(src), f"{f} importiert ein Modul"


@_OHNE_SUCHE
def test_suchmodul_haengt_am_ereignis():
    names = [h.__name__ for h in events.handlers("album_changed")]
    assert "_on_album_changed" in names
    from routers import stats  # noqa: F401 — laedt stats_geo mit
    import stats_geo  # noqa: F401
    names = [h.__name__ for h in events.handlers("photo_meta_changed")]
    assert "_on_photo_meta_changed" in names


@_OHNE_SUCHE
def test_album_update_entwertet_suchindex_ueber_ereignis(client, login, personal_album, tmp_path, monkeypatch):
    """Der Weg Ende-zu-Ende: Album speichern → album_changed → Suchindex weg.
    Indexdatei nach tmp umgebogen — nie die echte anfassen."""
    from urllib.parse import quote
    from routers import search
    idx = tmp_path / "mpd_search_index.json"
    monkeypatch.setattr(search, "_INDEX_FILE", idx)
    idx.write_text("{}", encoding="utf-8")
    login()
    url = f"/api/album/personal/{quote(personal_album)}"
    data = client.get(url).json()
    r = client.post(f"{url}/update", json={"version": "1.4", "meta": data["meta"],
                                           "elements": [{k: v for k, v in e.items() if k in ("id", "type", "file")} for e in data["elements"]]})
    assert r.status_code == 200, r.text
    assert not idx.exists()
