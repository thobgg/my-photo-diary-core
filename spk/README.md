# Synology-Paket (SPK) für My Photo Diary

Dünner Wrapper um Docker: Das Paket bringt Quellen und Dockerfile mit, und der
**Docker-Worker des Container Managers** baut beim Installieren das Image auf
der NAS und betreibt den Container (`conf/resource`). Es gibt kein eigenes
Compose im Paket. Aufbau, Befunde und Stand: `docs/specs/SPK.md`.

> Diese Datei wurde am 19.09.2026 auf den Code-Stand gebracht. Vorher
> beschrieb sie an mehreren Stellen den Entwurf vom 02.09.: Skripte als
> root, ein Paket-Compose, `var/data`, `mem_limit`, `PERSIST_*` und eine
> austauschbare `rt_*`-Laufzeitschicht — nichts davon gibt es im Paket.

## Bauen

```bash
spk/build.sh          # Build-Nummer aus spk/BUILD
spk/build.sh 29       # explizite Build-Nummer → MPD-<Produkt>.<Commits>-29.spk
```

Ergebnis: `spk/dist/MPD-<version>.spk`. Die Version rechnet `build.sh` aus
`backend/product_version.txt` und `git rev-list --count` (z. B. `3.0.437-28`);
ohne Git-Historie bricht der Bau ab. Braucht `rsync`, `tar`, `inkscape` (Icons
aus `frontend/img/brand/mpd-logo-plain.svg`). Kein spksrc, kein pkgscripts.

**Vorher `docs/OFFEN.md` lesen und abräumen** (siehe `CLAUDE.md`).

### Zwei Ausgaben, eine Paket-ID

| | gebaut in | Datei | Paket-Zentrum zeigt | Lizenzprüfung |
|---|---|---|---|---|
| **Plus** (Kern + Module) | diesem Repo | `MPD-<version>.spk` | „My Photo Diary“ | `MPD_LICENSE_ENFORCE=1` |
| **Core** | dem Export (`scripts/export-core.sh`) | `MPD-Core-<version>.spk` | „My Photo Diary Core“ | 0 (es gibt nichts zu prüfen) |

Den Schalter setzt `build.sh` beim Bau in `conf/resource` ein
(Platzhalter `{{LICENSE_ENFORCE}}`), er landet über `entry.sh` in der
`mpd.env`. **Notausgang, auch beim Kunden:**
`MPD_LICENSE_ENFORCE=0` in `docker/MPD/data/mpd.env.local`, Paket stoppen
und starten — die spätere Zeile gewinnt.

`build.sh` erkennt die Ausgabe an `.mpd-core-export` im Baum; im Kern kommt
auch die Version aus dieser Datei (dort gibt es keine Historie). Die Paket-ID
ist in beiden `MPD` — mit Absicht: Die Ausgaben ersetzen einander beim
Einspielen und behalten die Daten, statt nebeneinander um Port und
`docker/MPD/data` zu streiten. Welche Ausgabe läuft, zeigt die Fußzeile
(„· Core“) bzw. `edition` in `/api/version`.

Core bauen: `bash scripts/export-core.sh ~/AI-Projects/mpd-core && cd ~/AI-Projects/mpd-core && bash spk/build.sh`

## Aufbau

| Pfad | Landet in | Zweck |
|---|---|---|
| `INFO.in` | `INFO` | Paketmetadaten; `version`, `adminport`, `extractsize` setzt build.sh |
| `conf/privilege` | `conf/` | `run-as: package` — **kein root** (unsignierte Pakete dürfen das nicht, `SPK.md` §6.1) |
| `conf/resource` | `conf/` | Docker-Worker (Image-Bau, Container `syno-mpd`, Umgebung aus dem Assistenten, acht Steckplätze), Datenordner, Firewall-Dienst. `conf/resource.full` hält zusätzlich das DSM-Portal — verworfen, `SPK.md` §1 |
| `scripts/common` | `scripts/` | Pfade, Assistenten-Werte (`save_wizard`), Ports, `write_bootstrap_spec`, Steckplätze (`apply_mounts`), Zeitzone, Statusabfrage |
| `scripts/{pre,post}{inst,uninst,upgrade}`, `start-stop-status` | `scripts/` | DSM-Lebenszyklus; Container starten und stoppen macht der Worker, `prestart` setzt die Steckplatz-Links |
| `wizard/install_uifile.tmpl.sh` | `WIZARD_UIFILES/{install,upgrade}_uifile{,_ger}.sh` | erzeugt das Assistenten-JSON (Benutzerliste aus `/etc/passwd`), für Installation und Update |
| `wizard/uninstall_uifile.{enu,ger}` | `WIZARD_UIFILES/` | **Hinweistext** beim Deinstallieren — keine Abfrage, keine Wahl |
| `package/image/entry.sh` | ins Image | erzeugt `mpd.env`, legt Erstkonten an, sichert vor Versionswechseln, baut die Namens-Links der Steckplätze |
| `package/bootstrap/bootstrap_users.py` | ins Image | legt Konten mit Argon2 an — läuft im Container |
| `package/templates/`, `package/port_conf/` | `target/` | DSM-Desktop-Icon, Firewall-Dienstdefinition |
| `docs/` | `target/docs/` | Installationsanleitung (HTML; TeX/PDF daneben im Repo) |
| `tools/ds-cleanup.sh` | – | räumt eine gescheiterte Installation auf einer DSM-NAS vollständig auf (als root) |

