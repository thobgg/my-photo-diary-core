# Backend-Tests

```bash
# einmalig: venv mit App-Dependencies + pytest (Pins aus requirements.txt
# sind fürs Container-Python — lokal ungepinnt installieren)
python3 -m venv .venv-test
.venv-test/bin/pip install fastapi uvicorn requests urllib3 Pillow pillow-heif \
    piexif blurhash numpy APScheduler argon2-cffi pytest httpx

# ausführen (vom Repo-Root)
.venv-test/bin/python -m pytest backend/tests/ -q
```

## Im Container gegenprüfen — die eigentliche Wahrheit

Das venv oben ist ungepinnt und läuft mit neuerem Python. Das reicht zum
Entwickeln, aber ein Fehlschlag dort ist noch kein Fehler: **`httpx` ist in
`requirements-dev.txt` bewusst auf 0.27.2 festgenagelt**, weil ab 0.28 der
`app=`-Shortcut des TestClients fehlt. Mit einem neueren `httpx` schlägt
z. B. `test_api_media_range.py::test_download_range_malformed_ignored` fehl,
im Container nicht.

pytest ist **nicht** im Image (`Dockerfile` installiert nur
`requirements.txt`), also einmal pro Containerleben nachinstallieren:

```bash
# Auf der NAS, Verzeichnis egal — docker exec statt docker compose,
# sonst kommt "no configuration file provided: not found".
sudo docker exec my-photo-diary pip install --quiet pytest==8.1.1 httpx==0.27.2
sudo docker exec -w /app/backend my-photo-diary python -m pytest tests -q
```

`mpd` ist der Compose-Dienstname, **`my-photo-diary` der Containername** —
`docker exec` will den zweiten. Die Nachinstallation ist flüchtig und nach
jedem `docker compose restart` weg.

Stand 05.09.2026: 190 Tests grün im Container (Python 3.11, fastapi
0.109.0, httpx 0.27.2). Am 19.09.2026 zählt die Suite 350 Testfunktionen in
45 Dateien; die Tabelle unten nennt nur die ersten. Den maßgeblichen Aufruf
samt Linter steht in `CLAUDE.md` („Tests").

Die API-Tests fahren die echte FastAPI-App hoch: `conftest.py` biegt `config`
**vor** dem App-Import auf ein Temp-Verzeichnis um (users.json mit echten
Argon2-Hashes, SQLite-Session-DB, Foto-Basis mit Mini-JPEGs). Kein Mocking
der Auth — Login, Cookies, Middleware und Share-Tokens laufen wie in
Produktion, nur gegen Wegwerf-Daten.

| Datei | deckt ab |
|---|---|
| `test_api_auth.py` | Login/Logout, Cookie, 401/302-Schutz, Rollen |
| `test_api_albums.py` | Album-Anlage/Lesen, Viewer-Verbote, Duplikat, Traversal |
| `test_api_share.py` | Token erzeugen, anonymer Zugriff, Widerruf, Rollen |
| `test_api_memories.py` | Session-Pflicht (der alte Token-Bypass ist tot), Quelle je Nutzer, `limit`/`total` |
| `test_filesystem_photos.py` | Pfad-Härtung der Datei-API |
| `test_search_normalize.py` | Such-Normalisierung |
