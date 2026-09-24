"""
routers/static.py
My Photo Diary v2 – static files and health endpoint (session-based)
"""

from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from core.i18n import t as _t
from core.license import license_info
from routers.deps import require_session, available_spaces, user_modules


def _app_version() -> str:
    """Spaet importiert: main importiert diesen Router, ein Import auf
    Modulebene waere zirkulaer."""
    from main import VERSION
    return VERSION


def _app_product_version() -> str:
    """Nur die Produktversion (ohne Build) — dieselbe Spaet-Import-Regel."""
    from main import PRODUCT_VERSION
    return PRODUCT_VERSION

router = APIRouter()

_FRONTEND = Path(__file__).parent.parent.parent / "frontend"


# HTML entry routes ship with no-store so WebAPK / browser tabs always
# re-fetch the markup (and thus the latest ?v=N script tags). Pairs with
# frontend/js/version-watch.js — without no-store on HTML, a stale cached
# page never even loads the version watcher.
# HTML immer ohne Zwischenspeicher ausliefern: Die Seiten verweisen per
# ?v= auf ihre Skripte — wird das HTML selbst gecacht, hilft der beste
# Cache-Buster nichts. /tours und /trails fehlte das bis 05.09.2026;
# der MPDTours-WebView zeigte deshalb tagelang eine alte Fassung.
_HTML_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/")
async def root(request: Request):
    path = _FRONTEND / "index.html"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Frontend"}))
    return FileResponse(path, headers=_HTML_NO_STORE)


@router.get("/album.html")
async def album_page(request: Request):
    path = _FRONTEND / "album.html"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Album"}))
    return FileResponse(path, headers=_HTML_NO_STORE)


@router.get("/trail")
async def trail_page(request: Request):
    """Alt-Adresse aus der Dawarich-Zeit (05.09.2026). Die Seite lag früher
    im Dawarich-Volume und sprach dessen API; seit MPD Trails selbst führt,
    gibt es hier nichts mehr zu holen — alte Lesezeichen landen auf der
    MPD-eigenen Trails-Seite."""
    return RedirectResponse("/trails", status_code=302)

@router.get("/hilfe")
async def hilfe_page(request: Request):
    """Benutzerhandbuch (04.09.2026) — für alle eingeloggten Rollen,
    zweisprachig, in sich geschlossen. Liegt bewusst im bind-gemounteten
    frontend/ (nicht docs/), damit Änderungen ohne Image-Rebuild live gehen."""
    path = _FRONTEND / "hilfe.html"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Hilfe"}))
    return FileResponse(path, headers=_HTML_NO_STORE)


@router.get("/hilfe.pdf")
async def hilfe_pdf(request: Request):
    """Das Benutzerhandbuch als Druckfassung, verlinkt aus /hilfe.

    Liegt in `frontend/`, nicht in `docs/`: Von dort geht es ohne eigenen
    Schritt ins Paket (build.sh rsynct `frontend/` komplett) und ist im
    bind-gemounteten Betrieb ohne Image-Neubau aktuell. Erzeugt wird es aus
    `docs/specs/MPD_Benutzerhandbuch_v1_0.tex` — der Bauhinweis steht in
    dessen Kopf."""
    path = _FRONTEND / "MPD-Benutzerhandbuch.pdf"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Handbuch"}))
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition":
                                 'inline; filename="MPD-Benutzerhandbuch.pdf"'})


@router.get("/tours")
async def tours_page(request: Request):
    path = _FRONTEND / "tours.html"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Tours"}))
    return FileResponse(path, headers=_HTML_NO_STORE)


@router.get("/verbinden")
async def verbinden_page(request: Request):
    """„App verbinden" (11.09.2026): zeigt die eigene Adresse als QR-Code,
    damit sie auf dem Handy nicht abgetippt werden muss."""
    path = _FRONTEND / "verbinden.html"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Verbinden"}))
    return FileResponse(path, headers=_HTML_NO_STORE)


@router.get("/durchlauf")
async def durchlauf_page(request: Request):
    """Durchlauf-Ansicht (08.09.2026): alles Unzugeordnete eines Bestands,
    zum Einsortieren und Aufraeumen. Grundpaket, kein Modul."""
    path = _FRONTEND / "durchlauf.html"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Durchlauf"}))
    return FileResponse(path, headers=_HTML_NO_STORE)


@router.get("/trails")
async def trails_page(request: Request):
    path = _FRONTEND / "trails.html"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Trails"}))
    return FileResponse(path, headers=_HTML_NO_STORE)


