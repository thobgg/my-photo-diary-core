#!/bin/bash
# Paket-Einstieg: läuft VOR dem regulären /app/entrypoint.sh, nur im Synology-Paket.
# 1. mpd.env aus der Umgebung erzeugen (Wizard-Werte kommen als MPD_*-Variablen vom Docker-Worker),
#    Überschreibungen des Admins aus /data/mpd.env.local anhängen.
# 2. Erstkonten anlegen, wenn /app/pkgconf/bootstrap.json vorliegt (Argon2 gibt es nur hier).
# 3. Vor einem Versionswechsel die Betriebsdaten sichern.
# 4. Dem Reverse Proxy (DSM-Portal) über das Bridge-Gateway trauen.
set -u
DATA=/data
CONF=/app/pkgconf
mkdir -p "$DATA/state" "$DATA/backup"

# ── 0. Zeitzone ──
# Der Container hat keine eigene und laeuft sonst in UTC. Das ist nicht bloss
# kosmetisch: APScheduler legt seine Zeitplaene in der Ortszeit des Prozesses
# an, also feuert jeder Job verschoben. Bis 09/2026 kam der als "sonntags
# 18:00" gemeinte Kuratier-Bericht deshalb um 20:00.
# Rangfolge, absichtlich in dieser Reihenfolge:
#   1. TZ aus der Umgebung           — Compose und Tests setzen sie direkt
#   2. TZ= in /data/mpd.env.local    — der Notausgang, siehe unten
#   3. conf/timezone von prestart    — die DSM-Einstellung, der Normalfall
#   4. nichts                        — UTC, und das steht dann auch da
#
# Bewusst KEINE Frage im Assistenten: Die NAS kennt ihre Zeitzone bereits,
# eine zweite Stelle mit derselben Antwort driftet auseinander. Den Wert
# schreibt prestart (spk/scripts/common, detect_timezone) bei jedem Start neu
# — eine Aenderung in DSM greift also nach Stoppen und Starten des Pakets.
#
# Der Notausgang ist fuer den Fall, dass die DSM-Zeit nicht die richtige ist:
# NAS im Rechenzentrum, Familie woanders. Eine TZ-Zeile in mpd.env.local wirkt
# sonst NICHT — sie landet zwar in der erzeugten mpd.env, die liest aber nur
# config.py in sein Woerterbuch; in die Prozessumgebung kam sie nie, und nur
# dort schaut die C-Bibliothek hin.
#
# Zuerst gesetzt, damit auch die Zeitstempel weiter unten stimmen.
tz_quelle=""
if [ -n "${TZ:-}" ]; then
  tz_quelle="Umgebung"
elif [ -r "$DATA/mpd.env.local" ] &&
     TZ="$(sed -n 's/^[[:space:]]*TZ=//p' "$DATA/mpd.env.local" | tail -1 | tr -d "\"' \t\r")" &&
     [ -n "$TZ" ]; then
  tz_quelle="mpd.env.local"
elif [ -r "$CONF/timezone" ] && TZ="$(cat "$CONF/timezone")" && [ -n "$TZ" ]; then
  tz_quelle="DSM"
else
  TZ=""
fi
if [ -n "$TZ" ]; then
  export TZ
  echo "🕑 Zeitzone: $TZ (aus $tz_quelle) — $(date '+%Y-%m-%d %H:%M:%S %Z')"
else
  unset TZ
  echo "🕑 keine Zeitzone ermittelbar — der Container laeuft in UTC, alle Zeitplaene verschieben sich entsprechend"
fi

