# My Photo Diary — Architektur

Dieses Dokument erklärt, **wie die Teile zusammenspielen**. Es ist für
Menschen geschrieben und setzt keine KI-Unterstützung voraus.

Abgrenzung zu den anderen Dokumenten:

| Datei | Beantwortet |
|---|---|
| `README.de.md` | *Was* ist das, *welche* Dateien gibt es, *wie* setze ich es auf |
| **`ARCHITEKTUR.md`** (hier) | *Wie* hängt das zusammen, *wo* fasse ich etwas an |

Stand: 19.09.2026. Zeilennummern sind Orientierungswerte, keine Zusagen.

MPD gibt es als **Kern** (quelloffen, AGPL) und mit den Modulen **MPD Plus**
(Suche, Stats, Tours, Trails). Was nur mit Plus existiert, ist hier
mit „(Plus)“ gekennzeichnet; der Kern läuft ohne jede Plus-Datei (Abschnitt 5,
„Kern und Module“).

---

## 1. Das große Bild

MPD ist bewusst **einfach gebaut**: keine Datenbank für die Inhalte, kein
Build-Schritt, kein Framework im Frontend. Die drei tragenden Entscheidungen:

1. **Die Wahrheit liegt im Dateisystem.** Ein Album *ist* ein Ordner mit Fotos
   und einer `album.json`. Es gibt keinen Import, keine Synchronisation, keinen
   Zustand daneben, der auseinanderlaufen könnte. Wer einen Ordner per SMB
   kopiert, hat das Album kopiert.
2. **SQLite nur für Flüchtiges.** Sessions, Shares und das Adressbuch — also
   Dinge, die man verlieren darf. Fotos und Alben nie.
3. **Vanilla-Frontend.** Plain `<script>`-Tags, globale Variablen, kein Bundler.
   Eine Änderung an einer `.js`-Datei ist nach einem Browser-Reload live.

Daraus folgt die wichtigste Eigenschaft des Projekts: **Man kann fast alles
mit `ls`, `cat` und einem Texteditor nachvollziehen.**

---

## 2. Ein Request von vorne bis hinten

Beispiel: Jemand öffnet ein Album im Browser.

```
Browser: GET /album.html?name=2019-Toskana&space=shared
   │
   ├─► AuthMiddleware              main.py, ~Zeile 140
   │     Cookie `mpd_session` da?  → nein: Redirect /login
   │     Demo-User + Schreibzugriff? → 403
   │
   ├─► routers/static.py           liefert die HTML-Datei, Cache-Control: no-store
   │
   └─► Browser lädt /js/*.js  (26 Dateien, feste Reihenfolge, siehe Abschnitt 7)
         │
         └─► album.js: loadAlbum()
               │
               GET /api/album/shared/2019-Toskana
                 │
                 ├─► AuthMiddleware (nochmal, jeder Request einzeln)
                 ├─► require_session()          routers/deps.py
                 ├─► get_path(session, space)   → /volume1/photo/2019-Toskana
                 ├─► album.json lesen + validieren
                 ├─► Lazy-Migration: fehlende BlurHash/Auflösung nachrechnen
                 │     (max. 200 Elemente pro Request, Rest beim nächsten Öffnen)
                 └─► pro Element Thumbnail-URLs anhängen
                       { sm, m, xl, full } → /api/thumbnail/{space}/{album}/{datei}?size=…
                       (gebaut von media_urls() in deps.py, genutzt von
                        albums.py UND timeline.py; share.py hat einen eigenen
                        Satz — bewusst ohne full/original)
               │
               └─► album-render.js zeichnet, Bilder laden lazy per IntersectionObserver
                     │
                     GET /api/thumbnail/…?size=m
                       └─► routers/media.py → core/thumb_pipeline.py
                             Cache-Treffer? ausliefern : erzeugen und ablegen
```

Zwei Dinge, die man daran ablesen kann:

- **Jeder Request authentifiziert sich neu.** Es gibt keinen serverseitigen
  Zustand pro Sitzung außer der Session-Zeile in SQLite.
- **Die `/api/album/…`-Antwort ist der Vertrag zwischen Backend und Frontend.**
  Wer wissen will, was das Frontend sehen kann, liest `routers/albums.py`
  ab Zeile ~460 (`get_album`) — dort und nur dort entsteht die Struktur.

---

## 3. Wo die Daten liegen

Vier klar getrennte Orte. Die Trennung ist Absicht: Nur der erste ist wertvoll.

### a) Foto-Trees — die eigentlichen Daten

```
/volume1/photo/                        ← Shared Space  (MPD_PHOTO_SHARED)
├── 2019-Toskana/
│   ├── album.json                     ← das Album (PDX-Format, Abschnitt 4)
│   ├── IMG_0001.jpg                   ← Originale, unangetastet
│   ├── .mpd_backups/                  ← 10 letzte album.json-Stände
│   ├── IMG_0001.jpg.bak               ← Foto-Editor: rollierende Backups
│   ├── IMG_0001.jpg.bak1               ←   .bak (neuestes) … .bak4, Cap 5
│   └── .mpd_stats.json                ← EXIF-Cache dieses Albums (Plus: Stats)
├── .mpd_cache/                        ← (Plus: Stats)
│   ├── mpd_stats_global.json
│   ├── mpd_stats_geo.json
│   └── mpd_geo_coord_cache.json       ← nie löschen: gekaufte Geocoding-Abfragen
└── …

/volume1/homes/<user>/Photos/          ← Personal Space, pro User
```

