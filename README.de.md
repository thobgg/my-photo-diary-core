# My Photo Diary — Core

**Ein selbstgehostetes Foto-Tagebuch für deine NAS — wo deine Erinnerungen dir gehören.**

> 🇬🇧 [English version: README.md](README.md)

My Photo Diary (MPD) macht aus Foto-Ordnern Tagebücher: Fotos, Videos, Text,
Karten, GPS-Touren, Ton und Dokumente, in der Reihenfolge, die *du* bestimmst.
MPD läuft in einem einzigen Docker-Container auf deiner eigenen Hardware — keine
Cloud, kein Konto bei irgendwem, kein Algorithmus, der entscheidet, was du siehst.

Dieses Repository ist **MPD Core**, der quelloffene Kern von My Photo Diary. Er
ist für sich vollständig: Alles, was man braucht, um ein Familientagebuch zu
führen, zu schreiben und zu teilen, steckt hier drin. Die optionalen Module
**MPD Plus** werten es zusätzlich aus — siehe [Core und Plus](#core-und-plus).

![Startseite mit zwei Alben](screenshots/startseite.jpg)

---

## Installation auf der Synology

Ein Synology-Paket (`.spk`) für DSM 7 liegt bei den
[Releases](https://github.com/thobgg/my-photo-diary-core/releases). Es wird über
Paket-Zentrum → Manuelle Installation eingespielt, fragt in einem Assistenten
nach Foto-Freigabe und Konten und braucht den Container Manager. Einzelheiten in
der [Installationsanleitung](spk/docs/MPD_Installationsanleitung_v0_2.pdf) (PDF).

| | |
|---|---|
| ![Assistent: Foto-Freigabe](spk/docs/screenshots/02-wizard-fotos.png) | ![Assistent: Konten und Rollen](spk/docs/screenshots/03-wizard-benutzer.png) |
| Der Assistent fragt nach dem Namen der Foto-Freigabe, nicht nach einem Pfad | Eine Rolle je DSM-Konto; MPD hat eine eigene Anmeldung |

![Paket-Zentrum: My Photo Diary läuft](spk/docs/screenshots/05-paketzentrum-laeuft.png)

---

## Warum MPD

- **Deine Dateien bleiben Dateien.** Ein Album *ist* ein Ordner mit deinen
  Fotos und einer `album.json` daneben. Kein Import, keine Datenbank für deine
  Inhalte, nichts, das auseinanderlaufen kann. Wer den Ordner kopiert, hat das
  Album kopiert.
- **Ein offenes, dokumentiertes Format.** Die `album.json` folgt der
  [PDX-Spezifikation](docs/specs/PDX_SPEC_v1_5.pdf) (Photo Diary Exchange,
  v1.5) — reines JSON, menschenlesbar, versioniert, vorwärtskompatibel. Gäbe es
  MPD morgen nicht mehr, wären deine Alben trotzdem lesbar.
- **Ein Tagebuch, kein Feed.** Texte zwischen den Fotos, Kapitelüberschriften,
  Zitate, Karten — in einer Reihenfolge, die du von Hand setzt, nie ein
  Algorithmus.
- **Nichts verlässt das Haus.** Keine Telemetrie, kein Nach-Hause-Telefonieren,
  kein Fremdschlüssel nötig. Karten kommen von OpenStreetMap; die optionale
  Formulierungshilfe ist die einzige Funktion, die mit einem fremden Dienst
  spricht — und nur, wenn du sie einschaltest.

## Was im Kern steckt

**Alben und Schreiben**
- Alben mit 9 Elementtypen: Foto, Video, Text, Trenner, Karte, GPX-Tour, Ton,
  Dokument (PDF, Bild, Text / Markdown) und Weblink
- Textstile (Standard, Info, Zitat mit Quelle, Notiz, Kapitelüberschrift) mit
  leichtem Markdown (`*kursiv*`, `**fett**`)
- Bearbeiten per Ziehen und Ablegen mit Rückgängig/Wiederholen, automatische
  Album-Sicherungen (die letzten 10 bleiben)
- Fotos aus anderen Alben einbinden, ohne sie zu kopieren („Best-of“-Alben)
- Gesperrte Elemente, die nur der Eigentümer sieht — nie geteilt, nie
  durchgereicht

**Fotos**
- Vollbild-Ansicht mit Zoom, Diashow, Tastatur und Wischgesten
- Foto-Editor: drehen, spiegeln, geraderücken, zuschneiden, Helligkeit /
  Kontrast / Sättigung — jede Änderung hält eine Sicherungskette aus fünf Ständen
- EXIF-Editor für falsche Aufnahmedaten und fehlendes GPS, auf Wunsch
  Umbenennen nach `JJJJ-MM-TT_HH-MM-SS`
- WebP-Vorschaubilder in vier Größen, im Hintergrund erzeugt, BlurHash-Platzhalter
  beim Laden
- Bildformate JPEG, PNG, HEIC, WebP, GIF, TIFF, BMP; Video MP4, MOV, MKV, WebM,
  MTS/M2TS und mehr, mit Spulen (Range-Streaming)

**Zeitstrahl und Kuratieren**
- Zeitstrahl (in der Android-App) über alle Fotos, die du sehen darfst — auch
  über Dateien, die noch in keinem Album stehen
- „Durchlauf“: alles Unsortierte auf einen Blick, in Alben einsortieren oder
  verwerfen, „Rest des Jahres ins Jahresalbum“ mit einem Klick
- Hochladen aus der Android-App (Kamera direkt in den Durchlauf); Gäste steuern
  Fotos über Beisteuern-Links im Browser bei, ohne Konto

**Teilen**
- Freigabelinks zum Ansehen für ein Album oder ein einzelnes Foto, mit
  optionalem Ablaufdatum, widerrufbar; Empfänger brauchen weder Konto noch App
- Teilen per Mail über deinen eigenen SMTP-Server, mit Adressbuch
- Gespeichert wird nur der SHA-256-Hash eines Links

**Menschen und Sicherheit**
- Konten mit Rollen: Jedes Konto liest den Familienbestand und führt seinen
  eigenen persönlichen Bereich; im Familienbestand kuratiert `admin`, `editor`
  trägt bei, `viewer` liest
- Eigene Anmeldung (Argon2id), unabhängig von den NAS-Konten;
  Passwortwechsel bei der ersten Anmeldung erzwingbar; Sperre nach Fehlversuchen
- Nächtliche Rettungskopie von Konten und Einstellungen in den eigenen
  Foto-Ordner des Admins — eine Deinstallation nimmt sie nicht mit
- Nächtliches Aufräumen alter Sicherungen; verwaiste Vorschaubilder werden erst
  gelöscht, wenn du es einschaltest (`MPD_THUMB_SWEEP=1`), vorher nur gemeldet

**Erinnerungen**
- „Heute vor X Jahren“: jeden Morgen eine Auswahl aus den Fotos dieses
  Kalendertags aus allen Jahren, mit Ortsnamen aus OpenStreetMap
- Die Auswahl je Jahr folgt Tageszeit und Ort und wechselt von Jahr zu Jahr;
  die Meldung holt sich die App, es gibt keinen Push-Dienst

**Im Alltag**
- Oberfläche vollständig zweisprachig (Deutsch / Englisch), 9 Themes
- Benachrichtigungen in der App (neue Fotos, wöchentliche Kuratier-Erinnerung) —
  kein Push-Dienst, kein Google
- Native Android-App: verbinden per QR-Code auf der Seite „App verbinden“ des
  Servers (APK: [bgg-home.de/mpd.apk](https://bgg-home.de/mpd.apk))
- Optionale Formulierungshilfe („Companion“): schlägt drei Umformulierungen
  *eines* Textes vor, den du geschrieben hast — nie neue Fakten, nie deine
  Fotos. Mit eigenem Schlüssel (Anthropic, Mistral, Groq oder Gemini); aus,
  bis du einen einträgst

## Core und Plus

MPD folgt dem Modell **Open Core**:

| | MPD Core (dieses Repository) | MPD Plus |
|---|---|---|
| Alben, Editor, Vollbild-Ansicht, Foto-Editor | ✓ | |
| Zeitstrahl, Durchlauf, Hochladen, Beisteuern-Links | ✓ | |
| Teilen, Benachrichtigungen, Rollen, Rettungskopie | ✓ | |
| **Memories** — „heute vor X Jahren“, jeden Morgen | ✓ | |
| Formulierungshilfe (eigener Schlüssel) | ✓ | |
| **Suche** — Volltext über Alben und Metadaten | | ✓ |
| **Stats** — Auswertungen und GPS-Heatmap | | ✓ |
| **Tours** — deine GPX-Touren als Sammlung | | ✓ |
| **Trails** — eigene Standorthistorie statt Google Timeline | | ✓ |
| Lizenz | AGPL-3.0 | kommerziell, je Installation |

Die Linie dahinter: Alles, was das Tagebuch **führt und teilt**, ist Core. Plus
**wertet aus und reichert an**. Der Kern wird nie schlechter, wenn Plus fehlt:
Tours, Trails, Suche und Stats stehen dann mit Schloss und kurzer Erklärung im
Menü. Memories bleibt bewusst im Kern — ein Tagebuch lebt von „heute vor X
Jahren“; wer das nie erlebt, hat keinen Grund, etwas dazuzukaufen.

Plus wird über eine signierte Lizenzdatei freigeschaltet, die **offline**
geprüft wird — kein Aktivierungsserver, kein Nach-Hause-Telefonieren. Läuft eine
Lizenz ab, bleiben alle Alben voll benutzbar. Plus ist nicht Teil dieses
Repositorys; Auskunft unter thomas@bgg-mail.de.

## Schnellstart (Docker Compose)

Voraussetzungen: Docker mit Compose, rund 1 GB Arbeitsspeicher und deine Fotos
in Ordnern.

```bash
git clone https://github.com/thobgg/my-photo-diary-core.git
cd my-photo-diary-core

mkdir -p photos homes data
printf 'MPD_PHOTO_SHARED=/photos\nMPD_HOMES_ROOT=/homes\n' > mpd.env

docker compose up -d --build
docker compose exec mpd python scripts/manage_users.py add admin
```

Der letzte Befehl fragt nach der Rolle (`admin`), dem persönlichen Ordner (der
Vorschlag `/homes/admin/Photos` passt) und einem Passwort. Danach
`http://<dein-rechner>:8089` öffnen und anmelden.

Deine Fotos kommen nach `photos/`, ein Ordner je Album — zum Beispiel
`photos/2019 - Toskana/`. In MPD macht **Album hinzufügen** aus einem Ordner
ein Album. MPD legt dabei eine `album.json` in den Ordner; deine Fotos werden
nie verändert, außer du benutzt den Foto-Editor.

Für den Zugriff von unterwegs MPD hinter den Reverse Proxy stellen, den du ohnehin
benutzt (zum Beispiel den von DSM), und `MPD_BASE_URL` in `mpd.env` setzen, damit
geteilte Links auf die richtige Adresse zeigen.

### Synology-Paket

Siehe [Installation auf der Synology](#installation-auf-der-synology) oben.

## Wo deine Daten liegen

| Ort | Inhalt | Wenn verloren |
|---|---|---|
| `photos/`, `homes/*/Photos` | deine Fotos und die `album.json`-Dateien | **unersetzlich** — sichern! |
| `data/users.json` | Konten, Rollen | unersetzlich (Rettungskopie im Ordner des Admins) |
| `data/mpd-settings.json`, `mpd.env` | Einstellungen, SMTP-Passwort, Schlüssel | von Hand neu eintragen |
| `data/thumb-cache/` | Vorschaubilder | baut sich neu auf |
| `data/mpd.sqlite` | Sitzungen, Freigabelinks, Adressbuch | neu anmelden, Links sind weg |

> **Löschen ist außerhalb einer Synology-Freigabe endgültig.** MPD verschiebt
> gelöschte Dateien in den `#recycle`-Ordner der Freigabe, wenn es einen gibt
> (DSM). Ein einfaches Docker-Volume hat keinen — dort heißt Löschen Löschen.

## Einstellungen

Alle Schalter mit Vorgabe und Erklärung stehen in
[`mpd.env.example`](mpd.env.example); `mpd.env` wird beim Start einmal gelesen.
Schalter für die Installation (Teilen an/aus, SMTP, Formulierungshilfe, Lizenz)
setzt der Admin im Einstellungsdialog.

## Dokumentation

- [`ARCHITEKTUR.md`](ARCHITEKTUR.md) — wie die Teile zusammenspielen und wo man
  für eine Änderung anfängt
- [`docs/specs/PDX_SPEC_v1_5.pdf`](docs/specs/PDX_SPEC_v1_5.pdf) — das Albumformat
- In der App: **Hilfe** (`/hilfe`) für alle, das Entwickler-Handbuch für Admins

## Entwicklung

Backend: Python 3.11, FastAPI, Pillow, APScheduler. Frontend: reines HTML, CSS und
JavaScript — kein Build-Schritt, kein Framework.

```bash
pip install -r requirements-dev.txt
ruff check --select F,E9 backend/
pytest -q
```

Dasselbe Python wie im Container benutzen (3.11); `httpx` ist absichtlich auf
0.27.2 festgelegt. Die CI prüft Tests und Linter bei jedem Push.

## Mitmachen

Fehlermeldungen und Ideen sind willkommen — siehe
[CONTRIBUTING.md](CONTRIBUTING.md). Fremder Code kann vorerst nicht
übernommen werden: Open Core braucht dafür erst eine Beitragsvereinbarung,
und die gibt es noch nicht. Ein guter Fehlerbericht hilft im Moment mehr.

## Lizenz

MPD Core steht unter der **GNU Affero General Public License v3.0**
([LICENSE](LICENSE)). © 2026 Thomas Bugge.

Die Schriften unter `frontend/fonts/` und `docs/fonts/` stehen unter der SIL Open
Font License 1.1.