# ── 1. mpd.env ──
if [ -n "${MPD_PHOTO_SUBDIR:-}" ]; then export MPD_PHOTO_SHARED="${MPD_PHOTO_SHARED%/}/${MPD_PHOTO_SUBDIR#/}"; fi
{
  echo "# erzeugt vom Synology-Paket beim Start — nicht hier ändern, sondern /data/mpd.env.local"
  echo "# Zeitzone dieses Containers: ${TZ:-UTC} (kommt aus der DSM-Einstellung, nicht von hier)"
  env | grep -E '^MPD_[A-Z_]+=|^MAPTILER_KEY=|^ANTHROPIC_API_KEY=|^COMPANION_MODEL=|^MEMORIES_(HOUR|MINUTE)=' | grep -v '^MPD_PKG_VERSION=\|^MPD_PHOTO_SUBDIR=' | sort
  echo "MPD_THUMB_CACHE_DIR=/data/thumb-cache"
  echo "MPD_USERS_JSON=/data/users.json"
  echo "MPD_SESSION_DB=/data/mpd.sqlite"
  echo "MPD_NOTIFY_DB=/data/mpd-notify.sqlite"
  echo "MPD_TRAILS_DIR=/data/trails"
  echo "MPD_IMPORT_DIR=/data/import"
  echo "MPD_LICENSE_FILE=/data/mpd-license.json"
  if [ -f "$DATA/mpd.env.local" ]; then
    echo "# ── Überschreibungen aus /data/mpd.env.local ──"
    grep -E '^[A-Z_]+=' "$DATA/mpd.env.local"
  fi
} > /app/mpd.env
chmod 600 /app/mpd.env
if [ ! -f "$DATA/mpd.env.local" ]; then
  cat > "$DATA/mpd.env.local" <<'EOT'
# My Photo Diary — eigene Einstellungen. Jede Zeile hier gewinnt gegen die Vorgaben des Pakets.
# Alle Schalter mit Vorgabe: /app/mpd.env.example im Container bzw. spk/package/image/mpd.env.example.
# Änderungen greifen nach Stoppen/Starten des Pakets im Paketzentrum.
#MAPTILER_KEY=
#ANTHROPIC_API_KEY=
#MPD_SMTP_HOST=
#MPD_SMTP_PORT=587
#MPD_SMTP_USER=
#MPD_SMTP_PASS=
#MPD_FROM_ADDR=
#MPD_WARMER_THREADS=2
#MPD_WARMER=0          # Vorschaubilder nicht beim Start vorrechnen (kleine NAS)
#
# Zeitzone: Normalerweise nichts zu tun — das Paket uebernimmt die der NAS
# (DSM → Systemsteuerung → Regionale Optionen). Diese Zeile braucht nur, wessen
# NAS anders steht als die Menschen, deren Fotos darauf liegen. Sie bestimmt,
# wann "Heute vor X Jahren", der Tages-Digest und der Wochenbericht kommen.
#TZ=Europe/Berlin
EOT
  chmod 600 "$DATA/mpd.env.local"
fi

# ── 3. Sicherung vor Versionswechsel ──
ver="${MPD_PKG_VERSION:-unbekannt}"
last="$(cat "$DATA/.pkg_version" 2>/dev/null || true)"
if [ -n "$last" ] && [ "$last" != "$ver" ]; then
  dest="$DATA/backup/${last}-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$dest"
  for f in users.json mpd-settings.json mpd.sqlite mpd-notify.sqlite mpd-notify.sql mpd-license.json geo_reverse_cache_v4.json mpd.env.local; do
    [ -f "$DATA/$f" ] && cp -a "$DATA/$f" "$dest/"
  done
  for d in trails import; do [ -d "$DATA/$d" ] && cp -a "$DATA/$d" "$dest/"; done
  echo "📦 Sicherung vor Update ${last} → ${ver}: $dest"
  ls -1dt "$DATA"/backup/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
fi
echo "$ver" > "$DATA/.pkg_version"

# ── 2. Erstkonten ──
if [ -f "$CONF/bootstrap.json" ]; then
  if [ ! -f "$DATA/users.json" ]; then
    echo "👤 Erstkonten anlegen …"
    (cd /app/backend && python3 /app/bootstrap/bootstrap_users.py "$CONF/bootstrap.json") || echo "⚠️  Bootstrap fehlgeschlagen"
  fi
  rm -f "$CONF/bootstrap.json"