Der Personal-Pfad steht pro User in `users.json` (`personal_path`), nicht in
einer Konvention. Zwei User können auf denselben Ordner zeigen.

### b) `/volume1/docker/MPD/data` → im Container `/data`

So heißt der Ort im Synology-Paket; mit Docker Compose ist es der
eingehängte Datenordner (`./data`).

```
/data/
├── users.json          ← User, Rollen, Argon2id-Hashes, Modul-Freischaltung
├── mpd.sqlite          ← sessions, shares, contacts
├── mpd-notify.sqlite   ← Benachrichtigungen (+ SQL-Dump daneben, geht ins Backup)
├── mpd-license.json    ← nur wirksam bei MPD_LICENSE_ENFORCE=1
├── trails/<user>.sqlite ← Standorthistorie, eine Datei je Nutzer (Plus: Trails)
├── import/             ← Einlieferungsordner, Google-Zeitachse (Plus: Trails)
├── mpd-settings.json   ← Instance-Feature-Flags (Sharing an/aus, Demo an/aus)
└── thumb-cache/
    ├── thumb/ab/ab3f….webp      ← Pipeline A (Abschnitt 6)
    ├── cover/…
    ├── preview/…
    ├── full/…
    └── fs/…                     ← Pipeline B — anderer Satz, andere Größen!
```

Dieser Baum ist **zum größten Teil wegwerfbar** — aber nicht ganz. Der
Thumb-Cache baut sich neu auf, Sessions bedeuten nur „alle müssen sich neu
anmelden". **Unersetzlich sind:**

- `users.json` — Konten, Rollen, Modul-Freischaltung
- **`trails/`** (Plus) — die Standorthistorie entsteht nie neu; sie muss bei
  jedem Umzug mit
- `mpd-settings.json` und `mpd.env.local` — darin stehen Schlüssel und das
  SMTP-Passwort, die nur bei Dritten neu zu beschaffen sind

Die Rettungskopie `.mpd-keep` legt das Wichtigste davon nachts in die
persönlichen Foto-Ordner (`core/keep.py`) — Konten und Schlüssel zum Admin,
den Standortverlauf zur jeweiligen Person.

### c) Neben dem Code

`backend/mpd_search_index.json` — der Suchindex (Plus: Suche). Wird bei jeder
Albumänderung über das Ereignis `album_changed` gelöscht und beim nächsten
Suchen neu gebaut.

`backend/my-photo-diary.log` — rotierend, 10 MB, 5 Generationen.

### d) `mpd.env` (gitignored)

Alle Secrets und Pfade. Wird von `backend/config.py` mit einem
20-Zeilen-Parser gelesen — **kein** `python-dotenv`, keine Typkonvertierung
außer Hand-`int()`. Ein Tippfehler im Schlüsselnamen fällt nicht auf: Der
Default greift stillschweigend.

---

## 4. Das Datenmodell: PDX / `album.json`

Das ist der Kern des Projekts. Ein Album ist eine geordnete Liste von
**Elementen** — kein reines Foto-Verzeichnis, sondern eine Seitenfolge.

```jsonc
{
  "version": "1.5",              // gültig: 1.1 – 1.5
  "meta": {
    "title": "Toskana 2019",
    "year": 2019,
    "thumbnail": "IMG_0042.jpg", // Cover: Dateiname, nicht Pfad
    "thumbnail_crop": { … },     // optional
    "description": "…",
    "created":  "…",
    "modified": "…",
    "show_locked": false         // zeigt gesperrte Elemente dem Besitzer
  },
  "elements": [
    { "id": "0010", "type": "text",  "text": "<p>…</p>" },
    { "id": "0020", "type": "photo", "file": "IMG_0001.jpg",
      "resolution": "4032x3024",    // lazy nachgetragen
      "blurhash": "LEHV6n…",        // lazy nachgetragen
      "locked": false },
    { "id": "0030", "type": "photo", "file": "IMG_9999.jpg",
      "source_album": "2018-Ligurien" },   // PDX 1.4: Foto aus fremdem Album
    { "id": "0040", "type": "tour",  "file": "wanderung.gpx" }
  ]
}
```

**Regeln, die das Backend erzwingt** (`validate_album_data`,
`routers/albums.py` ab Zeile 95) — jeder Verstoß ist ein HTTP 400 beim
Speichern:

- `id` ist **genau vierstellig** (`^\d{4}$`) und innerhalb des Albums eindeutig.
  Das begrenzt ein Album auf 10.000 Elemente — bisher kein Problem.
- Elementtypen: `text`, `photo`, `video`, `separator`, `document`, `audio`,
  `map`, `tour`, `link`. **Unbekannte Typen werden toleriert und
  übersprungen**, nicht abgelehnt — Vorwärtskompatibilität mit Absicht.
**Die `ALLOWED`-Weißliste in `update_album` ist die leichteste Falle.** Sie
begrenzt, welche Felder je Elementtyp überhaupt gespeichert werden. Fehlt ein
Typ dort, behält der Server nur `id` und `type` — alle anderen Felder werden
stillschweigend verworfen, und die anschließende Validierung meldet ein
fehlendes Pflichtfeld. Der Fehler zeigt sich also als Formatfehler, obwohl der
Client alles korrekt geschickt hat.

