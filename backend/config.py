"""
Configuration for My Photo Diary v2

Roles are maintained per user in users.json (core/userdb.py).
v2: DSM auth fully decoupled — no more SYNO.Foto API access,
everything runs through FilesystemPhotosAPI on the local filesystem.
"""
from pathlib import Path


def _load_env(env_file="mpd.env") -> dict:
    cfg = {}
    env_path = Path(__file__).parent.parent / env_file
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    cfg[key.strip()] = val.strip()
    return cfg


_cfg = _load_env()

# Shared photo path (personal paths come per user from users.json)
PHOTO_PATH_SHARED = Path(_cfg.get("MPD_PHOTO_SHARED", "/volume1/photo"))

# ── SPK-Portabilität (09/2026, additiv; Defaults = heutiges Verhalten) ──
# Wurzel der Home-Verzeichnisse und Unterordner der persönlichen Alben.
# Der Install-Wizard des Synology-Pakets setzt beide; ohne Eintrag in
# mpd.env ändert sich nichts.
HOMES_ROOT      = Path(_cfg.get("MPD_HOMES_ROOT", "/volume1/homes"))
PERSONAL_SUBDIR = _cfg.get("MPD_PERSONAL_SUBDIR", "Photos")


def default_personal_path(username: str) -> str:
    """<HOMES_ROOT>/<user>/<PERSONAL_SUBDIR> — die eine Quelle für den
    Standard-Personal-Pfad (userdb, users_admin, manage_users)."""
    return str(HOMES_ROOT / username / PERSONAL_SUBDIR)


# Parallelität des Hintergrund-Warmers (Kleine-NAS-Tauglichkeit,
# 03.09.2026): 3 passt für 4+ GB RAM; das SPK setzt auf schwacher
# Hardware 1-2. Deckelt zugleich die Pillow-RAM-Spitzen.
WARMER_THREADS = max(1, min(8, int(_cfg.get("MPD_WARMER_THREADS", "3"))))

# Vorwaermer ganz abschalten (13.09.2026). Vorgabe ist an — bei jedem Start
# ueber alle Alben, das ist gewollt und liegt alles im Cache in Sekunden
# hinter sich. Es gibt aber zwei Faelle, in denen er schadet:
#   * eine Entwicklungsinstanz, die den echten Fotobestand nur eingehaengt
#     hat — sie wuerde ihn ueber die Netzfreigabe komplett durchlesen;
#   * eine sehr kleine NAS, auf der der erste Start sonst minutenlang
#     unter Volllast steht.
# Thumbnails entstehen dann beim ersten Ansehen, nur eben nicht im Voraus.
WARMER_ENABLED = _cfg.get("MPD_WARMER", "1") != "0"

# Ablage für Betriebsdateien (Log, Suchindex). Default: neben dem Code —
# wie bisher. Im SPK-Container zeigt MPD_STATE_DIR auf einen persistenten
# Mount, weil ins Image geschriebene Dateien den Neustart nicht überleben.
STATE_DIR = Path(_cfg.get("MPD_STATE_DIR", str(Path(__file__).parent)))
STATE_DIR.mkdir(parents=True, exist_ok=True)

# MPD-owned data (thumb cache etc.), outside the photo trees
THUMB_CACHE_DIR = Path(_cfg.get("MPD_THUMB_CACHE_DIR", "/data/thumb-cache"))

# DiaryMemories – täglicher Scan-Zeitpunkt. Der Push geht seit 09/2026
# über den In-App-Notify-Store (core/notifydb); ntfy und der
# MEMORIES_TOKEN-Bypass der alten Extra-APK sind ausgebaut.
MEMORIES_HOUR   = int(_cfg.get("MEMORIES_HOUR",   "6"))
MEMORIES_MINUTE = int(_cfg.get("MEMORIES_MINUTE", "30"))

# Oeffentliche Basisadresse fuer alles, was das Haus verlaesst: Share-Links,
# Mail-Links, OG-Tags, QR-Codes der App.
#
# **Kein Vorgabewert mehr.** Bis 05.09.2026 stand hier fest
# die Adresse der Entwicklungsinstallation. Fuer die stimmte das
# zufaellig — fuer jede andere war es eine scharfe Kante: eine Installation
# ohne gesetzten Wert verschickte Share-Mails mit Links auf einen FREMDEN
# Server. Der Kunde teilt ein Album, der Empfaenger landet woanders.
#
# Ist der Wert leer, leitet public_base_url() in routers/deps.py die Adresse
# aus dem Request ab (X-Forwarded-Proto/Host beachtet). Das ist fuer den
# laufenden Betrieb richtig; nur fuer Links, die OHNE Request entstehen
# (Hintergrundjobs), braucht es den gesetzten Wert. main.py warnt beim Start.
MPD_BASE_URL            = _cfg.get("MPD_BASE_URL", "").rstrip("/")
MPD_BASE_URL_CONFIGURED = bool(MPD_BASE_URL)

