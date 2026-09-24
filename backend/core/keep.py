"""
core/keep.py — Rettungskopie ausserhalb des Datenordners.

Spec: docs/specs/DATENMITNAHME.md

Beim Deinstallieren entfernt der Docker-Worker `docker/MPD/data` restlos —
Konten, Standortverlauf, Lizenz und ausgerechnet auch die nächtlichen
Album-Sicherungen, die darin liegen. Das trifft beide Anlässe gleich hart:
den Abschied ebenso wie das „kurz deinstallieren, gleich wieder drauf",
bei dem ohnehin niemand an Sicherungen denkt.

**Warum das hier laufen muss und nicht in `preuninst`:** Das Paket läuft
ohne root, als Nutzer `MPD`. Jede Datei unter `trails/` ist `0600 root` —
Datenbanken, Monatsdateien und Sicherungspunkte gleichermassen
(`create_snapshot()` setzt das absichtlich). Der Paketnutzer darf sie nicht
einmal lesen, kann also nichts wegkopieren. Der Container läuft als root
und kommt heran — aber beim Deinstallieren ist er längst gestoppt
(stop → preuninst → Worker räumt ab). Er kann nicht reagieren, er muss
**vorsorgen**.

**Wohin.** Nach dem Prinzip „was zu den Fotos gehört, liegt bei den Fotos —
geteilt bei geteilt, persönlich bei persönlich":

    <persönlicher Ordner>/.mpd-keep/    eigener Standortverlauf
    <Ordner des Admins>/.mpd-keep/      users.json, Einstellungen, Schlüssel

Der Standortverlauf gehört der Person, nicht der Familie — der Store führt
aus demselben Grund eine Datei je Nutzer. Die Geheimnisse liegen beim
Admin, weil die ACL eines Home-Ordners `everyone` nur Durchgangsrecht gibt
(Befund aus dem Fremdnutzer-Durchlauf, SPK.md §6.7): im
Familienbestand käme jedes Konto daran.

**Der führende Punkt genügt als Tarnkappe.** `.mpd-keep` wird an allen fünf
Stellen übersprungen, an denen es zählt — Album-Erkennung (`routers/
albums.py`), Zeitstrahl-Index und damit Durchlauf (`core/timeline_index.py`),
Vorwärmer, Aufräumen, Medien-Erkennung. Keine Sonderregel nötig.

**Nie ein Dateikopieren der Datenbank.** Neben jeder `<user>.sqlite` liegt
eine WAL-Datei mit den jüngsten Schreibvorgängen; wer nur die `.sqlite`
nimmt, bekommt eine unvollständige und womöglich widersprüchliche Kopie.
Deshalb ausschliesslich über `trails_store.create_snapshot()`, das
`VACUUM INTO` benutzt.

**Was NICHT gerettet wird:** der Vorschau-Zwischenspeicher (33 GB, neu
berechenbar — und nach einer Wiederherstellung ohnehin entwertet, weil der
Cache-Schlüssel den absoluten Pfad und `mtime_ns` enthält), Sitzungen und
Benachrichtigungen.
"""

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional

import config
from core.retention import prune_dir

# Trails ist ein Plus-Modul und darf fehlen (19.09.2026). Ohne es gibt es
# keinen Standortverlauf zu retten — die Geheimnisse und die
# Album-Sicherungen rettet die Kopie trotzdem.
try:
    from core import trails_store
except ImportError:
    trails_store = None

logger = logging.getLogger(__name__)

# Die Geheimnisse: klein, aber von niemandem wiederherstellbar. Die
# Schlüssel (MapTiler, Anthropic, SMTP) muss man bei Dritten neu besorgen —
# Konten dagegen legt der Assistent neu an. Deshalb gehören sie mit, obwohl
# es nur Kilobyte sind.
_SECRET_FILES = ("users.json", "mpd-settings.json", "mpd.env.local",
                 "mpd-license.json")

_README = """\
Rettungskopie von My Photo Diary
================================

Dieser Ordner entsteht automatisch. Er liegt hier, weil der Datenordner des
Pakets beim Deinstallieren vollstaendig entfernt wird — dieser Ordner nicht.

Was hier liegt:

  *.sqlite          Standortverlauf (Trails), vollstaendige Datenbank.
                    Zurueckspielen: Paket stoppen, Datei nach
                    docker/MPD/data/trails/<konto>.sqlite kopieren, starten.
  export/*.jsonl    dieselben Daten als Textzeilen, je Monat eine Datei.
                    Lesbar ohne MPD, auch in vielen Jahren.
  users.json u. a.  Konten und Zugangsschluessel (nur beim Admin).

Der Ordner darf geloescht werden — er entsteht in der naechsten Nacht neu.
Wer ihn aufhebt, sollte wissen: er enthaelt Standortdaten, und beim Admin
zusaetzlich Passwort-Hashes und Zugangsschluessel.
"""