- `link` (seit v1.4.1) ist der einzige Typ **ohne** Datei im Albumordner: er
  trägt statt `file` ein `url`-Feld, das nur `http`/`https` sein darf
  (`_LINK_URL_RE`). Beide Anreicherungs-Schleifen lassen ihn unverändert
  durch — er braucht keine Medien-URL.
- `meta` muss `title`, `year`, `thumbnail`, `created`, `modified` haben.
- Leere Alben sind gültig (PDX 1.4). Die Rückfrage „wirklich leer speichern?"
  ist reine Frontend-Logik.
- `source_album` darf nur ein einfacher Ordnername sein — nie ein Pfad.

**Schreibvorgänge sind abgesichert:**
`create_backup()` (Zeitstempel-Kopie nach `.mpd_backups/`, 10 werden behalten)
→ `_write_atomic()` (tmp-Datei + `os.replace`). Ein halb geschriebenes
`album.json` kann nie beobachtet werden.

**Migrationen laufen still beim Lesen.** `_migrate_hidden_to_locked()` ersetzt
die alte Nomenklatur `style: "hidden"` durch `locked: true`. Solche Migrationen
schlagen erst beim nächsten Speichern auf die Platte durch — eine Datei kann
also lange im alten Format liegen und trotzdem korrekt angezeigt werden.

---

## 5. Backend: Schichten

```
main.py            App, 2 Middlewares, Login/Logout, Lifespan
  │
  ├─ routers/      19 Router + deps.py. HTTP rein, JSON raus. Kennen `Request`.
  │    deps.py     ⭐ Der Türsteher: require_session, get_path,
  │                   is_readonly, available_spaces, require_module
  │
  ├─ core/         Fachlogik. Kennt kein HTTP, kein FastAPI.
  │    session.py / sessiondb.py / userdb.py     Auth
  │    filesystem_photos.py                      Ordner + Dateien auflisten
  │    thumb_pipeline.py                         Bilder verkleinern
  │    photo_edit.py / exif.py                   destruktive Bearbeitung, EXIF
  │    library_warmer.py                         Vorwärmen beim Start
  │    shares.py / contacts.py / mailer.py       Sharing + Beisteuern
  │    i18n.py                                   Backend-Übersetzungen
  │    http_range.py                             Range/206 (Starlette kann's nicht)
  │    timeline_index.py                         Timeline je Space, TTL 5 min
  │    notifydb.py / notify_jobs.py              Benachrichtigungen
  │    scheduler.py                              die eine APScheduler-Instanz, Nachtjobs
  │    retention.py / keep.py                    Aufräumen, Rettungskopie
  │    geocoder.py                               Ortsname zu GPS (Nominatim)
  │    events.py                                 Kern meldet, Module hören zu
  │    license.py / rsa_min.py                   Modul-Lizenz, nur bei ENFORCE=1
  │    ratelimit.py                              Querschnitt
  │    trails_*.py / geo_place.py / gpx_meta.py  (Plus: Trails, Tours)
  │
  └─ diary_memories/   Memories: Tagesmeldung „heute vor X Jahren“ (Kern)
```

### Kern und Module

Der Kern **importiert kein Plus-Modul**. Drei Mechanismen halten das zusammen:

- **Router optional laden:** `main.py` lädt `routers/{stats,search,tours,
  trails}.py` per `ImportError`-Schutz. Fehlt eine Datei, fehlt nur ihr
  Endpunkt.
- **Ereignisse statt Aufrufe:** Der Kern meldet über `core/events.py`
  (`album_changed`, `photo_meta_changed`), Module hängen sich beim eigenen
  Import ein — etwa die Suche, um ihren Index zu verwerfen. Ein Fehler in einem
  Handler wird geloggt, nie geworfen.
- **Nachtjobs im Kern:** `core/scheduler.py` trägt Aufräumen, Rettungskopie und
  Benachrichtigungen; Memories hängt seine Jobs dort an (`add_job()`).

`installed_modules()` in `routers/deps.py` sagt, welche Module liegen;
`user_modules()` schneidet damit. `test_kern_ohne_module.py` sichert das ab.
Wer im Kern etwas aus einem Modul braucht, meldet ein Ereignis — nie ein
Import in die Gegenrichtung.

**Die eine Datei, die man gelesen haben muss, ist `routers/deps.py`** (242
Zeilen). Dort steht das komplette Berechtigungsmodell:

| Rolle | Shared | Personal |
|---|---|---|
| `admin` | lesen · beitragen · kuratieren | lesen · beitragen · kuratieren |
| `editor` | lesen · beitragen | lesen · beitragen · kuratieren |
| `viewer` | nur lesen | lesen · beitragen · kuratieren |
| Demo | **gar nicht sichtbar** | nur lesen |

*Kuratieren* heißt Alben anlegen, gestalten, löschen (`is_readonly`);
*beitragen* heißt hochladen, einsortieren und unreferenzierten Ausschuss
löschen (`can_contribute`). Jedes echte Konto liest den Familienbestand und
verwaltet seinen eigenen Bereich vollständig; Demo bleibt ausgeschlossen,
weil `/demo-login` öffentlich ist. Bearbeiten (`may_edit_photo`) spiegelt die
Löschregel, Teilen (`may_share`) ist je Konto abschaltbar. Was ein Konto
darf, liefert `/api/whoami` als `rights` — für die Oberfläche; durchgesetzt
wird an jedem Endpunkt einzeln.

