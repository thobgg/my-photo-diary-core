"""
routers/deps.py
My Photo Diary v2 — shared dependencies for all routers

Session-based: API instances are created per request from the session.
"""

from pathlib import Path
from typing import Optional
from urllib.parse import quote
from fastapi import Request, HTTPException
from core import media_types
from core.filesystem_photos import FilesystemPhotosAPI
from core.i18n import t as _t
from core.session import get_session, request_token, Session
import config
from config import PHOTO_PATH_SHARED


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------

def require_session(request: Request) -> Session:
    """Read session from bearer header (native app) or cookie. 401 when not logged in."""
    token = request_token(request)
    if not token:
        raise HTTPException(401, _t("common.not_logged_in", request))
    session = get_session(token)
    if session is None:
        raise HTTPException(401, _t("common.session_expired", request))
    return session


# ---------------------------------------------------------------------------
# API factory (per request, from session SID)
# ---------------------------------------------------------------------------

def get_api(session: Session, space: str, request: Optional[Request] = None):
    """
    Build a Photos API for the requested space.
    v2: FilesystemPhotosAPI for all users, all spaces.
    """
    if space == "shared" and session.is_demo:
        raise HTTPException(403, _t("common.no_shared_access", request))
    base = get_path(session, space, request)  # raises 404 on unknown space
    return FilesystemPhotosAPI(base=base)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_path(session: Session, space: str, request: Optional[Request] = None) -> Path:
    if space == "shared":
        # Sicherheits-Audit 02.09.2026 (H2): Prüfung hier statt nur in
        # get_api() — etliche Datei-Endpunkte (media, get_album, album_files)
        # nutzen get_path direkt und lieferten Shared-Inhalte ungeprüft aus.
        #
        # Rechtemodell-Entscheidung 04.09.2026 (RECHTEMODELL.md, B1):
        # Der Familienbestand ist für JEDES echte Konto lesbar — auch für
        # `viewer`; das ist die Rolle „darf mitschauen, nichts anfassen".
        # Geschrieben wird davon nichts: is_readonly()/can_contribute()
        # lassen Shared unverändert nur für admin bzw. admin+editor zu.
        #
        # Demo bleibt ausgeschlossen — /demo-login ist ÖFFENTLICH, ein
        # Demo-Konto mit Shared-Lesezugriff wäre der Familienbestand im
        # Internet. Das war der akute Teil von H2.
        if session.is_demo:
            raise HTTPException(403, _t("common.no_shared_access", request))
        return PHOTO_PATH_SHARED
    if space == "personal":
        return session.personal_path
    raise HTTPException(404, _t("common.space_not_available", request, {"space": space}))


def is_readonly(session: Session, space: str) -> bool:
    """Kuratier-Rechte: Alben anlegen/gestalten/löschen, Dateien entfernen.
    Shared kuratiert nur der Admin — bewusst (Kurator-Modell)."""
    if session.is_demo:
        return True  # demo accounts: never editable, regardless of role
    if space == "shared":
        return session.role != "admin"  # only admin may edit Shared
    if space == "personal":
        # Seit 06.09.2026: JEDES Konto verwaltet seinen eigenen Bereich
        # vollstaendig. Vorher war `viewer` auch dort nur Zuschauer — sein
        # persoenlicher Bereich war damit toter Ballast, und die Rolle
        # „darf mitschauen, fuehrt aber sein eigenes Tagebuch" fehlte.
        # Niemand sieht diesen Bereich ausser dem Konto selbst; ein Fehler
        # darin trifft nur eigene Daten.
        return False
    return True


def can_contribute(session: Session, space: str) -> bool:
    """Beitrags-Rechte (C+, Kuratier-Modell 02.09.2026): Hochladen in den
    Durchlauf, Einsortieren/Wiederbeleben, Ausschuss (unreferenzierte
    Dateien) löschen. Editoren dürfen das auch im Shared-Space — das
    Gestalten der Alben bleibt Kurator-Sache (is_readonly)."""
    if session.is_demo:
        return False
    if space == "shared":
        return session.role in ("admin", "editor")
    if space == "personal":
        return True  # der eigene Bereich gehoert dem Konto (s. is_readonly)
    return False


