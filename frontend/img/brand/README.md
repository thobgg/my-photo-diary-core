# MPD-Marke — was gilt

**Stand 13.09.2026.** Diese Datei ist die Antwort auf die Frage „welches
Logo denn nun". Sie stand vorher nirgends, und deshalb liefen vier
Fassungen nebeneinander her.

## Die Marke

Motiv: **ein beschriebenes Blatt, davor ein Polaroid, daneben ein
Bleistift.** Nicht „Kamera mit Stift" — das war die Fassung bis April und
sagte „Foto-App". Das Blatt sagt „Tagebuch, in dem Fotos vorkommen", und
genau das ist MPD (vgl. Hero-Zeile: „Nicht nur ein Zeitstrahl. Ein
Tagebuch.").

Quelle ist `src/icon_master.svg` (512×512). Alles andere wird daraus
abgeleitet, nichts wird von Hand nachgezeichnet:

| Datei | Wofür |
|---|---|
| `src/icon_master.svg` | **Master.** Quadrat mit Hintergrund |
| `src/icon_fg.svg` | Vordergrund ohne Hintergrund, mit Android-Sicherheitszone — für Adaptive Icons |
| `src/icon_round.svg` | Kreisfassung |
| `src/icon_legacy.svg` | Squircle |
| `mpd-logo.svg` | **fürs Web** — Kopie der Squircle-Fassung, mit Kachel. Die HTML-Seiten laden diese Datei |
| `mpd-logo-plain.svg` | **ohne Kachel**, transparenter Grund. Daraus baut `spk/build.sh` das DSM-Paket-Icon — Synology setzt keine eigene Kachel darum, mit Rahmen sähe es falsch aus |
| `mpd-icon-*.png` | 16–512 px, aus dem Master gerastert (10.07.2026) |
| `draft/` | Android-Adaptive-Icon-Vorlagen samt Anleitung für Android Studio |

Warum fürs Web die Squircle-Fassung: bei 30 px im Seitenkopf hat sie eine
definierte Kante auf hellem wie dunklem Grund. Der reine Vordergrund trägt
die Android-Sicherheitszone und wirkt dort verloren; das harte Quadrat
wirkt wie ein aufgeklebter Kasten.

## Was NICHT mehr gilt

`archiv-2026-04/` — der komplette Satz vom April: die dunkelblaue Kamera
mit dem goldenen Stift, dazu die davon abgeleiteten Modulmarken für Search,
Memories und Stats. Der Ordner hiess bis zum 13.09.2026 `current/`, was
seit Mai nicht mehr stimmte.

**Wegwerfen ist er trotzdem nicht.** Die Modulmarken sind sauber aus der
damaligen Hauptmarke abgeleitet — gleiches Raster 140×140, gleiche
Markenfarben, wiederkehrende Grundformen. Das ist die Vorlage, an der sich
neue Modulmarken auf der heutigen Grundform orientieren sollen. Dazu liegt
hier auch `mpd-logo-mai.svg`, die Zwischenstufe vom 12.05. (Blatt mit
Kamera), aus der die heutige Marke hervorgegangen ist.

## Android-Icons erneuern

Die Launcher-Icons entstehen nicht hier, sondern in Android Studio. Das
Wissen stand bis zum 13.09.2026 in `draft/README.md`, zusammen mit Dateien
aus dem Mai-Stand; die Dateien sind weg, die Anleitung steht hier:

1. Im Project-Panel `app → res → mipmap`, dort `ic_launcher` und
   `ic_launcher_round` loeschen.
2. Rechtsklick auf `res` → **New → Image Asset**, Icon Type
   *Launcher Icons (Adaptive and Legacy)*, Name `ic_launcher`.
3. **Foreground Layer:** Asset Type *Image*, Pfad `src/icon_fg.svg`.
   **Trim: No** — sonst verrutscht das Motiv. **Resize: 100 %.**
4. **Background Layer:** Asset Type *Color*, Hex **`F4EFE6`**.
5. **Monochrome Layer** (Themed Icons ab Android 13): braucht eine
   einfarbige Fassung — die gibt es zur heutigen Marke noch nicht, s. unten.
6. **Legacy Tab:** Generate Legacy Icon *Yes*, Generate Round Icon *Yes*.

## Offen

- **Modulmarken fehlen** für Trails, Tours, Memories und Share auf der
  heutigen Grundform. Die CI v1.2 führt sie als Lücke — kannte dabei aber
  die Memories-Marke aus dem April nicht, weil sie in `docs/` lag, wo kein
  Code hinsieht. Search existiert im April-Satz bereits als „Blatt mit
  Lupe" und passt zur neuen Grundform besonders gut.
- **Favicon bei 16 px** braucht eine eigene, vereinfachte Fassung: nur
  Blatt und Polaroid, ohne Bleistift und Textlinien. Bei dieser Größe
  wird das volle Motiv zum Fleck.
- `MPD_CI_v1_2.tex` beschreibt unter „Bildmarken" noch den April-Stand und
  verweist auf `current/`. Nachzug fällig, die Datei ist in fremder
  Bearbeitung.
- **Einfarbige Fassung fehlt.** Android 13 und neuer faerbt Icons nach dem
  Systemthema ein und braucht dafuer einen Monochrome Layer. Zur Mai-Marke
  gab es ihn; er liegt als `archiv-2026-04/monochrome-mai.svg` und taugt als
  Vorlage — er traegt die Android-Masse (viewBox 108, Motiv auf 75 %
  skaliert und um 1,5 versetzt). Zur heutigen Marke fehlt er.
- Der Master benutzt einen Farbverlauf im Hintergrund. Die CI schliesst
  Verläufe aus („Kein Gradient, kein Glasmorphism") — gemeint sind dort
  UI-Flächen, beim App-Icon ist es üblich. Wenn es stören soll, ist es
  eine Zeile im Master.