@router.get("/css/{filename}")
async def get_css(filename: str, request: Request):
    """CSS wie JS ohne Zwischenspeicher (13.09.2026).

    Bis heute ging CSS mit den Vorgabe-Headern raus. Das `?v=N` im HTML war
    damit die EINZIGE Absicherung gegen alte Dateien — anders als bei /js/
    und HTML, die `no-store` bekommen, und anders als von ARCHITEKTUR.md
    beschrieben („zwei Netze fangen das ab"). CSS lag in keinem der beiden.

    Aufgefallen an einer Aenderung am Zuschnitt-Editor: Das JS lief, das
    zugehoerige CSS kam beim Nutzer nie an, weil die Nummer nicht
    hochgesetzt war — und ohne Header merkt das niemand, auch nicht mit
    hartem Reload. Die elf CSS-Dateien sind zusammen 260 KB, das faellt
    gegen eine falsch dargestellte Oberflaeche nicht ins Gewicht.

    Die Nummer im HTML bleibt trotzdem Pflicht: Sie ist das, was Proxys und
    der WebView-Cache sehen."""
    path = _FRONTEND / "css" / filename
    if not path.exists() or path.suffix != ".css":
        raise HTTPException(404, _t("static.not_found", request, {"kind": "CSS"}))
    return FileResponse(path, media_type="text/css", headers={"Cache-Control": "no-store"})


@router.get("/img/{filename}")
async def get_img(filename: str, request: Request):
    path = _FRONTEND / "img" / filename
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Image"}))
    suffix = path.suffix.lower()
    media_types = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg"}
    return FileResponse(path, media_type=media_types.get(suffix, "application/octet-stream"))


@router.get("/img/brand/{filename}")
async def get_brand_img(filename: str, request: Request):
    base = (_FRONTEND / "img" / "brand").resolve()
    path = (base / filename).resolve()
    if not path.is_file() or base not in path.parents:
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Brand asset"}))
    suffix = path.suffix.lower()
    media_types = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}
    return FileResponse(path, media_type=media_types.get(suffix, "application/octet-stream"))


@router.get("/manifest.webmanifest")
async def get_manifest(request: Request):
    path = _FRONTEND / "manifest.webmanifest"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Manifest"}))
    return FileResponse(path, media_type="application/manifest+json")


@router.get("/apple-touch-icon.png")
async def apple_touch_icon(request: Request):
    path = _FRONTEND / "img" / "brand" / "mpd-icon-180.png"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Apple touch icon"}))
    return FileResponse(path, media_type="image/png")


@router.get("/favicon.ico")
async def favicon(request: Request):
    path = _FRONTEND / "img" / "brand" / "mpd-icon-32.png"
    if not path.exists():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Favicon"}))
    return FileResponse(path, media_type="image/png")


# Fremdbibliotheken mit Versionsnummer im Inhalt, nicht im Namen: sie
# aendern sich nur, wenn wir sie bewusst austauschen. `no-store` waere hier
# teuer — pdf.worker.min.js allein ist 1,06 MB und wuerde bei jedem Oeffnen
# eines PDFs neu uebertragen, auf dem Handy auch ueber Mobilfunk.
_JS_IMMUTABLE = frozenset({"pdf.min.js", "pdf.worker.min.js"})
_JS_IMMUTABLE_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


@router.get("/js/{filename}")
async def get_js(filename: str, request: Request):
    path = _FRONTEND / "js" / filename
    if not path.exists() or path.suffix != ".js":
        raise HTTPException(404, _t("static.not_found", request, {"kind": "JS"}))
    headers = (_JS_IMMUTABLE_CACHE if filename in _JS_IMMUTABLE
               else {"Cache-Control": "no-store"})
    return FileResponse(path, media_type="application/javascript", headers=headers)


# Hausschrift (CI 1.2): selbst gehostete woff2 unter frontend/fonts/.
# Dateinamen tragen Familie, Gewicht und Subset; eine Schrift aendert sich
# nur, wenn wir sie bewusst austauschen — dann bekommt sie einen neuen
# Namen. Deshalb ein Jahr immutable, wie die pdf.js-Dateien oben.
_FONT_TYPES = {".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}


@router.get("/fonts/{filename}")
async def get_font(filename: str, request: Request):
    base = (_FRONTEND / "fonts").resolve()
    path = (base / filename).resolve()
    if not path.is_file() or base not in path.parents or path.suffix not in _FONT_TYPES:
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Font"}))
    return FileResponse(path, media_type=_FONT_TYPES[path.suffix], headers=_JS_IMMUTABLE_CACHE)


@router.get("/health")
async def health(request: Request):
    session = require_session(request)
    spaces  = available_spaces(session)
    return {
        "app"     : "My Photo Diary",
        # Eine Quelle fuer die App-Version: main.VERSION, gespeist aus
        # backend/version.txt (post-commit-Hook) mit Git-Rueckfall. Hier stand
        # fest "2.0.0", waehrend /api/version laengst 2.0.135 meldete.
        "version" : _app_version(),
        "product" : _app_product_version(),
        "status"  : "running",
        "role"    : session.role,
        "user"    : session.user,
        "shared"  : "shared" in spaces,
        "personal": "personal" in spaces,
        "modules" : sorted(user_modules(session)),
        # Additiv 09/2026: Lizenzstatus (Basis frei, Module kosten)
        "license" : license_info(),
    }