# ---------------------------------------------------------------------------
# Space availability
# ---------------------------------------------------------------------------

def available_spaces(session: Session):
    """Spaces, die dieses Konto SEHEN darf (Schreibrechte regeln
    is_readonly/can_contribute). Seit 04.09.2026 sehen alle echten Konten
    den Familienbestand — nur Demo bleibt auf seinen eigenen Bereich
    beschränkt, weil /demo-login öffentlich ist (s. get_path)."""
    if session.is_demo:
        return ["personal"]
    return ["shared", "personal"]


def may_share(session: Session, space: str) -> bool:
    """Darf dieses Konto Inhalte nach AUSSEN geben (Freigabelink, Mail)?

    Eine eigene Achse, keine der beiden Stufen: Im Familienbestand duerfen
    admin und editor teilen — ein Mitwirkender also, der dort nicht
    kuratieren darf. Im eigenen Bereich darf es jeder, dem der Bereich
    gehoert.

    Darueber liegt seit 06.09.2026 ein Schalter je Konto (`may_share` in
    users.json, Vorgabe erlaubt). Er ist die Ausnahme fuer einzelne Konten
    — Kinderkonto: eigenes Tagebuch ja, veroeffentlichen nein. Ehrlich
    dazu: Er verhindert MPD-Links, nicht das Weitergeben von Bildern.
    """
    if session.is_demo:
        return False
    from core.userdb import lookup  # lazy: Import-Zyklus
    rec = lookup(session.user)
    if rec is not None and not rec.may_share:
        return False
    if space == "shared":
        return session.role in ("admin", "editor")
    if space == "personal":
        return True
    return False


def may_edit_photo(session: Session, space: str, album_dir, filename: str) -> bool:
    """Darf dieses Konto DIESES Foto bearbeiten (drehen, zuschneiden, Tonwerte)?

    Bis 06.09.2026 hing das am Kurator-Recht — mit einer Umkehrung, die
    niemand entschieden hatte: Ein Mitwirkender durfte im Familienbestand
    eine unreferenzierte Datei LOESCHEN, aber kein Foto GERADERUECKEN,
    obwohl das Bearbeiten eine fuenffache Sicherungskette hat und das
    Loeschen keine.

    Jetzt spiegelt es die Loeschregel: Der Kurator darf alles; ein
    Beitragender darf das bearbeiten, was er auch loeschen duerfte —
    Unreferenziertes, also seinen eigenen Beitrag im Durchlauf. Sobald ein
    Foto in einem Album steht, ist es Kurator-Sache. In einem Satz: Was du
    beisteuerst, gehoert dir, bis es einsortiert ist.
    """
    if not is_readonly(session, space):
        return True                      # Kurator (und der eigene Bereich)
    if not can_contribute(session, space):
        return False
    import json
    from pathlib import Path
    aj = Path(album_dir) / "album.json"
    if not aj.is_file():
        return True                      # Durchlauf-Ordner: nichts referenziert
    try:
        data = json.loads(aj.read_text(encoding="utf-8"))
    except Exception:
        return False                     # unlesbar → im Zweifel nein
    if (data.get("meta") or {}).get("thumbnail") == filename:
        return False                     # Cover bleibt Kurator-Sache
    return not any(e.get("file") == filename for e in data.get("elements", []))


# ---------------------------------------------------------------------------
# Rechte fuer die Oberflaeche (06.09.2026)
# ---------------------------------------------------------------------------

