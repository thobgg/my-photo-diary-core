"""
core/license.py — Modul-Lizenz: Basis-MPD ist frei, Module kosten.

Eine Lizenzdatei (Default /data/mpd-license.json, MPD_LICENSE_FILE) ist
ein RSA-signiertes JSON:

    {"payload": {"customer": "…", "modules": ["tours", "trails", …],
                 "issued": "2026-09-03", "expires": null},
     "signature": "<hex>"}

Signiert wird offline mit scripts/make_license.py (privater Schluessel
liegt nur beim Herausgeber der Lizenzen); hier steht ausschliesslich der oeffentliche
Schluessel. Kein Phone-Home, keine Online-Pruefung — das passt zum
Produktversprechen (alles auf der eigenen NAS) und ist ehrlich gegenueber
dem Bedrohungsmodell: Ein Selbsthoster kann jede Pruefung im Code
umgehen; die Signatur verhindert nur das ERZEUGEN gueltiger Lizenzen
durch Dritte.

Scharf geschaltet wird ueber MPD_LICENSE_ENFORCE=1 (mpd.env; Default 0 =
heutiges Verhalten, Bestandsinstallationen aendern sich nicht). Nach dem
Ablaufdatum laufen die Module noch MPD_LICENSE_GRACE_DAYS weiter (Vorgabe
30): Eine Verlaengerung, die einen Tag zu spaet kommt, soll nicht ueber
Nacht alles abschalten. Gewarnt wird vorher ueber core/notify_jobs.py. Die
Kanonisierung des Payloads (sort_keys, kompakte Trenner) muss mit
scripts/make_license.py identisch bleiben.
"""

import json
import logging
from datetime import date

import config
from core import rsa_min

logger = logging.getLogger(__name__)

# Oeffentlicher Pruefschluessel (RSA-2048, e=65537) — Gegenstueck zum
# privaten Signierschluessel aus scripts/make_license.py.
PUBLIC_KEY_N = int(
    "a070e428f1e0f5fa2f03d6d04ef742ae39b6cf23761b785dbe8d45bc47cd9c0c"
    "a74f6dafc79a1039e9bc6de8f657c1273996e69518f826e26f569f84414bd11d"
    "a61b9f3f6eef8f41c196578e751f085d72df100334dc656433293070f7230069"
    "51a4b523b08f7a9b6948e2e27070551f88a46288a9b5729a1d8aaeda14cc2f5f"
    "ed955b6740accef67be3deac7bf77e69c7f18446adf05621111ce9797b87c022"
    "21c28e30b4753d91059355d2a84132df210bbf4dd3b833819d367fc5a3f9307d"
    "567e099aabbbd12587b1d6615c79ebda07ad5ddde165eb54069e7d20489558c7"
    "e384e9e788d8cd7d45e08ac0cef313a8034b673114e1a429d443ad42340135e3",
    16,
)
PUBLIC_KEY_E = rsa_min.PUBLIC_EXPONENT


