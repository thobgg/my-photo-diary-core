"""
core/module_keys.py — welche Module Geld kosten. Eine Wahrheit, ohne Ballast.

Bewusst ohne jeden Import: Diese Menge brauchen auch Werkzeuge, die
FastAPI nicht laden koennen oder wollen — `scripts/make_license.py` auf
dem Arbeitsrechner (dort ist kein fastapi installiert; der Aufruf brach
am 24.09.2026 genau daran ab) und `backend/scripts/manage_users.py`, das
sie sich bis dahin mit einem "keep in sync"-Kommentar doppelt hielt.

Memories gehoert seit 20.09.2026 zum Kern: "heute vor X Jahren"
ist das, was MPD von einer Fotogalerie unterscheidet — wer es im freien
Kern nie erlebt, kauft auch nichts dazu. Wie timeline am 03.09. also aus
den kostenpflichtigen Modulen heraus.
"""

MODULE_KEYS = frozenset({"tours", "trails", "search", "stats"})