def user_rights(session: Session) -> dict:
    """Was dieses Konto darf — als Namen, je Bestand plus Installation.

    WOZU: Bis heute mussten Web und App die Regeln aus is_readonly() und
    can_contribute() NACHBAUEN, um zu entscheiden, welche Knoepfe sie
    ueberhaupt anbieten. Dieselbe Logik zweimal im Haus laeuft
    auseinander, sobald sich die Rollen bewegen — und „faelschlich
    erlaubt" faellt niemandem auf: Es endet als Knopf, der in ein 403
    fuehrt.

    NUR FUER DIE OBERFLAECHE. Durchgesetzt wird weiterhin an jedem
    Endpunkt einzeln; ein hier fehlendes Recht schuetzt nichts. Wer diese
    Funktion aendert, aendert die Anzeige, nicht die Sicherheit.

    NAMEN STATT FESTER SCHLUESSEL, damit der Vertrag Rollenaenderungen
    ueberlebt: Ein Recht, das nicht in der Liste steht, ist nicht erteilt.
    Neue Rechte tauchen einfach auf, weggefallene verschwinden — kein
    Client bricht daran.
    """
    from core import settings as _settings

    share_on = bool(_settings.get("share_enabled"))
    spaces = available_spaces(session)
    out: dict = {}

    for space in ("shared", "personal"):
        rights = []
        if space in spaces:
            rights.append("see")
        else:
            out[space] = rights          # nichts sehen heisst nichts duerfen
            continue
        if can_contribute(session, space):
            rights.append("contribute")
        if not is_readonly(session, space):
            rights.append("curate")
            rights.append("edit")
            # Beisteuern-Links haengen am Kurator-Recht — anders als das
            # Teilen unten. Absicht: ein Freigabelink laesst hineinsehen,
            # ein Beisteuern-Link laesst herein.
            if share_on:
                rights.append("contrib_link")
        elif can_contribute(session, space):
            # Seit 06.09.2026 getrennt: Beitragende duerfen bearbeiten, was
            # sie auch loeschen duerften — Unreferenziertes. Ob das fuer
            # eine BESTIMMTE Datei gilt, weiss nur may_edit_photo(); hier
            # steht nur, dass es ueberhaupt vorkommt.
            rights.append("edit_unreferenced")
        # Teilen ist eine EIGENE Regel, keine der beiden Stufen — samt
        # Schalter je Konto (may_share). Der globale Kill-Switch zaehlt
        # mit, weil die Oberflaeche sonst einen Knopf zeigt, der 503
        # liefert.
        if share_on and may_share(session, space):
            rights.append("share")
        out[space] = rights

    instance = []
    if session.role == "admin" and not session.is_demo:
        instance += ["manage_users", "settings"]
        # Nur, wenn es Trails in dieser Installation gibt (19.09.2026)
        if "trails" in installed_modules():
            instance.append("foreign_trails")
    out["instance"] = instance
    return out


# ---------------------------------------------------------------------------
# Module entitlements (paid add-on modules)
# ---------------------------------------------------------------------------

# timeline gehört seit 03.09.2026 zum GRUNDPAKET (die
# Grund-Blätteransicht trägt kein Schloss) — bewusst NICHT in den Keys.
def public_base_url(request) -> str:
    """Basisadresse fuer Links, die das Haus verlassen — ohne Schrägstrich am Ende.

    Reihenfolge: der konfigurierte Wert (MPD_BASE_URL) gewinnt. Ist keiner
    gesetzt, wird die Adresse aus dem Request abgeleitet — hinter dem
    DSM-Reverse-Proxy stehen Schema und Host in X-Forwarded-*, sonst im
    Host-Header.

    Warum ueberhaupt abgeleitet: Ein fester Vorgabewert im Code hiesse, dass
    eine nicht konfigurierte Installation Share-Links auf einen FREMDEN
    Server verschickt. Lieber die tatsaechlich benutzte Adresse als eine
    geratene.
    """
    if config.MPD_BASE_URL:
        return config.MPD_BASE_URL
    h = request.headers
    proto = (h.get("x-forwarded-proto", "").split(",")[0].strip()
             or request.url.scheme or "https")
    host = (h.get("x-forwarded-host", "").split(",")[0].strip()
            or h.get("host", "").strip())
    return f"{proto}://{host}".rstrip("/") if host else ""


