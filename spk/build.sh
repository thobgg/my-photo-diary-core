#!/bin/bash
# Baut das Synology-Paket MPD-<version>.spk nach spk/dist/.
# Ein SPK ist ein tar aus: INFO, PACKAGE_ICON*.PNG, package.tgz, scripts/, conf/, WIZARD_UIFILES/.
# Kein spksrc, kein pkgscripts — für ein noarch-Paket ohne Kompilat reicht tar.
#
#   spk/build.sh            → Build-Nummer aus spk/BUILD
#   spk/build.sh 3          → Build-Nummer 3
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
# Version: <Produkt>.<Commit-Zahl>. backend/version.txt schreibt der post-commit-Hook und ist
# gitignored — in einer CI mit flachem Checkout fehlt sie oder ist falsch (der Server-Chat
# baute so ein "3.0.1"). Deshalb hier selbst rechnen und die Datei nur als Gegenprobe nehmen.
#
# Ausgabe (19.09.2026): Liegt .mpd-core-export im Baum, ist das der oeffentliche Kern
# (scripts/export-core.sh im privaten Repo). Dann heisst das Paket "Core", und die Version
# kommt aus jener Datei — der Kern hat keine Historie, gezaehlt wuerde dort Unsinn. Die
# Paket-ID bleibt in beiden Ausgaben "MPD": Core und Plus ersetzen einander beim Einspielen,
# statt nebeneinander um Port und docker/MPD/data zu streiten.
if [ -f "${ROOT}/.mpd-core-export" ]; then
  EDITION="core"
  APPVER="$(sed -n 's/^version=//p' "${ROOT}/.mpd-core-export")"
  [ -n "${APPVER}" ] || { echo "✗ .mpd-core-export ohne version= — Abbruch."; exit 1; }
else
  EDITION="full"
  PRODUCT="$(tr -d '[:space:]' < "${ROOT}/backend/product_version.txt" 2>/dev/null || echo 3.0)"
  COUNT="$(git -C "${ROOT}" rev-list --count HEAD 2>/dev/null || echo 0)"
  APPVER="${PRODUCT}.${COUNT}"
  if [ -r "${ROOT}/backend/version.txt" ]; then
    FILEVER="$(tr -d '[:space:]' < "${ROOT}/backend/version.txt")"
    [ "${FILEVER}" = "${APPVER}" ] || echo "  Hinweis: version.txt sagt ${FILEVER}, gerechnet ist ${APPVER} — es gilt ${APPVER}"
  fi
  [ "${COUNT}" -gt 0 ] || { echo "✗ Keine Git-Historie gefunden — Version waere ${APPVER}. Abbruch."; exit 1; }
fi
BUILD="${1:-$(tr -d '[:space:]' < "${HERE}/BUILD")}"
VERSION="${APPVER}-${BUILD}"
DIST="${HERE}/dist"
STAGE="${DIST}/stage"
PKG="${DIST}/pkg"
if [ "${EDITION}" = "core" ]; then
  OUT="${DIST}/MPD-Core-${VERSION}.spk"
  DISPLAYNAME="My Photo Diary Core"
  # Im Kern liegt kein Plus-Modul; eine Lizenzpruefung haette nichts zu
  # pruefen und wuerde nur den Abschnitt "MPD Plus" im Einstellungsdialog
  # scharf machen.
  LICENSE_ENFORCE=0
else
  OUT="${DIST}/MPD-${VERSION}.spk"
  DISPLAYNAME="My Photo Diary"
  # Das Plus-Paket prueft die Lizenz (24.09.2026). Das gilt auch fuer
  # die eigene Installation des Entwicklers — wer sein eigener erster Kunde ist,
  # merkt Fehler in der Freischaltung vor dem Kunden. Notausgang ohne
  # neues Paket: MPD_LICENSE_ENFORCE=0 in /data/mpd.env.local, dann
  # Paket stoppen und starten (spaetere Zeile gewinnt in config.py).
  LICENSE_ENFORCE=1