`get_api(session, space)` ist die einzige legitime Art, an einen Pfad zu
kommen. Wer `PHOTO_PATH_SHARED` direkt importiert und selbst zusammensetzt,
umgeht die Rollenprüfung.

### Nebenläufigkeit

Alles läuft in **einem** Uvicorn-Prozess (kein Gunicorn, keine Worker). Das
heißt: Modul-globale Caches funktionieren, brauchen aber Disziplin.

| Cache | Ort | Lebensdauer | Invalidierung |
|---|---|---|---|
| Albumliste | `_ALBUMS_CACHE` in `albums.py` | 30 s TTL, Key `(user, role)` | `invalidate_albums_cache()` |
| Suchindex (Plus) | Datei `mpd_search_index.json` | bis zur nächsten Änderung | Ereignis `album_changed` |
| Timeline-Index | `_CACHE` in `core/timeline_index.py` | 5 min TTL, je Space | an allen Schreibstellen + TTL |
| Login-Fehlversuche | `_LOGIN_FAILS` in `main.py` | 5 min gleitend | automatisch |
| Thumbnails | Platte | bis sich die mtime ändert | implizit über den Schlüssel |
| Ortsnamen (Lightbox) | `/data/geo_reverse_cache_v4.json` | **nie** — bewusst, auch leere Antworten | keine |


**Beim Schreiben immer die Albumliste entwerten und `album_changed` melden.** Vergisst man
`invalidate_albums_cache()`, sieht der Nutzer seine Änderung bis zu 30
Sekunden lang nicht — ein Fehlerbild, das sich beim Nachschauen von selbst
„repariert" und deshalb schwer zu fassen ist.

Blockierendes Datei-I/O gehört in `asyncio.to_thread()`. Die NAS ist langsam;
ein synchroner Zugriff im Event-Loop blockiert alle anderen Nutzer.

---

## 6. Bilder: zwei Pipelines, ein Cache-Schlüssel

Das ist die häufigste Stolperfalle im Projekt.

**Pipeline A — `core/thumb_pipeline.py`** (die aktuelle, WebP):

| Name | längere Kante | Qualität | API-Alias |
|---|---|---|---|
| `thumb` | 400 | 80 | `sm` |
| `cover` | 900 | 80 | `m` |
| `preview` | 2048 | 82 | `xl` |
| `full` | 4096 | 85 | *(keins)* |

**Pipeline B — `core/filesystem_photos.py`** (älter, JPEG): benutzt
**dieselben Kurznamen** `sm`/`m`/`xl` für **200/400/1200 px** und legt in
`thumb-cache/fs/` ab.

Beide sind aktiv. Die Pfade sind getrennt, die Namen kollidieren. Wer sie
zusammenführt, muss beide Cache-Ordner und beide Aufruferketten prüfen.

**Cache-Schlüssel:** `sha1(absoluter Pfad + mtime_ns)`, abgelegt als
`{size}/{key[:2]}/{key}.webp`. Zwei Konsequenzen:

- Jede mtime-Änderung entwertet **alle** Varianten eines Fotos auf einmal.
  Ein `touch` über den Bestand erzwingt eine Neuberechnung von allem.
- **Verwaiste Einträge räumt `core/retention.py` nachts ab**
  (`sweep_thumb_cache()`, seit 06.09.2026). Gekehrt wird gegen die Menge
  der gültigen Schlüssel, nicht nach Alter. Per Vorgabe nur Trockenlauf,
  `MPD_THUMB_SWEEP=1` schaltet scharf. Drei Riegel gegen das Löschen
  gültiger Bilder: fehlt ein Bestand auf der Platte oder ist die
  Schlüsselmenge leer, wird nicht gekehrt; frisch Geschriebenes hat eine
  Schonfrist. Der zweite Satz unter `thumb-cache/fs/` wird **nicht** gekehrt.

**`library_warmer.py`** läuft bei *jedem* App-Start über alle Alben und
erzeugt fehlende Varianten vor. Liegt alles im Cache, ist er in Sekunden
durch. Fehlt viel, läuft die NAS minutenlang heiß; `MPD_WARMER_THREADS`
(Vorgabe 3) begrenzt die Last, `Image.draft()` verhindert volle Decodes
großer Panoramen.

---

## 7. Frontend: kein Build, keine Module

26 JS-Dateien, 12 HTML-Seiten, 8 CSS-Dateien. Kein npm, kein Bundler, kein
`import`. Die Konsequenzen muss man kennen, sonst sucht man lange:

### Globale Variablen statt Modulen

`js/album-state.js` deklariert mit `var` alles, was mehrere `album-*.js`
gemeinsam benutzen: `albumData`, `isEditMode`, `undoStack`, `albumName`,
`albumSpace`. Der Kommentar in der Datei sagt es deutlich:

> `var` statt `let`/`const`: nur so sind die Deklarationen über mehrere
> plain-script-Tags hinweg sichtbar.