def _keep_dir(base: Path) -> Optional[Path]:
    """Legt `<base>/.mpd-keep` an und gibt ihn zurueck. None, wenn `base`
    nicht existiert — dann gibt es nichts zu bewachen."""
    if not base.is_dir():
        return None
    d = base / config.KEEP_DIR_NAME
    try:
        d.mkdir(exist_ok=True)
    except OSError as e:
        logger.warning("Rettungskopie: %s nicht anlegbar — %s", d, e)
        return None
    _hand_over(d, base)
    readme = d / "LIESMICH.txt"
    if not readme.exists():
        try:
            readme.write_text(_README, encoding="utf-8")
            _hand_over(readme, d)      # sonst bleibt sie root gehoerend
        except OSError:
            pass
    return d


def _hand_over(target: Path, like: Path) -> None:
    """Frisch angelegtes gehoert root (der Container laeuft so). Dem
    Eigentuemer des umgebenden Ordners uebergeben, damit der Mensch ueber
    Datei-Station oder Netzlaufwerk an seine eigene Kopie kommt — dieselbe
    Mechanik, mit der entry.sh neue Foto-Ordner uebergibt. Nur wenn die
    Datei root gehoert, damit nie an Fremdem gedreht wird."""
    try:
        st_parent = like.stat()
        if st_parent.st_uid == 0:
            return                      # Elternteil gehoert auch root: nichts zu tun
        if target.stat().st_uid != 0:
            return                      # schon uebergeben
        os.chown(target, st_parent.st_uid, st_parent.st_gid)
    except (OSError, AttributeError):
        pass                            # kein POSIX oder keine Rechte — nicht schlimm


def _copy(src: Path, dst_dir: Path) -> int:
    """Kopiert, wenn Ziel fehlt oder aelter ist. Gibt die Bytes zurueck, die
    tatsaechlich geschrieben wurden (0 = war schon aktuell)."""
    dst = dst_dir / src.name
    try:
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            return 0
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)            # erst umbenennen: kein halber Stand
        _hand_over(dst, dst_dir)
        return dst.stat().st_size
    except OSError as e:
        logger.warning("Rettungskopie: %s nicht kopierbar — %s", src, e)
        return 0


def _snapshot_frisch(d: Path) -> bool:
    """Liegt in `d` schon ein Sicherungspunkt, der jung genug ist?
    `MPD_KEEP_SNAPSHOT_DAYS=0` schaltet die Schonung ab — dann wird jede
    Nacht einer gezogen."""
    if config.KEEP_SNAPSHOT_DAYS <= 0:
        return False
    grenze = time.time() - config.KEEP_SNAPSHOT_DAYS * 86400
    return any(f.stat().st_mtime > grenze for f in d.glob("*.sqlite"))


def _keep_trails(user: str, base: Path) -> Dict[str, int]:
    """Standortverlauf einer Person in ihren eigenen Ordner."""
    out = {"snapshot": 0, "monate": 0, "bytes": 0}
    if trails_store is None or not trails_store.has_store(user):
        return out
    d = _keep_dir(base)
    if d is None:
        # Laut werden, nicht schweigen: Diese Person HAT einen
        # Standortverlauf, und er bleibt ungesichert. Ausweichen waere
        # schlimmer — die Daten in einen fremden oder in den geteilten
        # Ordner zu legen, hebt genau die Trennung auf, derentwegen der
        # Store eine Datei je Nutzer fuehrt. Abhilfe ist ein Foto-Ordner
        # im Home (Fremdnutzer-Durchlauf 11.09.2026, Befund 3).
        logger.warning(
            "Rettungskopie: %s hat einen Standortverlauf, aber keinen "
            "persoenlichen Foto-Ordner (%s) — dieser Verlauf bleibt "
            "UNGESICHERT. Ordner anlegen und Paket neu starten.",
            user, base)
        return out

    # Ganze Datenbank — ueber VACUUM INTO, nie als Dateikopie (WAL!).
    # Nicht jede Nacht: ein Stand kostet rund 400 MB schreiben und nochmal
    # so viel kopieren, und der von gestern unterscheidet sich kaum. Die
    # Monatsdateien unten wandern dagegen taeglich mit — sie sind klein und
    # tragen den Zuwachs.
    if _snapshot_frisch(d):
        out["snapshot_uebersprungen"] = 1
    else:
        snap = trails_store.create_snapshot(user)
        if snap:
            src = trails_store.snapshot_dir(user) / snap["name"]
            n = _copy(src, d)
            if n:
                out["snapshot"] = 1
                out["bytes"] += n

    # Monatsdateien: die zweite Ebene. Reiner Text, lesbar ohne MPD — die
    # Versicherung gegen den Fall, dass die Datenbank selbst das Problem ist.
    exp_src = trails_store.export_dir(user)
    if exp_src.is_dir():
        exp_dst = d / "export"
        exp_dst.mkdir(exist_ok=True)
        _hand_over(exp_dst, d)
        for f in sorted(exp_src.glob("*.jsonl*")):   # gepackt und ungepackt
            n = _copy(f, exp_dst)
            if n:
                out["monate"] += 1
                out["bytes"] += n

    # Alte Staende wegraeumen — ein Sicherungspunkt kostet die volle Groesse
    # des Stores, deshalb Vorgabe 1.
    prune_dir(d, keep_newest=config.KEEP_SNAPSHOTS, pattern="*.sqlite")
    return out


