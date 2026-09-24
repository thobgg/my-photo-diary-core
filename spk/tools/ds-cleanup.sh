#!/bin/sh
# Räumt eine gescheiterte MPD-Paketinstallation auf einer DSM-7-NAS vollständig auf.
# Als root ausführen (sudo -i). Fasst ausschließlich Dinge mit dem Namen MPD an — plus DSMs
# Paket-Existenzcache, den DSM danach aus /var/packages neu aufbaut.
# Hintergrund: docs/specs/SPK.md §6.4 („repair"-Schleife).
set -u
PKG=MPD
echo "1) synopkg uninstall (darf scheitern)"; synopkg uninstall "$PKG" 2>/dev/null
echo "2) Paketpfade"
rm -rf "/var/packages/$PKG" "/volume1/@appstore/$PKG" "/volume1/@appdata/$PKG" "/volume1/@appconf/$PKG" \
       "/volume1/@apphome/$PKG" "/volume1/@apptemp/$PKG" "/volume1/@appshare/$PKG" "/volume1/docker/$PKG" \
       "/usr/syno/etc/packages/$PKG" "/usr/syno/synoman/webman/3rdparty/$PKG" /var/tmp/resource."$PKG".* \
       "/run/synopkg/lock/$PKG.lock"
echo "3) Paketnutzer/-gruppe"
synouser --get "$PKG" >/dev/null 2>&1 && synouser --del "$PKG"
synogroup --get "$PKG" >/dev/null 2>&1 && synogroup --del "$PKG"
echo "4) Docker-Reste"
docker network rm "$PKG" >/dev/null 2>&1; docker rm -f syno-mpd >/dev/null 2>&1
echo "5) DSM-Paketcache (wird neu aufgebaut)"
rm -f /var/cache/synopkg/installed/*
echo "6) DSM-Webdienst neu starten"
synosystemctl restart synoscgi
echo "--- Kontrolle:"
ls -d "/var/packages/$PKG" /volume1/@app*/"$PKG" "/volume1/docker/$PKG" "/usr/syno/synoman/webman/3rdparty/$PKG" 2>&1 | grep -v "No such" || echo "sauber"
grep -c "\"$PKG\"" /var/cache/synopkg/installed/existence 2>/dev/null || echo "Existenzcache: leer (wird neu erzeugt)"