**Das heißt: Es gibt keinen Namensraum.** Eine neue globale Funktion `save()`
in irgendeiner Datei überschreibt eine gleichnamige in einer anderen — ohne
Fehlermeldung. Neue Funktionen bekommen deshalb ein Präfix
(`albumSave…`, `lbZoom…`).

### Die Ladereihenfolge in `album.html` ist Vertrag

```
i18n.js → version-watch.js → … → theme.js → mpd-dialog.js → blurhash.js
→ photo-zoom.js → lightbox-core.js → lightbox-exif.js → lightbox-editor.js
→ album-state.js → album-render.js → companion.js → album-edit.js
→ album-drag.js → album-save.js → album-insert.js → album-mixed-picker.js
→ album-ui.js → album-map.js → album.js
```

`album.js` ist der Einstiegspunkt und muss letzter bleiben.

### `?v=N` — Cache-Busting von Hand

`<script src="/js/album-render.js?v=12">`. **Diese Zahl wird nicht automatisch
erhöht.** Wer eine JS-Datei ändert, muss die Nummer im HTML manuell hochsetzen
— sonst liefern Browser und WebView die alte Datei aus.

Zwei Netze fangen das ab: `routers/static.py` schickt für `/js/`, `/css/`
und HTML `Cache-Control: no-store`, und `js/version-watch.js` vergleicht bei
jedem `visibilitychange` den `build_token` von `/api/version` (das größte
mtime über Backend **und** Frontend) und lädt bei Abweichung stillschweigend
neu. Trotzdem: die Nummer hochsetzen — sie ist das, was Proxys und der
WebView-Cache sehen.

**`/css/` hing bis zum 13.09.2026 in keinem der beiden Netze.** Dieser
Abschnitt behauptete es trotzdem. Aufgefallen an einer Änderung am
Zuschnitt-Editor: Das JS kam beim Nutzer an (no-store), das zugehörige CSS
nicht, weil die Nummer nicht hochgesetzt war — die Änderung war damit
wirkungslos, und zwar unsichtbar, auch bei hartem Reload. Seither bekommt
CSS denselben Header.

### `click` kommt immer nach `pointerup`

Der Bild-Container der Lightbox trägt seit jeher `onclick="toggleZoom()"`.
Gleichzeitig wertet `_onPointerUp` in `lightbox-core.js` Tipps, Doppeltipps
und Wischgesten aus. **Beide sehen dieselbe Berührung**, und der `click`
trifft immer als Letzter ein — nachdem die Gestenschicht ihren Zustand schon
geändert hat.

Diese Reihenfolge hat an einem Tag drei Fehler erzeugt:

- Ein Einzeltipp am Tablet lief am Gestenpfad vorbei und zoomte, statt die
  Bedienung auszublenden.
- Der Doppeltipp zoomte korrekt — und der nachfolgende `click` sah den
  frischen Zoom und setzte ihn sofort zurück.
- Am Desktop bekam jedes Verschieben im Zoom eine weitere Zoomstufe, weil
  nach dem Ziehen ebenfalls ein `click` folgt.

**Wer hier etwas ändert, muss beide Pfade zusammen denken.** Faustregeln:
Zustandsabfragen im `click`-Handler sehen bereits das Ergebnis der Geste, nie
den Zustand davor. Und wer „geklickt" von „gezogen" unterscheiden will,
braucht ein Flag aus `_onPointerMove` (`_mouse.moved`) — `dragging` allein
sagt nur, dass die Taste gedrückt war.

### Zwei Zoom-Implementierungen

- `js/lightbox-core.js` — das Vollbild. Eigener Pinch-Code, eigene Transforms.
- `js/photo-zoom.js` — die Lupe auf den Kacheln. Wird von der Lightbox **nicht**
  aufgerufen.

Änderungen am Zoomverhalten müssen meist in beide.

### Nie native Dialoge

`confirm()` / `alert()` / `prompt()` erscheinen im MPD-Wrapper inzwischen
wieder — er setzt einen `WebChromeClient`. Tabu bleiben sie trotzdem: Der
Systemdialog stellt „Auf der Seite https://… steht:" voran und ignoriert das
Theme. In jedem anderen WebView-Einbau liefert
`confirm()` weiterhin still `false`, die Aktion bricht dort unbemerkt ab.
Stattdessen `window.mpdConfirm({…})` aus `js/mpd-dialog.js`, in der Lightbox
den Helper `_lbConfirm` aus `lightbox-exif.js`.

### i18n greift tiefer, als man denkt

`js/i18n.js` **ersetzt `window.fetch`** und hängt jedem gleichrangigen Request
einen `Accept-Language`-Header an. Deshalb kommen Backend-Fehlermeldungen in
der richtigen Sprache. Wer das Fetch-Verhalten debuggt, muss diesen Wrapper
kennen — er ist der Grund, warum ein Request im Netzwerk-Tab anders aussieht
als im Code.

### Externe Abhängigkeiten kommen vom CDN

Leaflet, leaflet.geodesic, Sortable.js werden per `<script src="https://…">`
geladen. **Ohne Internet funktionieren Karten und Drag & Drop nicht** — auch
wenn MPD sonst vollständig selbst gehostet ist.

### Die Hausschrift kommt nicht vom CDN

