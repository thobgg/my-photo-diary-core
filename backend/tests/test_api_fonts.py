"""Hausschrift (CI 1.2): /fonts/ liefert die selbst gehosteten woff2-Dateien
oeffentlich (Login- und Gastseiten brauchen sie ohne Sitzung), mit langem
Cache, und laesst nichts ausserhalb von frontend/fonts/ heraus."""

from pathlib import Path

FONTS = Path(__file__).resolve().parents[2] / "frontend" / "fonts"


def _any_font() -> str:
    names = sorted(p.name for p in FONTS.glob("*.woff2"))
    assert names, "frontend/fonts/ ist leer"
    return names[0]


def test_font_is_public_and_immutable(client):
    r = client.get(f"/fonts/{_any_font()}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("font/woff2")
    assert "immutable" in r.headers["cache-control"]


def test_font_license_is_served(client):
    r = client.get("/fonts/OFL-archivo.txt")
    assert r.status_code == 200
    assert "SIL Open Font License" in r.text


def test_font_route_refuses_other_files_and_traversal(client):
    assert client.get("/fonts/nope.woff2").status_code == 404
    assert client.get("/fonts/..%2Fcss%2Fstyle.css").status_code in (404, 400)
    assert client.get("/fonts/fonts.css").status_code == 404