Repo-Quellen (`backend/`, `frontend/`, `Dockerfile`, `entrypoint.sh`,
`requirements.txt`, `mpd.env.example`, `docs/handbook.html`, `LICENSE`) kopiert
build.sh beim Bauen in den Build-Kontext `image/`; das Dockerfile bekommt dort
`entry.sh` als Einstieg angehängt. Im Paket steht nichts doppelt.

## Was das Paket auf der NAS anlegt

```
/var/packages/MPD/
├── target/  → /volume1/@appstore/MPD   Build-Kontext, Steckplätze, conf/ (wegwerfbar)
└── var/     → /volume1/@appdata/MPD    wizard.env (Assistenten-Werte, keine Passwörter),
                                         install.log (was die Skripte getan haben)

/volume1/docker/MPD/data/               ← im Container /data, NICHT wählbar
├── users.json  mpd.sqlite  mpd-notify.sqlite  mpd-notify.sql
├── mpd-settings.json  mpd-license.json  geo_reverse_cache_v4.json
├── mpd.env.local       eigene Ergänzungen (MAPTILER_KEY, TZ …) — beim Start gelesen
├── trails/             Standorthistorie, eine SQLite je Konto — entsteht nie neu
├── import/             Einlieferungsordner (Google-Zeitachse)
├── thumb-cache/        wegwerfbar
├── state/              Log + Suchindex (MPD_STATE_DIR)
└── backup/<alt>-<ts>/  Sicherung vor jedem Versionswechsel (5 werden behalten)
```

`mpd.env` im Container erzeugt `entry.sh` bei **jedem** Start neu aus den
Assistenten-Werten; eigene Zeilen gehören deshalb nach `mpd.env.local`.

**Deinstallieren entfernt `docker/MPD/data` — mitsamt Konten, Einstellungen
und Standortverlauf.** Wer sie behalten will, kopiert den Ordner vorher weg.

*(Hier stand bis zum 15.09.2026 das Gegenteil: Deinstallieren sichere nach
`/volume1/@appdata/MPD.keep/`, die nächste Installation hole es zurück. Das
hat es nie gegeben — `MPD.keep` kommt im ganzen Code kein einziges Mal vor.
Keine Ungenauigkeit, sondern eine Falle: Wer es las, deinstallierte im
Vertrauen darauf. Aufgefallen am 14.09. beim Schreiben von
`docs/specs/DATENMITNAHME.md` §8.)*

Der Standortverlauf ist davon inzwischen ausgenommen: Er liegt seit dem
14.09. zusätzlich in jedem persönlichen Foto-Ordner unter `.mpd-keep`
(`backend/core/keep.py`) und überlebt die Deinstallation.

## Wiederherstellung nach einer NAS-Neuinstallation

Was wirklich gebraucht wird, in dieser Reihenfolge:

1. **Die Fotos.** `/volume1/photo` und die `Photos`-Ordner in den Homes. Das ist das Einzige,
   was unersetzlich ist; die Alben stecken als `album.json` darin.
2. **Die Betriebsdaten**: `docker/MPD/data` (Konten, Einstellungen, Sitzungen,
   Standortverlauf, Vorschaubilder). Ohne sie laufen die Fotos trotzdem — Konten legt der
   Assistent neu an, der Vorschau-Zwischenspeicher baut sich neu auf. **Der Standortverlauf
   nicht** — der kommt aus der Sicherung oder aus `.mpd-keep`.
3. **Die Paketdatei** `MPD-<Version>.spk`. Sie liegt **nicht** in Git (`dist/` ist ignoriert),
   sondern entsteht aus dem Repo mit `spk/build.sh` — dafür braucht es Repo, `rsync`, `tar`
   und `inkscape`.

Ablauf: DSM neu aufsetzen, Container Manager installieren, Fotos zurückspielen, Paket
installieren, Assistenten wie bei einer Erstinstallation ausfüllen. Danach das Paket stoppen,
`docker/MPD/data` zurückspielen, Paket starten — dann sind auch die alten Konten wieder da.

**Empfehlung:** Eine Kopie der aktuellen `.spk` samt `docs/installation.html` ausserhalb der
NAS aufbewahren (PC, USB-Platte). Dann braucht der Ernstfall weder Repo noch Werkzeuge.

