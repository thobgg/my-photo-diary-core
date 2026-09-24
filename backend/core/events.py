"""
core/events.py — Ereignisse vom Kern an die Module, ohne Import in die
Gegenrichtung (Block A der Bestandsaufnahme vom 02.09.2026, gebaut 09.09.).

Bis heute holte sich der Kern die Invalidierung des Suchindex direkt aus
`routers/search.py` und den Geo-Cache aus `stats_geo.py` — beides bezahlte
Module. Fehlte eines, startete der Kern nicht (Importfehler, kein sanftes
Degradieren). Jetzt meldet der Kern nur, DASS sich etwas geaendert hat;
wer es wissen will, haengt sich beim eigenen Import ein.

Zwei Ereignisse, mehr braucht es heute nicht:

  album_changed        album.json geschrieben oder Album angelegt/geloescht/
                       umbenannt, Datei einsortiert, hochgeladen, geloescht.
                       Argumente optional: space, album, base.
  photo_meta_changed   EXIF/GPS eines Fotos geschrieben. Argumente: base.

Regeln: Handler laufen synchron, in Anmeldereihenfolge, und ein Fehler in
einem Handler wird geloggt, nie geworfen — ein kaputtes Modul darf keinen
Album-Save scheitern lassen. Kein Threading, kein Async: die Handler sind
heute reine Cache-Invalidierungen (Datei loeschen, Flag setzen).
"""

import logging
from collections import defaultdict
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)

_handlers: Dict[str, List[Callable]] = defaultdict(list)


def subscribe(event: str, handler: Callable) -> None:
    """Handler anmelden. Mehrfaches Anmelden derselben Funktion ist
    harmlos (z. B. bei Modul-Reload in Tests) — sie steht dann einmal drin."""
    if handler not in _handlers[event]:
        _handlers[event].append(handler)


def emit(event: str, **kwargs) -> int:
    """Ereignis ausloesen. Gibt die Zahl der aufgerufenen Handler zurueck."""
    n = 0
    for h in list(_handlers.get(event, ())):
        try:
            h(**kwargs)
            n += 1
        except Exception as e:  # noqa: BLE001 — bewusst: Module duerfen den Kern nicht stoppen
            logger.warning("Ereignis %s: Handler %s fehlgeschlagen: %s",
                           event, getattr(h, "__name__", h), e)
    return n


def handlers(event: str) -> List[Callable]:
    """Fuer Tests und Diagnose."""
    return list(_handlers.get(event, ()))