fi

# ── 3b. Persoenliche Bereiche: Namens-Links auf die Steckplaetze ──
# Die Paket-Deklaration kennt keine Kontonamen, deshalb haengen die Foto-Ordner an festen
# Steckplaetzen /mpd-slots/uN/<Unterordner>. Hier entsteht daraus die Sicht, die MPD erwartet.
#
# Die Links liegen unter DEMSELBEN Pfad wie auf dem Host (MPD_HOMES_ROOT, z. B.
# /volume1/homes/<konto>). Damit bleibt ein in users.json gespeicherter personal_path nach
# Umstieg und Wiederherstellung gueltig, ohne dass jemand die Datei anfassen muss.
# /mpd-homes/<konto> entsteht zusaetzlich als Alias — Installationen aus dem September 2026
# haben diesen Pfad gespeichert. An der Sicherheit aendert das nichts: eingehaengt sind
# weiterhin nur die einzelnen Foto-Ordner, nie der Home-Bereich als Ganzes.
HOMES_ROOT="${MPD_HOMES_ROOT:-/volume1/homes}"
mkdir -p "$HOMES_ROOT" /mpd-homes
# eigene Links von frueheren Starts entfernen (nur solche, die in die Steckplaetze zeigen)
for d in "$HOMES_ROOT"/* /mpd-homes/*; do
  [ -L "$d" ] || continue
  case "$(readlink "$d")" in /mpd-slots/*) rm -f "$d" ;; esac
done
if [ -f "$CONF/homes-map" ]; then
  n=0
  while IFS=: read -r name slot uid gid; do
    [ -n "$name" ] && [ -n "$slot" ] || continue
    [ -d "/mpd-slots/$slot" ] || continue
    ln -sfn "/mpd-slots/$slot" "$HOMES_ROOT/$name" && n=$((n+1))
    ln -sfn "/mpd-slots/$slot" "/mpd-homes/$name"
    # Frisch angelegter Ordner gehoert root — dem Konto uebergeben, damit es seine Fotos
    # auch ueber Netzlaufwerk oder Datei-Station ablegen kann. Nur wenn leer und root
    # gehoerend, damit nie an bestehenden Bestaenden gedreht wird.
    d="/mpd-slots/$slot/${MPD_PERSONAL_SUBDIR:-Photos}"
    if [ -n "$uid" ] && [ -d "$d" ] && [ -z "$(ls -A "$d" 2>/dev/null)" ] && [ "$(stat -c %u "$d" 2>/dev/null)" = "0" ]; then
      chown "$uid:${gid:-$uid}" "$d" 2>/dev/null && chmod 0755 "$d" 2>/dev/null &&
        echo "   Ordner $name/${MPD_PERSONAL_SUBDIR:-Photos} an uid $uid uebergeben"
    fi
  done < "$CONF/homes-map"
  echo "🏠 persoenliche Bereiche: $n (unter $HOMES_ROOT, Alias /mpd-homes)"
else
  echo "🏠 keine homes-map — keine persoenlichen Bereiche eingehaengt"
fi

# ── 4. Proxy-Vertrauen: Bridge-Gateway aus der Routing-Tabelle ──
if [ -z "${FORWARDED_ALLOW_IPS:-}" ]; then
  gw="$(awk '$2 == "00000000" { printf "%d.%d.%d.%d\n", strtonum("0x" substr($3,7,2)), strtonum("0x" substr($3,5,2)), strtonum("0x" substr($3,3,2)), strtonum("0x" substr($3,1,2)) }' /proc/net/route 2>/dev/null | head -1)"
  [ -n "$gw" ] && export FORWARDED_ALLOW_IPS="$gw" && echo "🔒 Proxy-Header vertraut: $gw"
fi

exec /app/entrypoint.sh "$@"