fi

echo "→ ${DISPLAYNAME} ${VERSION}"
rm -rf "${STAGE}" "${PKG}"
mkdir -p "${STAGE}" "${PKG}"

# 1) target/ (package.tgz): image/ = Docker-Build-Kontext, daneben Paketdateien
IMG="${STAGE}/image"; mkdir -p "${IMG}"
rsync -a --delete \
  --exclude 'tests/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '*.log' --exclude '*.log.*' --exclude '*.pid' --exclude 'mpd_search_index.json' \
  --exclude '.pytest_cache/' --exclude '.ruff_cache/' \
  "${ROOT}/backend/" "${IMG}/backend/"
rsync -a --delete "${ROOT}/frontend/" "${IMG}/frontend/"
mkdir -p "${IMG}/docs" "${STAGE}/docs" "${STAGE}/conf"
cp "${ROOT}/docs/handbook.html" "${IMG}/docs/"
cp "${HERE}/docs/installation.html" "${STAGE}/docs/"
rsync -a "${HERE}/docs/screenshots/" "${STAGE}/docs/screenshots/"
cp "${ROOT}/entrypoint.sh" "${ROOT}/requirements.txt" "${ROOT}/mpd.env.example" "${IMG}/"
# AGPL: Lizenztext gehoert mit ausgeliefert, nicht nur in den Quellen
cp "${ROOT}/LICENSE" "${IMG}/LICENSE"
cp "${ROOT}/LICENSE" "${STAGE}/LICENSE"
cp -r "${HERE}/package/bootstrap" "${IMG}/bootstrap"
cp "${HERE}/package/image/entry.sh" "${IMG}/entry.sh"
# Repo-Dockerfile unverändert + Paket-Einstieg (entry.sh erzeugt mpd.env, legt Erstkonten an)
{ cat "${ROOT}/Dockerfile"; cat <<'EOT'

# ── Synology-Paket: Einstieg vor dem regulären Entrypoint (spk/package/image/entry.sh) ──
COPY bootstrap/ /app/bootstrap/
COPY entry.sh /app/entry.sh
COPY mpd.env.example /app/mpd.env.example
RUN chmod +x /app/entry.sh
ENTRYPOINT ["/app/entry.sh"]
EOT
} > "${IMG}/Dockerfile"
rsync -a --exclude 'image/' --exclude 'bootstrap/' "${HERE}/package/" "${STAGE}/"
touch "${STAGE}/conf/.keep"
# Mount-Platzhalter: target/mounts/{photos,homes} legt Docker beim Anlegen des Containers als leere
# Verzeichnisse an; postinst ersetzt sie durch Symlinks auf die Wizard-Pfade (Worker erlaubt nur
# Pfade unter target/). Keine Symlinks im Paket selbst — der Bau läuft auf einem CIFS-Mount.
mkdir -p "${STAGE}/mounts/photos" "${STAGE}/empty"
touch "${STAGE}/empty/.keep" "${STAGE}/mounts/photos/.keep"
for i in 1 2 3 4 5 6 7 8; do mkdir -p "${STAGE}/mounts/u${i}"; touch "${STAGE}/mounts/u${i}/.keep"; done
# version.txt bleibt der Repo-Stand (<Produkt>.<Build>); die Paketnummer mit "-<n>" steht nur in INFO.

# 2) Icons aus der kachellosen Fassung der Marke (Notizbuch, Foto, Stift auf
#    transparentem Grund) — DSM-Paket-Icons tragen keine eigene Kachel, Android
#    dagegen schon (mpd-icon-*.png).
#    ACHTUNG (13.09.2026): Hier stand mpd-logo.svg. Die Datei traegt seit dem
#    Markenwechsel die Squircle-Fassung MIT Kachel, damit sie im Seitenkopf des
#    Web-Frontends eine Kante hat. Fuer DSM waere daraus ein Icon mit Rahmen
#    geworden. Deshalb die eigene Datei, abgeleitet aus src/icon_master.svg.
LOGO="${ROOT}/frontend/img/brand/mpd-logo-plain.svg"
mkdir -p "${STAGE}/ui/images"
for s in 16 24 32 48 64 72 256; do
  inkscape -w "$s" -h "$s" "${LOGO}" -o "${STAGE}/ui/images/mpd_${s}.png" >/dev/null 2>&1