# MPD Stats Geo – Maptiler reverse geocoding
MAPTILER_KEY = _cfg.get("MAPTILER_KEY", "")

# User DB (v2: own auth, DSM-decoupled)
USERS_JSON_PATH = Path(_cfg.get("MPD_USERS_JSON", "/data/users.json"))

# Session DB (M3: restart-robust via SQLite)
SESSION_DB_PATH = Path(_cfg.get("MPD_SESSION_DB", "/data/mpd.sqlite"))

# Modul-Lizenz (Basis frei, Module kosten — additiv 09/2026, core/license.py).
# ENFORCE Default 0: Bestandsinstallationen verhalten sich unveraendert;
# das SPK setzt spaeter 1. Die Lizenzdatei liegt neben users.json.
LICENSE_FILE_PATH = Path(_cfg.get("MPD_LICENSE_FILE", "/data/mpd-license.json"))
LICENSE_ENFORCE   = _cfg.get("MPD_LICENSE_ENFORCE", "0") == "1"
# Karenz nach dem Ablaufdatum (24.09.2026). Eine Verlaengerung, die einen
# Tag zu spaet kommt, darf nicht ueber Nacht Module abschalten — und bei
# Trails haengt daran der Standort-Ingest des Handys. 0 schaltet die
# Karenz ab.
LICENSE_GRACE_DAYS = max(0, int(_cfg.get("MPD_LICENSE_GRACE_DAYS", "30")))

# Fremd-Einlieferung in die Notify-Queue (NAS-Waechter statt ntfy, 09/2026):
# statischer Token fuer POST /api/notify/publish. Leer = Endpunkt aus.
NOTIFY_PUBLISH_TOKEN = _cfg.get("MPD_NOTIFY_PUBLISH_TOKEN", "")

# Benachrichtigungs-Store (native App, additiv 09/2026). Eigene Datei neben
# der Session-DB, dazu ein menschenlesbarer SQL-Dump daneben.
NOTIFY_DB_PATH = Path(_cfg.get("MPD_NOTIFY_DB", "/data/mpd-notify.sqlite"))

# MPD Trails — Standorthistorie (Phase 1, 09/2026, core/trails_store.py).
# Eine SQLite-Datei je Nutzer unter TRAILS_DIR, Monatsexporte (Zeilen-JSON)
# unter TRAILS_DIR/export/<user>/. Liegt neben den anderen Betriebsdateien
# unter /data (= /volume1/mpd-data). Export-Uhrzeit: nächtlich.
TRAILS_DIR         = Path(_cfg.get("MPD_TRAILS_DIR", "/data/trails"))
# Einlieferung: hier legt der Nutzer Dateien ab, die importiert werden
# sollen (Google-Zeitachse, spaeter GPX u. a.). Bewusst unter /data und
# nicht im Fotobestand — MPD-eigene Daten gehoeren an eine Stelle.
IMPORT_DIR         = Path(_cfg.get("MPD_IMPORT_DIR", "/data/import"))

# Ortsnamen aus Koordinaten (Rueckwaertssuche). Vorgabe ist der oeffentliche
# Nominatim-Dienst. Dessen Nutzungsregeln erlauben fuer Stapellaeufe, die
# ueber einen Tag laufen, 4 Anfragen je Minute — daher 15 s Abstand. Wer
# eine eigene Instanz (Nominatim oder Photon) betreibt, traegt sie hier ein
# und ist die Begrenzung los; wer MPD_GEOCODE_URL leert, schaltet das
# Benennen ganz ab und behaelt Besuche ohne Namen.
GEOCODE_URL        = _cfg.get("MPD_GEOCODE_URL",
                              "https://nominatim.openstreetmap.org/reverse")
GEOCODE_INTERVAL_S = float(_cfg.get("MPD_GEOCODE_INTERVAL_S", "15"))
GEOCODE_NIGHTLY    = int(_cfg.get("MPD_GEOCODE_NIGHTLY", "360"))
GEOCODE_LANG       = _cfg.get("MPD_GEOCODE_LANG", "de")
TRAILS_EXPORT_HOUR = int(_cfg.get("MPD_TRAILS_EXPORT_HOUR", "4"))

