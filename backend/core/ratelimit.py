"""
core/ratelimit.py
Zwei verschiedene Bremsen — nicht verwechseln:

1. **Fehlversuchs-Sperre** (check/record_failure/clear). Zaehlt nur, was
   SCHIEFGEHT: fuenf Fehlschlaege in fuenf Minuten je IP → 60 s Sperre.
   Gegen Brute-Force und Log-Fluten bei Share- und Publish-Tokens.

2. **Mengenbegrenzung** (allow). Zaehlt jeden erfolgreichen Aufruf in
   einem gleitenden Fenster. Fuer teure Ressourcen, bei denen nicht das
   Raten das Problem ist, sondern die schiere Menge — der Companion ruft
   eine kostenpflichtige API auf. Eine Sperre nach Fehlern hilft dort
   nicht: es geht ja alles gut, nur zu oft.

Beides im Speicher und prozesslokal (fuer den einen Container auf der
Synology richtig; bei mehreren Workern muesste es nach Redis).

Pattern mirrors the login rate limit in main.py: 5 failed attempts in
5 min per IP → 60 s lockout. In-memory, process-local (fine for the
single-container deployment on the Synology; with multiple workers
this would need to move to Redis).

Brute-force is already prevented by token entropy (256 bit). The
purpose here is log-flood protection and discipline — a scanner hits
the 429 lockout after five attempts and the log stays clean.
"""

import threading
import time
from typing import Dict, List

_WINDOW    = 300   # 5-minute observation window
_THRESHOLD = 5     # failed attempts in window until lockout
_LOCKOUT   = 60    # lockout duration in seconds

_fails: Dict[str, List[float]] = {}
_lock = threading.Lock()

# Mengenbegrenzung: Zeitstempel je Schluessel, aeltere fliegen beim Zugriff raus.
_hits: Dict[str, List[float]] = {}


def check(ip: str) -> float:
    """
    Remaining lockout in seconds (0.0 = not locked). If >0, the caller
    should return 429.
    """
    now = time.time()
    with _lock:
        timestamps = [t for t in _fails.get(ip, []) if now - t < _WINDOW]
        _fails[ip] = timestamps
        if len(timestamps) >= _THRESHOLD:
            remaining = _LOCKOUT - (now - max(timestamps))
            return max(0.0, remaining)
    return 0.0


def record_failure(ip: str) -> None:
    """One invalid share-token attempt from this IP."""
    with _lock:
        _fails.setdefault(ip, []).append(time.time())


def clear(key: str) -> None:
    """Fehlversuche eines Schlüssels verwerfen (z. B. nach erfolgreichem
    Login). Schlüssel sind freie Strings — der Login nutzt "login:<ip>",
    damit Share-/Publish-Fehlversuche eine andere Zählung haben."""
    with _lock:
        _fails.pop(key, None)


def allow(key: str, *, limit: int, window: int) -> bool:
    """Darf `key` gerade noch? Gleitendes Fenster, zaehlt ERFOLGE.

    True heisst "erlaubt" und vermerkt den Aufruf gleich mit — der Aufrufer
    muss nichts zurueckmelden. False heisst "Kontingent ausgeschoepft".

    Unterschied zu check()/record_failure(): dort werden Fehlschlaege
    gezaehlt und es gibt eine Sperrzeit. Hier zaehlt jeder Aufruf, und
    sobald der aelteste aus dem Fenster faellt, ist wieder Platz.
    """
    now = time.time()
    cutoff = now - window
    with _lock:
        hits = [t for t in _hits.get(key, ()) if t > cutoff]
        if len(hits) >= limit:
            _hits[key] = hits          # aufgeraeumt zurueckschreiben
            return False
        hits.append(now)
        _hits[key] = hits
        return True


def remaining(key: str, *, limit: int, window: int) -> int:
    """Wie viele Aufrufe sind im Fenster noch frei? Nur zum Anzeigen/Loggen."""
    cutoff = time.time() - window
    with _lock:
        return max(0, limit - len([t for t in _hits.get(key, ()) if t > cutoff]))


def reset(key: str) -> None:
    """Kontingent eines Schluessels leeren (Tests)."""
    with _lock:
        _hits.pop(key, None)