Seit 08.09.2026 (CI 1.2) hat MPD eine Hausschrift: Archivo für die
Oberfläche, Literata für Tagebuchtexte, Courier Prime für Filmstrip und
Code, Playwrite DE VA als Handschrift **nur** am Memories-Banner. Alle
vier liegen als woff2 unter `frontend/fonts/` und werden über
`/css/fonts.css` eingebunden — bewusst kein Google-Fonts-Link, sonst
verließe jeder Seitenaufruf das Haus. `style.css` kennt dafür je Theme
zwei Werte: `--font-family` (Oberfläche) und `--font-text`
(Textblöcke). Wer eine Schrift austauscht, gibt der Datei einen **neuen
Namen** — `/fonts/` wird ein Jahr `immutable` gecacht. Die TTF-Quellen für
App und TeX liegen unter `docs/fonts/`, die Referenz-Typografie der
PDX-Stile steht in der CI.

---

## 8. Die Wege hinein

Alle laufen durch `AuthMiddleware` in `main.py`. Die Listen dort sind die
Wahrheit: `_PUBLIC_PATHS` (einzelne Pfade) und `_PUBLIC_PREFIXES` (Präfixe).

**1. Session — der Normalfall, zwei Trägerformen**
Login → Argon2id gegen `users.json` → Token in SQLite. Ausgeliefert wird er
als Cookie `mpd_session` (30 Tage, httponly) **und**, wenn der Login mit
`want_token` kam, zusätzlich im JSON — die native App schickt ihn dann als
`Authorization: Bearer …`. `request_token()` in `core/session.py` nimmt beide
entgegen, Bearer hat Vorrang. Ratelimit: 5 Fehlversuche in 5 Minuten → 60 s
Sperre pro IP (in-memory).

**2. Share-Token (`/share/{token}`)**
Magic-Link, komplett an der Session vorbei. In der DB liegt **nur der Hash**.
`routers/share.py` liefert eine gefilterte Album-Ansicht: `show_locked` wird
hart auf `false` gezwungen, gesperrte Elemente kommen gar nicht erst ins JSON,
und jeder Medien-Proxy prüft, ob die Datei im freigegebenen PDX referenziert
ist. Ratelimit greift schon **vor** dem Token-Lookup — sonst fluten
Scanner-Bots das Log. Seit 09/2026 gibt es zusätzlich Ablaufdaten und
Einzelfoto-Shares (`share-photo.html`).

**3. Contrib-Token (`/contrib/{token}`)** — „Beisteuern"
Gastlink zum *Hochladen* statt zum Ansehen (`frontend/contrib.html`,
`create_contrib`/`resolve_contrib` in `core/shares.py`). Gleiches Ratelimit
wie `/share/`.

**4. Publish-Token (`POST /api/notify/publish`)**
Für NAS-Skripte ohne Session, die Benachrichtigungen einliefern
(`MPD_NOTIFY_PUBLISH_TOKEN`). Ohne gesetzten Token gibt es den Endpunkt nicht
(404).

**5. Gerätetoken für den Standort-Ingest (`POST /api/trails/ingest`, Plus: Trails)**
OwnTracks meldet sich per Basic-Auth (Nutzername + Gerätetoken, alternativ
das MPD-Passwort) oder per `?user=…&token=…` für Clients ohne Basic-Auth.
`verify_device_token()` in `core/trails_store.py`.

**Der Demo-Zugang ist eine Eigenschaft der Installation, kein Produktmerkmal.**
`/demo-login` meldet ohne Passwort als Schau-Konto an — sinnvoll genau dort,
wo eine Landing-Page darauf verlinkt, und sonst nirgends. Bis 06.09.2026 war
die Route überall öffentlich, `demo_enabled` stand auf `true`, und die
Zugangsdaten (`mpd-demo`/`mpd-demo`) standen im Code; gehalten hat das nur,
weil das Konto anderswo nicht existiert. Seither entscheidet `MPD_DEMO` in
`mpd.env` (Vorgabe **aus**) samt gesetztem `MPD_DEMO_PASSWORD`, ob es die
Route überhaupt gibt — ohne sie ist `/demo-login` weder öffentlich noch
vorhanden (404). Der Kill-Switch `demo_enabled` aus den Einstellungen wirkt
erst danach: Er kann abschalten, was existiert, aber nichts einschalten.
Das ist die Linie, an der man solche Fragen entscheidet — **eine
Einstellung, die man umlegen kann, darf keine Sicherheitszusage sein.**

**Quer dazu: `must_change_password`.** Ist das Flag im `users.json` gesetzt,
lässt die Middleware nur noch `_MUST_CHANGE_ALLOWED` durch (die
Änderungsseite, ihre API, Logout, `/api/whoami`). Gilt für Web und App
gleichermaßen.

### Modul-Freischaltung

Vier Module gehören zu MPD Plus: `tours`, `trails`, `search`, `stats`.
Drei Schichten entscheiden, ob ein Konto ein Modul benutzen darf,
alle drei müssen zustimmen:

1. **Installiert?** `installed_modules()` — liegt die Router-Datei überhaupt da.
2. **Freigeschaltet?** Das Feld `modules` in `users.json`. **Fehlt das Feld,
   bekommt das Konto alles** (Grandfathering), Admin sowieso.
3. **Lizenziert?** Nur bei `MPD_LICENSE_ENFORCE=1`: `core/license.py` prüft
   eine signierte Lizenzdatei offline und schneidet mit — dann auch beim Admin.