**Der wunde Punkt:** Das Container-Image wird bei der Installation gebaut und zieht
`python:3.11-slim` sowie die Python-Pakete aus dem Internet. Ohne Internet gibt es keine
Installation — und hinter einem strikten DNS-Filter scheitert der Bau nach Minuten stumm
(`SPK.md` §6.3). Wer ganz sicher gehen will, hebt zusätzlich ein `docker save mpd:<Version>`
auf; ein fertiges Image aus einer Registry würde das dauerhaft lösen (`SPK.md` §8).

## Persönliche Bereiche: Steckplätze statt Homes-Vollmount

Die Paket-Deklaration kennt keine Kontonamen — DSM ersetzt dort nur `{{wizard_*}}`. Damit
der Container nicht den gesamten Home-Bereich sieht (Audit-Punkt N6), hängen acht feste
Steckplätze `/mpd-slots/u1..u8` im Container. `prestart` legt die Symlinks
`target/mounts/uN` auf `<homes>/<konto>/<unterordner>` der lokalen DSM-Konten, die einen
Foto-Ordner haben, und schreibt die Zuordnung nach `target/conf/homes-map`.

`entry.sh` baut daraus im Container die Sicht, die MPD erwartet, und zwar **unter demselben
Pfad wie auf dem Host**: `<homes>/<konto>` → `/mpd-slots/uN`, dazu `/mpd-homes/<konto>` als
Alias für Installationen aus dem September 2026, die diesen Pfad in `users.json` stehen haben.
`MPD_HOMES_ROOT` und `MPD_PERSONAL_SUBDIR` tragen die Werte aus dem Assistenten.

Der Grund für die Host-Pfade: `users.json` speichert je Konto einen `personal_path`. Zeigt er
auf `/volume1/homes/<konto>/Photos`, muss genau dieser Pfad im Container auflösen — sonst
bleibt der persönliche Bereich nach jedem Umstieg und jeder Wiederherstellung leer, und
jemand müsste `users.json` von Hand umschreiben. Das ist am 13.09.2026 bei einem echten Umstieg
passiert.

Folgen: Der Container sieht nur die Foto-Ordner, nicht die Home-Verzeichnisse. Mehr als acht
Konten mit Foto-Ordner werden nicht bedient (Warnung im Log). Ein DSM-Konto, das nach der
Installation neu entsteht, bekommt seinen Bereich erst nach einem Stopp/Start des Pakets.

## Betriebshinweise

- Beim ersten Start (und bei jedem weiteren) baut `library_warmer` fehlende
  Vorschaubilder für alle Alben. Auf einer NAS mit großem Bestand ist die erste
  Stunde nach der Installation Last zu erwarten; der Fortschritt steht im
  Container-Log (`docker logs syno-mpd`, Zeilen mit `library_warmer`).
- Das Paket setzt `MPD_WARMER_THREADS=2` und `MPD_THUMB_SWEEP=1` (verwaiste
  Vorschaubilder werden nachts wirklich gelöscht). Alle weiteren Schalter stehen in
  `mpd.env.example`; eigene Werte nach `docker/MPD/data/mpd.env.local`, dann Paket
  stoppen und starten.
- Der Container läuft als root (der Worker legt ihn so an). Eingehängt sind nur die
  Foto-Freigabe, die acht Steckplätze, `data` und `target/conf`. Ein Non-root-Container
  scheitert derzeit an den Datei-Eigentümern im Fotobaum.
- **Eine Speichergrenze setzt das Paket nicht.** Die Empfehlung aus `CLAUDE.md`
  (`mem_limit` ~1 GB auf kleineren NAS) ist im Paket nicht umgesetzt. Ob der Worker dafür
  einen Schlüssel kennt, ist ungeprüft — Vorsicht: Undokumentierte Schlüssel haben die
  Installation schon abbrechen lassen (`SPK.md` §6.1).
- Die Sicherung vor einem Versionswechsel macht `entry.sh` beim ersten Start der neuen
  Version: `users.json`, `mpd-settings.json`, `mpd.sqlite`, `mpd-notify.*`,
  `mpd-license.json`, `geo_reverse_cache_v4.json`, `mpd.env.local`, `trails/`, `import/`
  nach `backup/<alte Version>-<Zeit>/` — nie den Thumb-Cache. Wer unter `/data` etwas
  Neues anlegt, das ein Update überleben muss, trägt es dort ein.

## Trockentests ohne NAS

```bash
for f in spk/scripts/* spk/wizard/*.sh; do sh -n "$f"; done
PASSWD_FILE=<fake passwd> SYNOPKG_USERNAME=anna SYNOPKG_TEMP_LOGFILE=/tmp/w.json \
  sh spk/dist/pkg/WIZARD_UIFILES/install_uifile_ger.sh && jq . /tmp/w.json
```

Die CI führt dieselben Prüfungen samt Probebau bei jedem Push aus
(`.github/workflows/tests.yml`).