done
cp "${STAGE}/ui/images/mpd_72.png"  "${PKG}/PACKAGE_ICON.PNG"
cp "${STAGE}/ui/images/mpd_256.png" "${PKG}/PACKAGE_ICON_256.PNG"

# 3) conf/ — resource enthält {{wizard_…}}-Platzhalter, die DSM beim Installieren füllt
mkdir -p "${PKG}/conf"
cp "${HERE}/conf/privilege" "${PKG}/conf/"
sed -e "s/{{PKG_VERSION}}/${VERSION}/g" -e "s/{{LICENSE_ENFORCE}}/${LICENSE_ENFORCE}/g" \
    "${HERE}/conf/resource" > "${PKG}/conf/resource"
sed -e 's/__LAN_PORT__/8089/g' -e 's/__PORTAL_HTTPS__/8443/g' "${HERE}/package/templates/ui-config.tmpl" > "${STAGE}/ui/config"
sed -e 's/__LAN_PORT__/8089/g' -e 's/__PORTAL_HTTPS__/8443/g' "${HERE}/package/port_conf/mpd.sc.tmpl" > "${STAGE}/port_conf/mpd.sc"

# 4) scripts/
mkdir -p "${PKG}/scripts"
cp "${HERE}"/scripts/* "${PKG}/scripts/"
chmod 755 "${PKG}"/scripts/*

# 5) WIZARD_UIFILES/ — enu ist der Fallback ohne Suffix
W="${PKG}/WIZARD_UIFILES"; mkdir -p "${W}"
sed -e 's/__WLANG__/enu/' -e 's/__WMODE__/install/' "${HERE}/wizard/install_uifile.tmpl.sh" > "${W}/install_uifile.sh"
sed -e 's/__WLANG__/ger/' -e 's/__WMODE__/install/' "${HERE}/wizard/install_uifile.tmpl.sh" > "${W}/install_uifile_ger.sh"
sed -e 's/__WLANG__/enu/' -e 's/__WMODE__/upgrade/' "${HERE}/wizard/install_uifile.tmpl.sh" > "${W}/upgrade_uifile.sh"
sed -e 's/__WLANG__/ger/' -e 's/__WMODE__/upgrade/' "${HERE}/wizard/install_uifile.tmpl.sh" > "${W}/upgrade_uifile_ger.sh"
cp "${HERE}/wizard/uninstall_uifile.enu" "${W}/uninstall_uifile"
cp "${HERE}/wizard/uninstall_uifile.ger" "${W}/uninstall_uifile_ger"
chmod 755 "${W}"/*.sh

# 6) package.tgz + INFO
tar -czf "${PKG}/package.tgz" -C "${STAGE}" .
EXTRACT_KB="$(du -sk "${STAGE}" | cut -f1)"
sed -e "s/__VERSION__/${VERSION}/" -e "s/__LAN_PORT__/8089/" -e "s/__EXTRACTSIZE__/${EXTRACT_KB}/" \
    -e "s/__DISPLAYNAME__/${DISPLAYNAME}/" "${HERE}/INFO.in" > "${PKG}/INFO"

# 7) SPK
rm -f "${OUT}"
tar -cf "${OUT}" -C "${PKG}" INFO PACKAGE_ICON.PNG PACKAGE_ICON_256.PNG package.tgz scripts conf WIZARD_UIFILES
echo "✓ $(du -h "${OUT}" | cut -f1)  ${OUT}"