`require_module()` in `deps.py` setzt das an den Modul-Routern durch.

Tours ist **soft-gated**: `routers/media.py` hat bewusst **keinen einzigen**
`require_module`-Guard, deshalb liefert `/api/gpx/…` eingebettete
Tour-Elemente weiter aus. Gesperrt sind nur `/tours` selbst und das Kopieren.

---

## 8a. Versionen — fünf voneinander unabhängige Zähler

In MPD gibt es fünf Dinge, die „Version" heißen und **nichts** miteinander zu
tun haben. Wer eine davon anfasst, sollte wissen, welche.

| Was | Beispiel | Quelle der Wahrheit |
|---|---|---|
| **App-Version** | `3.0.448` | `backend/version.txt`: `<Produkt>.<Commit-Zahl>`, Produkt aus `backend/product_version.txt` |
| **PDX-Format** | `1.5` | `SUPPORTED_PDX_VERSIONS` in `routers/albums.py`, Specs unter `docs/specs/` |
| **Frontend-Assets** | `album-render.js?v=17` | von Hand in den HTML-Dateien |
| **Android-APK** | `versionCode 3` / `1.2` | `app/build.gradle.kts` im Wrapper-Projekt |

**App-Version.** `main.py::_read_version()` liest `backend/version.txt`, mit
`git rev-list --count` als Rückfall und `<Produkt>.0` als letzter Notnagel.
Im öffentlichen Kern schreibt der Export die Datei mit — dort zählt die
Historie nicht mit dem Stand, aus dem Paket und Kern gebaut werden.
Von dort geht sie an `/api/version`, an `/health` und über
`/api/version` ins Frontend. **Sie darf nirgends fest verdrahtet werden.**
Genau das war zweimal der Fall: `/health` meldete dauerhaft `2.0.0`, und
`js/theme.js` trug eine feste Konstante `MPD_VERSION = '2.0.0'`, während die
echte Version längst `2.0.135` war.

Dazu kam eine Kollision: `theme.js` **und** `version-watch.js` beschrieben
beide `#mpd-version-badge`, mit unterschiedlichem Text. Welcher zu sehen war,
entschied die Ladereihenfolge. Jetzt gilt: der Badge gehört `theme.js`, die
Fußzeile `.mpd-version-text` gehört `version-watch.js`, und beide beziehen
ihren Wert aus `/api/version`.


**Frontend-Assets:** siehe Abschnitt 7, `?v=N` wird von Hand hochgesetzt.

## 8b. Die jüngeren Subsysteme

Sie sind nach dem ursprünglichen Bauplan dazugekommen und folgen ihm trotzdem:
Fachlogik in `core/`, HTTP in `routers/`, keine Datenbank für Inhalte.

**Timeline** (`routers/timeline.py`, `core/timeline_index.py`). Alle Fotos
eines Space chronologisch, quer über die Alben — auch **unkuratierte** Dateien,
die in keinem `album.json` stehen (Flag `referenced: false`). Der Index liegt
im RAM je Space mit 5 min TTL und wird an allen Schreibstellen entwertet; das
TTL fängt Dateien ab, die per SMB an der API vorbei dazukommen. Cursor-Paging.
Timeline gehört zum Grundpaket, ist **kein** kostenpflichtiges Modul.

**Benachrichtigungen** (`routers/notify.py`, `core/notifydb.py`,
`core/notify_jobs.py`). Eigene SQLite `/data/mpd-notify.sqlite` plus
menschenlesbarer SQL-Dump daneben, der ins Nachtbackup wandert. Abholung per
Poll/Long-Poll statt Push-Dienst — das hat den ntfy-Container ersetzt, der
inzwischen gelöscht ist. Fremde NAS-Skripte liefern über
`POST /api/notify/publish` mit Token ein. Fan-out nach Ereignisart:
`alert` → Admins, `upload` → alle außer dem Verursacher, `curation` →
Admin und Editor.

**Nachtjobs** (`core/scheduler.py`). Eine APScheduler-Instanz im selben
Prozess: Aufräumen (`core/retention.py`), Rettungskopie (`core/keep.py`, dazu
ein Start-Lauf, wenn noch keine existiert), Upload-Tagesmeldung und
Kuratier-Wochenbericht. Module hängen ihre Jobs mit `add_job()` an. Die
Zeiten für Aufräumen, Rettungskopie und Memories stehen in `mpd.env.example`,
Tagesmeldung (19:00) und Wochenbericht (sonntags 18:00) fest in
`core/notify_jobs.py`; alle folgen der Zeitzone des Containers (`TZ`).


### Der HTTP-Vertrag zur nativen App

Vier Dinge, die 09/2026 additiv dazugekommen sind und beide Clients betreffen:

- **`ETag`** auf `/api/thumbnail` und dem Original-Download —
  `sha1(Pfad + mtime_ns)`, also derselbe Wert wie der Thumb-Cache-Schlüssel
  (`etag_for()`). Dazu `Cache-Control: no-cache`, `If-None-Match` → 304.
  Der ETag gewinnt vor Range. Das löste den Fall „Foto bearbeitet, Client
  zeigt 24 h das alte Bild".
- **Range/206** auf Video, Audio und Download (`core/http_range.py`) —
  selbst gebaut, weil Starlette 0.35 kein Range kann und ein
  Framework-Upgrade nicht in Frage kam.
