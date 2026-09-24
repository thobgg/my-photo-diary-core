# Contributing to My Photo Diary Core

Thank you for taking the time. MPD is maintained by one person, so a clear
report helps more than a long one.

## Reporting a bug

Open an issue with:

- what you did, what you expected, what happened instead
- the version (bottom of every page, or `GET /api/version`)
- how MPD runs (Docker Compose, Synology package, NAS model)
- the relevant lines from the log (`docker compose logs mpd`), **with names,
  addresses and paths removed**

Please never attach photos, `users.json`, `mpd.env` or `mpd-settings.json` —
they contain private data and credentials.

## Security issues

Do not open a public issue. Write to thomas@bgg-mail.de instead; you will get an
answer.

## Code contributions are paused

MPD is open core: this repository is AGPL-3.0, the Plus modules are not. To
accept outside code, the project first needs a contributor license agreement —
and that text does not exist yet. **Until it does, pull requests cannot be
merged.** Please open an issue instead; a good bug report or a well-argued
idea helps more than a patch that has to wait.

If you want to contribute code, say so in an issue. When enough people ask,
the agreement will be written and this section will change.

## If pull requests open again

1. Open an issue first for anything larger than a small fix, so we can agree on
   the direction before you invest time.
2. Keep one change per pull request, with tests where it makes sense.
3. Run before pushing:

   ```bash
   ruff check --select F,E9 backend/
   pytest -q
   ```

4. Follow the style of the surrounding code: plain HTML/CSS/JS without a build
   step in the frontend; business logic in `backend/core/`, HTTP in
   `backend/routers/`. `ARCHITEKTUR.md` explains where things live.
5. Never use native `alert()`, `confirm()` or `prompt()` in the frontend — use
   `window.mpdConfirm()` from `js/mpd-dialog.js`.
6. When you change a JS or CSS file, raise its `?v=N` number in the HTML that
   loads it.

## Languages

Issues and pull requests in English or German are both fine. User-facing text in
the app must exist in both languages (`frontend/js/i18n.js`,
`backend/core/i18n.py`).