# Die Menge selbst steht in core/module_keys.py — ohne Importe, damit
# Werkzeuge ohne FastAPI (make_license.py, manage_users.py) dieselbe
# Wahrheit lesen koennen. Hier nur weitergereicht; `deps.MODULE_KEYS`
# bleibt der gewohnte Name.
from core.module_keys import MODULE_KEYS  # noqa: E402


def installed_modules() -> frozenset:
    """Module, deren Router-Datei in dieser Installation liegt.

    Der Kern laeuft seit 09.09.2026 ohne die Plus-Dateien (main.py laedt
    die Router per ImportError optional). Bis zum 19.09. rechnete
    user_modules() aber nur mit Rolle und Lizenz — ein Admin in einem
    reinen Kern bekam alle fuenf Module „frei" und sah Menuepunkte, deren
    Seiten fehlen. Jetzt zaehlt nur, was auch da ist; der Rest erscheint
    im Web als Schaufenster mit Schloss."""
    global _INSTALLED
    if _INSTALLED is None:
        import importlib.util
        _INSTALLED = frozenset(
            m for m in MODULE_KEYS
            if importlib.util.find_spec(f"routers.{m}") is not None)
    return _INSTALLED


_INSTALLED = None


def account_modules(session: Session) -> set:
    """Was dieses KONTO darf — Rolle und users.json, ohne die Lizenz.

    Getrennt von user_modules() seit dem 24.09.2026, und zwar fuer genau
    einen Fall: die Einlieferung von Standortpunkten
    (POST /api/trails/ingest). Ob jemand seine Daten ansehen darf, ist
    eine Kaufsache; ob das Handy sie abliefern darf, nicht — eine
    abgelaufene Lizenz wuerde sonst Punkte verwerfen, die niemand
    zurueckholen kann. Fuer alles andere bleibt user_modules() die
    richtige Frage.
    """
    if session.role == "admin":
        base = set(MODULE_KEYS)
    else:
        from core.userdb import lookup  # lazy: avoids import cycle
        rec = lookup(session.user)
        if rec is None or rec.modules is None:
            base = set(MODULE_KEYS)
        else:
            base = set(rec.modules) & MODULE_KEYS
    return base & installed_modules()


def user_modules(session: Session) -> set:
    """Set of add-on modules this user may use.

    - admin: always everything
    - `modules` field missing in users.json (None): Grandfather → everything
    - `modules` present: exactly that set (intersected with known keys)
    - immer geschnitten mit `installed_modules()` — was nicht
      mitgeliefert ist, ist fuer niemanden frei

    Mit MPD_LICENSE_ENFORCE=1 (core/license.py) wird das Ergebnis
    zusaetzlich mit den lizenzierten Modulen geschnitten — dann sieht
    auch der Admin nur Gekauftes (sonst waere nichts verkaufbar).
    """
    base = account_modules(session)

    from core.license import licensed_modules  # lazy: config-Reihenfolge
    licensed = licensed_modules()
    if licensed is None:  # Lizenzpruefung nicht scharf → wie bisher
        return base
    return base & licensed


def require_module(session: Session, key: str, request: Optional[Request] = None) -> None:
    """403 when the module `key` is not unlocked for this user."""
    if key not in user_modules(session):
        raise HTTPException(403, _t("modules.locked", request, {"module": key}))