- **Bearer-Token** gleichwertig zum Cookie (Abschnitt 8).
- **Opt-in-Konfliktschutz** beim Album-Speichern: Wer `X-MPD-Base-Modified`
  mitschickt, bekommt bei zwischenzeitlicher Änderung ein 409 statt eines
  stillen Überschreibens.


---

## 9. Die Doppelungen — bitte hier nachschlagen

Diese Stellen existieren **zweimal**. Wer eine ändert und die andere vergisst,
baut einen Fehler, der nur in einem der beiden Wege auftritt und deshalb spät
auffällt.

| Was | Stelle A | Stelle B | Unterschied |
|---|---|---|---|
| Element-Anreicherung mit Thumbnail-URLs | `media_urls()` in `routers/deps.py` (genutzt von `albums.py` + `timeline.py`) | `routers/share.py` ~Z. 740 | A: `/api/thumbnail/{space}/…`, liefert auch `full` · B: `/share/{token}/thumbnail/…`, **bewusst ohne `full`/`original`**, kennt dafür `audio`/`tour` |
| Thumbnail-Größen | `core/thumb_pipeline.py` (400/900/2048/4096, WebP) | `core/filesystem_photos.py` (200/400/1200, JPEG) | gleiche Kurznamen, andere Werte, anderer Cache-Ordner |
| Zoom | `js/lightbox-core.js` | `js/photo-zoom.js` | getrennte Implementierungen |
| Medien-Ausliefer-Endpunkte | `routers/media.py` (`/api/…`) | `routers/share.py` (`/share/{token}/…`) | B prüft zusätzlich gegen das freigegebene PDX |
| Erweiterungslisten `_PHOTO_EXTS` etc. | `routers/deps.py` | `core/filesystem_photos.py` | inhaltlich identisch, zwei Definitionen |

Faustregel: **Was ein angemeldeter Nutzer sehen kann, muss ein Share-Empfänger
getrennt bekommen.** `share.py` ist kein Wrapper um `albums.py`, sondern eine
Parallelimplementierung.

---

## 10. Ich will X ändern — wo fange ich an?

| Vorhaben | Dateien, der Reihe nach |
|---|---|
| Neuer Elementtyp im Album | `routers/albums.py`: `known_types`, `validate_album_data` **und `ALLOWED` in `update_album`** → `js/album-render.js` → `js/album-insert.js` → `routers/share.py` (Anreicherung!) |
| Thumbnail-Größe/Qualität ändern | `core/thumb_pipeline.py` (`SIZES`, `QUALITY`) → Cache-Ordner löschen → `routers/media.py` (`_SIZE_ALIAS`) |
| Neuer API-Endpunkt | passender `routers/*.py` → `require_session` + ggf. `require_module` → in `main.py` registrieren (nur bei neuem Router) |
| Berechtigungen | ausschließlich `routers/deps.py` |
| Neue Seite im Frontend | `frontend/*.html` → Route in `routers/static.py` → Script-Tags mit `?v=1` |
| Text/Übersetzung | `frontend/js/i18n.js` (UI, inline) bzw. `backend/core/i18n.py` (Fehlermeldungen) |
| Schrift austauschen | `frontend/fonts/` (neue Datei, neuer Name) → `frontend/css/fonts.css` → `--font-family`/`--font-text` in `style.css` → `docs/fonts/` + CI |
| Etwas am Login | `frontend/login.html` **+ Container-Restart** (wird in `main.py` auf Modulebene eingelesen) |
| Share-Verhalten | `routers/share.py` + `core/shares.py` + `frontend/share.html` |
| User anlegen/Rollen | CLI: `backend/scripts/manage_users.py` |
| Etwas an der Timeline | `core/timeline_index.py` (Index + Invalidierung) → `routers/timeline.py` |
| Neue Benachrichtigungsart | `core/notify_jobs.py` (Fan-out) → `core/notifydb.py` → `routers/notify.py` |
| Nachtjob | `core/scheduler.py` (Kern) bzw. `add_job()` aus dem Modul |
| Kern soll ein Modul benachrichtigen | Ereignis in `core/events.py`, Modul hängt sich per `subscribe()` ein |
| Am Entwickler-Handbuch | `docs/handbook.html` — geht per `COPY` ins Image, **braucht einen Neubau** (`docker compose up -d --build`) |

---

## 11. Was hier bewusst nicht steht

- **Setup, Umgebungsvariablen, Deploy-Kommandos** → `README.de.md`,
  `mpd.env.example`.
- **Modul-Fachlogik** (Stats-Auswertung, GPX-Parsing, Memories-Matching). Die
  sind jeweils in sich geschlossen und in den Dateiköpfen dokumentiert.

## 12. Bekannte Eigenheiten, die kein Fehler sind

Damit niemand Zeit verliert, weil etwas falsch aussieht:

- Der Container löscht bei jedem Start `__pycache__` — der Bind-Mount kann
  `.pyc`-Dateien von einer anderen Python-Version enthalten.
- `/api/version` liefert `build_date` aus dem größten mtime der `.py`-Dateien.
  Auf einem CIFS-Mount kann das sprunghaft aussehen.
- `httpx` ist in `requirements-dev.txt` auf `0.27.2` gepinnt. Neuere Versionen
  brechen die FastAPI-Testclients. Nicht „aufräumen".