# Verfallsregeln (E1 aus docs/specs/DATEIABLAGE.md, core/retention.py).
# Ein Muster fuer alle Ablagen, die von selbst wachsen: behalten wird, was zu
# den N neuesten gehoert UND juenger als D Tage ist; 0 schaltet die jeweilige
# Grenze ab, der juengste Stand bleibt immer. Aufgeraeumt wird nachts.
RETENTION_HOUR       = int(_cfg.get("MPD_RETENTION_HOUR", "3"))
RETENTION_MINUTE     = int(_cfg.get("MPD_RETENTION_MINUTE", "30"))
ALBUM_BACKUP_KEEP    = int(_cfg.get("MPD_ALBUM_BACKUP_KEEP", "10"))
# Ein Trails-Sicherungspunkt ist eine vollstaendige Kopie des Stores
# (Groessenordnung mehrere hundert MB) — deshalb wenige und nicht lange.
TRAILS_SNAPSHOT_KEEP = int(_cfg.get("MPD_TRAILS_SNAPSHOT_KEEP", "3"))
TRAILS_SNAPSHOT_DAYS = int(_cfg.get("MPD_TRAILS_SNAPSHOT_DAYS", "90"))
# Verwaiste Vorschaubilder kehren. Vorgabe AUS: der Lauf meldet erst nur,
# was er entfernen wuerde. Scharf mit MPD_THUMB_SWEEP=1 — dann loescht er.
THUMB_SWEEP_ENABLED    = _cfg.get("MPD_THUMB_SWEEP", "0") == "1"
THUMB_SWEEP_GRACE_DAYS = int(_cfg.get("MPD_THUMB_SWEEP_GRACE_DAYS", "7"))

# Rettungskopie (core/keep.py, docs/specs/DATENMITNAHME.md).
# Beim Deinstallieren entfernt der Docker-Worker den ganzen Datenordner. Was
# unersetzlich ist, liegt deshalb zusaetzlich dort, wo DSM nicht hinlangt:
# im Fotobestand bzw. im persoenlichen Foto-Ordner, in einem Unterordner mit
# fuehrendem Punkt (den ueberspringt MPD ueberall). Vorgabe AN — eine
# Sicherung, die man erst einschalten muss, fehlt genau dann, wenn sie zaehlt.
KEEP_ENABLED    = _cfg.get("MPD_KEEP", "1") == "1"
KEEP_DIR_NAME   = _cfg.get("MPD_KEEP_DIR_NAME", ".mpd-keep")
KEEP_HOUR       = int(_cfg.get("MPD_KEEP_HOUR", "2"))
KEEP_MINUTE     = int(_cfg.get("MPD_KEEP_MINUTE", "30"))
# Wie viele Staende je Nutzer. Einer genuegt fuer den Zweck; jeder weitere
# kostet die volle Groesse des Stores.
KEEP_SNAPSHOTS  = int(_cfg.get("MPD_KEEP_SNAPSHOTS", "1"))
# Wie oft ein frischer Sicherungspunkt gezogen wird. Die Monatsdateien
# wandern jede Nacht mit — sie sind klein und aendern sich staendig. Die
# ganze Datenbank kostet dagegen rund 400 MB schreiben, nochmal so viel
# kopieren, und das Aufraeumen wirft danach den aeltesten weg: 800 MB
# Schreiblast je Nacht fuer einen Stand, der sich in einer Woche kaum
# unterscheidet (15.09.2026). 0 = jede Nacht.
KEEP_SNAPSHOT_DAYS = int(_cfg.get("MPD_KEEP_SNAPSHOT_DAYS", "7"))

# Demo-Zugang. Das ist eine Eigenschaft DIESER Installation (die
# Landing-Page verlinkt darauf), kein Produktmerkmal — deshalb hier und
# nicht in den Einstellungen: Eine Einstellung, die man umlegen kann, darf
# keine Sicherheitszusage sein. Vorgabe AUS; ohne den Schalter gibt es die
# Route /demo-login nicht, und der Kill-Switch demo_enabled wirkt nur,
# wenn sie ueberhaupt existiert. Die Zugangsdaten standen bis 06.09.2026
# fest im Code (mpd-demo/mpd-demo) — ohne gesetztes Passwort bleibt der
# Zugang aus, auch wenn der Schalter an ist.
DEMO_ENABLED  = _cfg.get("MPD_DEMO", "0") == "1"
DEMO_USER     = _cfg.get("MPD_DEMO_USER", "mpd-demo")
DEMO_PASSWORD = _cfg.get("MPD_DEMO_PASSWORD", "")


# Write Companion (Anthropic Haiku) — optional, opt-in
ANTHROPIC_API_KEY = _cfg.get("ANTHROPIC_API_KEY", "")
COMPANION_MODEL   = _cfg.get("COMPANION_MODEL", "claude-haiku-4-5-20251001")

# SMTP relay (Phase 6 — Share-per-Mail via mailbox.org).
# All values optional; without host the mailer facade stays silent.
SMTP_HOST      = _cfg.get("MPD_SMTP_HOST", "")
SMTP_PORT      = int(_cfg.get("MPD_SMTP_PORT", "587"))
SMTP_USER      = _cfg.get("MPD_SMTP_USER", "")
SMTP_PASS      = _cfg.get("MPD_SMTP_PASS", "")
SMTP_FROM_ADDR = _cfg.get("MPD_FROM_ADDR", "")
SMTP_FROM_NAME = _cfg.get("MPD_FROM_NAME", "My Photo Diary")