def resolve_media_path(session: Session, space: str, album_name: str,
                       filename: str = None, request: Optional[Request] = None) -> Path:
    """Pfad-Härtung für alle Datei-Endpunkte (Sicherheits-Audit M3,
    03.09.2026): resolve() + is_relative_to(base) — fängt Rest-'..'
    (ein einzelnes Segment passt durchs Routing) UND Symlinks, die aus
    dem Space hinauszeigen. Wirft 400 bei Ausbruch."""
    base = get_path(session, space, request).resolve()
    p = base / album_name if filename is None else base / album_name / filename
    rp = p.resolve()
    if not rp.is_relative_to(base):
        raise HTTPException(400, _t("common.invalid_path", request))
    return rp


# ---------------------------------------------------------------------------
# Media URLs
# ---------------------------------------------------------------------------

def media_urls(space: str, album_name: str, filename: str,
               version: str = None) -> tuple:
    """(thumbnails-Dict, original-URL) im einheitlichen Schema — genutzt
    von der Album-Antwort und /api/timeline, damit die Varianten nie
    auseinanderlaufen (die full-Lücke bis 09/2026 entstand genau so).
    share.py bleibt bewusst eigenständig (kein full/original im Share).

    version (Performance-Paket 03.09.2026): Kurzform des Thumb-Cache-
    Schlüssels als ?v= — /api/thumbnail antwortet dann mit
    immutable/1 Jahr (Google-Muster: inhaltsadressierte URLs, null
    Revalidierungs-Roundtrips). Ohne version bleibt alles wie bisher."""
    a, f = quote(album_name), quote(filename)
    vq = f"&v={version}" if version else ""
    thumb = f"/api/thumbnail/{space}/{a}/{f}?size="
    return (
        {"sm": thumb + "sm" + vq, "m": thumb + "m" + vq,
         "xl": thumb + "xl" + vq, "full": thumb + "full" + vq},
        f"/api/photo/{space}/{a}/{f}/download",
    )


# ---------------------------------------------------------------------------
# Media constants
# ---------------------------------------------------------------------------

# Die Endungsmengen und die beiden Bild-/Video-MIME-Karten stehen seit
# 07.09.2026 in core/media_types.py — hier nur noch durchgereicht, weil
# ein Dutzend Router sie von `deps` importiert. Nicht neu definieren.
_PHOTO_EXTS = media_types.PHOTO_EXTS
_VIDEO_EXTS = media_types.VIDEO_EXTS
_MEDIA_EXTS = media_types.MEDIA_EXTS

_AUDIO_EXTS = media_types.AUDIO_EXTS
_DOC_EXTS   = media_types.DOC_EXTS

# Kanonische MIME-Karten + Größen-Aliasse (Aufräumen 03.09.2026): vorher
# je eine identische Kopie in routers/media.py und routers/share.py.
_SIZE_ALIAS = {"sm": "thumb", "m": "cover", "xl": "preview"}
# .txt und .md seit 15.09.2026: In Albumordnern liegen Textdateien, die
# MPD bis dahin nicht anzeigen konnte — das naechtliche Aufraeumen meldete
# sie als "kann MPD nicht anzeigen". Bewusst als text/plain, NICHT als
# text/html oder text/markdown-mit-Darstellung: Der Inhalt kommt aus
# fremden Dateien, und was der Browser nicht auswertet, kann auch nichts
# ausfuehren. Zusammen mit dem globalen X-Content-Type-Options: nosniff
# (main.py) ist das dicht.
_DOC_MIME   = {".pdf": "application/pdf", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".png": "image/png",
               ".txt": "text/plain; charset=utf-8",
               ".md":  "text/plain; charset=utf-8"}
# `_DOC_MIME` oben ist eine ERLAUBNISLISTE (was als Dokument durchgeht),
# keine MIME-Auskunft — wer damit ein Foto beschriftet, macht aus .heic
# stillschweigend image/jpeg. Fuer Fotos deshalb `_PHOTO_MIME`.
_PHOTO_MIME = media_types.PHOTO_MIME
_AUDIO_MIME = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
               ".wav": "audio/wav", ".aac": "audio/aac"}
_VIDEO_MIME = media_types.VIDEO_MIME