def canonical_payload_bytes(payload: dict) -> bytes:
    """Eine Wahrheit fuer Signieren UND Pruefen."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


# Cache: (mtime_ns, Ergebnis) — die Datei aendert sich praktisch nie,
# Signaturpruefung kostet trotzdem nur Mikrosekunden.
_cache: tuple | None = None


def _load() -> dict:
    """Lizenzzustand: {"valid", "customer", "modules", "issued",
    "expires", "error"} — nie eine Exception nach aussen."""
    global _cache
    path = config.LICENSE_FILE_PATH
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {"valid": False, "error": "keine Lizenzdatei", "modules": []}

    if _cache and _cache[0] == mtime:
        return _cache[1]

    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        result = verify_doc(doc)
    except Exception as e:  # kaputte/gefaelschte Datei = keine Lizenz
        logger.warning("Lizenzdatei %s unbrauchbar: %s", path, e)
        result = {"valid": False, "error": str(e), "modules": []}

    _cache = (mtime, result)
    return result


def verify_doc(doc: dict) -> dict:
    """Signatur pruefen und den Anzeigeblock bauen — wirft bei allem, was
    keine gueltige Lizenz ist. Gemeinsam fuer Datei und Einspielen."""
    payload = doc["payload"]
    signature = int(doc["signature"], 16)
    if not rsa_min.verify(
        canonical_payload_bytes(payload), signature, PUBLIC_KEY_N, PUBLIC_KEY_E
    ):
        raise ValueError("Signatur ungueltig")
    return {
        "valid": True,
        "customer": str(payload.get("customer", "")),
        "modules": sorted(set(map(str, payload.get("modules", [])))),
        "issued": payload.get("issued"),
        "expires": payload.get("expires"),
        "error": None,
    }


def install(text: str) -> dict:
    """Lizenz einspielen (09.09.2026): Text pruefen, erst dann atomar nach
    LICENSE_FILE_PATH schreiben (0600), Cache leeren. Wirft ValueError mit
    lesbarem Grund, wenn der Text keine gueltige Lizenz ist — dann bleibt
    die vorhandene Datei unangetastet. Der Weg fuer Kaeufer ohne Shell:
    Text in den Einstellungen einfuegen."""
    global _cache
    import os, tempfile
    try:
        doc = json.loads(text)
    except Exception as e:
        raise ValueError(f"kein gueltiges JSON: {e}")
    if not isinstance(doc, dict) or "payload" not in doc or "signature" not in doc:
        raise ValueError("Lizenzdatei braucht 'payload' und 'signature'")
    try:
        verify_doc(doc)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Lizenz unbrauchbar: {e}")
    path = config.LICENSE_FILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mpd-license.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _cache = None
    logger.info("Lizenz eingespielt: %s", license_info().get("customer") or "?")
    return license_info()


def remove() -> dict:
    """Lizenzdatei entfernen — Plus faellt weg, Basis bleibt."""
    global _cache
    try:
        config.LICENSE_FILE_PATH.unlink()
        logger.info("Lizenz entfernt")
    except FileNotFoundError:
        pass
    _cache = None
    return license_info()


def _ablauf(info: dict):
    """Ablaufdatum als date — None bei unbefristet, False bei unlesbar."""
    expires = info.get("expires")
    if not expires:
        return None
    try:
        return date.fromisoformat(str(expires))
    except ValueError:
        return False


def tage_bis_ablauf(info: dict | None = None):
    """Tage bis zum Ablaufdatum: positiv davor, 0 am Tag selbst, negativ
    danach. None bei unbefristet oder ohne gueltige Lizenz."""
    info = info if info is not None else _load()
    if not info.get("valid"):
        return None
    tag = _ablauf(info)
    if tag is None or tag is False:
        return None
    return (tag - date.today()).days


def _in_karenz(info: dict) -> bool:
    """Abgelaufen, aber noch innerhalb der Karenz — Module laufen weiter."""
    tage = tage_bis_ablauf(info)
    return tage is not None and -config.LICENSE_GRACE_DAYS <= tage < 0


def _expired(info: dict) -> bool:
    """Endgueltig abgelaufen: nach Ablaufdatum PLUS Karenz."""
    tag = _ablauf(info)
    if tag is None:
        return False
    if tag is False:
        return True  # unlesbares Datum zaehlt als abgelaufen
    return (date.today() - tag).days > config.LICENSE_GRACE_DAYS


def licensed_modules() -> set | None:
    """Freigekaufte Module — oder None, wenn nicht scharf geschaltet
    (dann gilt allein users.json wie bisher)."""
    if not config.LICENSE_ENFORCE:
        return None
    info = _load()
    if not info["valid"] or _expired(info):
        return set()
    return set(info["modules"])


def license_info() -> dict:
    """Anzeigeblock fuer /api/whoami und /health."""
    info = _load()
    karenz = info["valid"] and _in_karenz(info)
    return {
        "enforced": config.LICENSE_ENFORCE,
        "valid": info["valid"] and not _expired(info),
        "customer": info.get("customer", ""),
        "modules": info["modules"],
        "issued": info.get("issued"),
        "expires": info.get("expires"),
        # Additiv (24.09.2026) fuer Web und App: Tage bis zum Ablauf
        # (negativ = darueber), ob die Karenz laeuft und wie lang sie ist.
        "days_left": tage_bis_ablauf(info),
        "in_grace": karenz,
        "grace_days": config.LICENSE_GRACE_DAYS,
        "error": ("abgelaufen" if info["valid"] and _expired(info)
                  else ("Karenz" if karenz else info.get("error"))),
    }