def _keep_secrets(base: Path) -> Dict[str, int]:
    """Konten, Einstellungen und Zugangsschluessel in den Ordner des Admins."""
    out = {"dateien": 0, "bytes": 0}
    d = _keep_dir(base)
    if d is None:
        return out
    data_dir = config.USERS_JSON_PATH.parent
    for name in _SECRET_FILES:
        src = data_dir / name
        if not src.is_file():
            continue
        n = _copy(src, d)
        if n:
            out["dateien"] += 1
            out["bytes"] += n
    # Geheimnisse bleiben Geheimnisse, auch hier.
    for f in d.glob("*"):
        if f.name in _SECRET_FILES:
            try:
                f.chmod(0o600)
            except OSError:
                pass
    return out


def _admin_base() -> Optional[Path]:
    """Persoenlicher Ordner des Admins. Bei mehreren gewinnt der
    alphabetisch erste — bewusst stumpf, damit die Kopie nicht von Lauf zu
    Lauf den Ort wechselt. Offener Punkt der Spec (§10.3): Wird dieses Konto
    geloescht, liegt die Rettung im Ordner einer Person ohne Zustaendigkeit."""
    from core.userdb import list_users, lookup
    admins = sorted(u for u, role in list_users().items() if role == "admin")
    for name in admins:
        rec = lookup(name)
        if rec and not rec.is_demo and rec.personal_path.is_dir():
            return rec.personal_path
    return None


def erstlauf_noetig() -> bool:
    """Liegt noch NIRGENDS eine Rettungskopie?

    Der Nachtjob laeuft um 02:30. Nach einer Neuinstallation — oder nach
    einem Umzug auf andere Hardware, und das ist der Fall, fuer den die
    Kopie ueberhaupt gedacht ist — stuende man bis zu einen Tag ohne da.
    Ein Lauf beim Hochfahren schliesst das.

    Nur beim ERSTEN Mal: Steht irgendwo schon ein `.mpd-keep` mit Inhalt,
    passiert nichts. Sonst zoege jeder Neustart mehrere hundert MB, und
    Neustarts gibt es oefter als Neuinstallationen.

    Legt bewusst keinen Ordner an (anders als `_keep_dir`) — eine Pruefung
    darf den Zustand nicht veraendern, den sie prueft.
    """
    if not config.KEEP_ENABLED:
        return False
    from core.userdb import list_users, lookup

    for name in sorted(list_users()):
        rec = lookup(name)
        if rec is None or rec.is_demo or not rec.personal_path:
            continue
        d = Path(rec.personal_path) / config.KEEP_DIR_NAME
        try:
            if d.is_dir() and any(d.iterdir()):
                return False
        except OSError:
            continue        # unlesbar heisst nicht "nicht vorhanden"
    return True


def run_keep() -> dict:
    """Ein vollstaendiger Durchgang. Gibt Zahlen zurueck statt zu schweigen —
    der Nachtjob loggt sie."""
    from core.userdb import list_users, lookup

    ergebnis: Dict[str, object] = {}
    if not config.KEEP_ENABLED:
        logger.info("Rettungskopie: abgeschaltet (MPD_KEEP=0)")
        return {"abgeschaltet": True}

    # 1. Standortverlauf je Person in ihren eigenen Ordner.
    trails: List[str] = []
    bytes_ges = 0
    for name in sorted(list_users()):
        rec = lookup(name)
        if rec is None or rec.is_demo:
            continue
        r = _keep_trails(name, rec.personal_path)
        if r["snapshot"] or r["monate"]:
            trails.append(f"{name} ({r['snapshot']} Datenbank, "
                          f"{r['monate']} Monatsdateien)")
            bytes_ges += r["bytes"]
    ergebnis["trails"] = trails

    # 2. Geheimnisse in den Ordner des Admins.
    admin_base = _admin_base()
    if admin_base is not None:
        r = _keep_secrets(admin_base)
        ergebnis["geheimnisse"] = r["dateien"]
        bytes_ges += r["bytes"]
    else:
        logger.warning("Rettungskopie: kein Admin mit persoenlichem Ordner — "
                       "Konten und Schluessel bleiben ungesichert")
        ergebnis["geheimnisse"] = 0
    # Album-Sicherungen gehoeren NICHT hierher (entschieden 19.09.2026): Jedes
    # Album hat seine letzten Staende in .mpd_backups/ im eigenen Ordner, und
    # die Foto-Ordner sichert Hyper Backup. Das fruehere Skript
    # scripts/backup-albums.sh ist entfernt.

    ergebnis["mb"] = round(bytes_ges / 1e6, 1)
    if bytes_ges:
        logger.info("Rettungskopie: %s; Geheimnisse %s Datei(en); %.1f MB neu",
                    ", ".join(trails) or "nichts", ergebnis["geheimnisse"],
                    bytes_ges / 1e6)
    else:
        logger.info("Rettungskopie: alles schon aktuell")
    return ergebnis
