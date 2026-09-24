#!/bin/sh
# Install-Wizard von My Photo Diary — generiert das Wizard-JSON nach $SYNOPKG_TEMP_LOGFILE.
# Sprache wird von build.sh eingesetzt: __WLANG__ = enu | ger
WLANG="__WLANG__"
WMODE="__WMODE__"
WIZENV="/var/packages/MPD/var/wizard.env"
wiz_get() { [ -f "${WIZENV}" ] && sed -n "s/^$1=//p" "${WIZENV}" | head -1; }
d_share="$(wiz_get wizard_photo_share)"; [ -n "${d_share}" ] || d_share="photo"
d_subdir="$(wiz_get wizard_photo_subdir)"
d_homes="$(wiz_get wizard_homes_root)"; [ -n "${d_homes}" ] || d_homes="/volume1/homes"
d_psub="$(wiz_get wizard_personal_subdir)"; [ -n "${d_psub}" ] || d_psub="Photos"
d_port="$(wiz_get wizard_port)"; [ -n "${d_port}" ] || d_port="8089"
d_portal="$(wiz_get wizard_portal_https)"; [ -n "${d_portal}" ] || d_portal="8443"
d_base="$(wiz_get wizard_base_url)"
PASSWD_FILE="${PASSWD_FILE:-/etc/passwd}"

# Identisch zu scripts/common:local_users — Reihenfolge ist der Vertrag für wizard_role_u<N>_*.
local_users() {
    awk -F: '$3 >= 1026 && $3 < 65536 && $6 ~ /^\/var\/services\/homes\// { print $1 }' "${PASSWD_FILE}" | sort
}
user_desc() {
    /usr/syno/sbin/synouser --get "$1" 2>/dev/null | sed -n 's/^User Desc[[:space:]]*:[[:space:]]*\[\(.*\)\]$/\1/p' | head -1
}
jq_esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

if [ "${WLANG}" = "ger" ]; then
T_P1="Fotos"
T_P1_INTRO="Wo liegen die Fotos? MPD verändert nichts an den Bildern selbst; je Album entsteht eine <b>album.json</b> im Ordner.<br><br>Es gibt zwei Bereiche: den <b>gemeinsamen Bestand</b>, den alle sehen (oben), und je Person einen <b>privaten Bereich</b> in ihrem Home-Ordner (unten). Die unteren beiden Felder kannst du so lassen, wenn deine NAS normal eingerichtet ist."
T_SHARE="Freigabe mit den gemeinsamen Fotos"
T_SHARE_HINT="Nur der Name der Freigabe, wie er in der Systemsteuerung steht (z.&nbsp;B. <code>photo</code>), kein Pfad."
T_SHARE_ERR="Nur der Name der Freigabe, kein Pfad"
T_SUBDIR="Unterordner, falls nur ein Teil gemeint ist"
T_HOMES="Wo die Home-Ordner liegen"
T_HOMES_ERR="Muss mit /volume… beginnen"
T_PSUB="Privater Bereich heißt dort"
T_PSUB_ERR="Nur ein Ordnername, kein Pfad"
T_P2="Erreichbarkeit"
T_P2_INTRO="Im Heimnetz ist MPD unter <b>http://&lt;NAS&gt;:&lt;LAN-Port&gt;</b> erreichbar. Für den Zugriff von unterwegs trägst du später eine Regel unter Systemsteuerung → Anmeldeportal → Reverse Proxy ein; sie verschlüsselt mit dem Zertifikat deiner NAS."
T_PORT="LAN-Port"
T_PORTAL="Portal-Port (HTTPS)"
T_PORT_ERR="Port zwischen 1024 und 65535"
T_BASE="Öffentliche Adresse (optional)"
T_BASE_HINT="Die öffentliche Adresse (z.&nbsp;B. <code>https://fotos.example.org</code>) brauchen nur Share-Links; leer = aus dem Aufruf abgeleitet."
T_BASE_ERR="Muss mit http:// oder https:// beginnen"
T_P3="Benutzer"
T_P3_INTRO="MPD hat eine <b>eigene Anmeldung</b>. Die DSM-Konten unten sind nur die Vorlage; das DSM-Passwort gilt hier nicht. Mindestens ein Konto braucht <b>Admin</b>.<br>Jedes Konto sieht die gemeinsamen Alben und führt seine eigenen. Dazu: <b>Admin</b> gestaltet auch die gemeinsamen Alben und verwaltet Konten · <b>Editor</b> darf dort hochladen und einsortieren · <b>Viewer</b> sieht sie nur<br><br><b>Umstieg oder Wiederherstellung?</b> Wer Daten einer früheren Installation mitbringt, stellt hier alles auf <i>nicht anlegen</i> und lässt die Passwortfelder leer — die Konten stehen schon in den Daten."
T_NONE="nicht anlegen"
T_ADMIN="Admin"
T_EDITOR="Editor"
T_VIEWER="Viewer"
T_PW_ADMIN="Startpasswort Admin"
T_PW_OTHER="Startpasswort Editor/Viewer"
T_PW_HINT="Mindestens 12 Zeichen, nur nötig für Konten, die hier entstehen.<br><b>Empfehlung:</b> zweites Feld leer lassen — dann vergibt der Admin jedem Konto in der App ein eigenes, statt eines gemeinsamen für alle."
T_PW_ERR="Mindestens 12 Zeichen"
T_PW_NOTE="<b>Persönlicher Bereich:</b> Ein Konto bekommt ihn, sobald in seinem Home-Ordner der oben gewählte Ordner (z.&nbsp;B. <code>Photos</code>) existiert. Fehlt er, legt ihn das Konto selbst in der Datei-Station an; danach das Paket einmal stoppen und starten.<br><br>Bitte den Nutzern das Startpasswort mitteilen.<br><br><b>Die Installation dauert einige Minuten</b>, der Fortschrittsbalken bewegt sich dabei kaum: DSM baut das Container-Image. <b>Nach der Installation</b> erzeugt MPD Vorschaubilder für alle Fotos. Bei großen Beständen darf die NAS dafür eine Weile arbeiten (einige tausend Fotos: bis zu einer Stunde). Die App ist währenddessen schon benutzbar."
T_UPGRADE_NOTE="Konten, Einstellungen und Betriebsdaten bleiben erhalten. Der Container sichert sie vor dem ersten Start der neuen Version unter <code>docker/MPD/data/backup</code>. Das Image wird neu gebaut (Internetverbindung nötig)."
T_NOUSERS="Keine lokalen DSM-Benutzer mit Home-Ordner gefunden. Bitte zuerst in der Systemsteuerung einen Benutzer anlegen und den Benutzer-Home-Dienst aktivieren."
else
T_P1="Photos"
T_P1_INTRO="Where are the photos? MPD never alters the images; each album gets an <b>album.json</b> in its folder.<br><br>There are two areas: the <b>shared collection</b> everyone sees (top) and a <b>private area</b> per person inside their home folder (bottom). Leave the lower two fields as they are if your NAS is set up normally."
T_SHARE="Shared folder with the common photos"
T_SHARE_HINT="Just the name of the shared folder as shown in Control Panel (e.g. <code>photo</code>), not a path."
T_SHARE_ERR="Just the share name, no path"
T_SUBDIR="Subfolder, if only part of it is meant"
T_HOMES="Where the home folders live"
T_HOMES_ERR="Must start with /volume…"
T_PSUB="Private area is called"
T_PSUB_ERR="A folder name only, no path"
T_P2="Access"
T_P2_INTRO="On your LAN, MPD is reachable at <b>http://&lt;NAS&gt;:&lt;LAN port&gt;</b>. For access from outside, add a rule later under Control Panel → Login Portal → Reverse Proxy; it encrypts with your NAS certificate."
T_PORT="LAN port"
T_PORTAL="Portal port (HTTPS)"
T_PORT_ERR="Port between 1024 and 65535"
T_BASE="Public address (optional)"
T_BASE_HINT="Only share links need the public address (e.g. <code>https://photos.example.org</code>); empty = derived from the request."
T_BASE_ERR="Must start with http:// or https://"
T_P3="Users"
T_P3_INTRO="MPD has its <b>own login</b>. The DSM accounts below are only the template; the DSM password does not apply here. At least one account needs <b>Admin</b>.<br>Every account sees the shared albums and keeps its own. On top: <b>Admin</b> also designs the shared albums and manages accounts · <b>Editor</b> may upload and file photos there · <b>Viewer</b> only sees them<br><br><b>Migrating or restoring?</b> If you bring data from an earlier installation, set everything to <i>do not create</i> and leave the password fields empty — the accounts are already in that data."
T_NONE="do not create"
T_ADMIN="Admin"
T_EDITOR="Editor"
T_VIEWER="Viewer"
T_PW_ADMIN="Starting password Admin"
T_PW_OTHER="Starting password Editor/Viewer"
T_PW_HINT="At least 12 characters, only needed for accounts created here.<br><b>Recommendation:</b> leave the second field empty — the admin then sets an individual password per account in the app, instead of one shared by all."
T_PW_ERR="At least 12 characters"
T_PW_NOTE="<b>Personal space:</b> An account gets one as soon as the folder chosen above (e.g. <code>Photos</code>) exists in its home directory. If it is missing, the account creates it in File Station; then stop and start the package once.<br><br>Please tell your users the starting password.<br><br><b>Installation takes a few minutes</b> and the progress bar barely moves meanwhile: DSM builds the container image. <b>After installation</b> MPD generates preview images for all photos. With large libraries the NAS may be busy for a while (several thousand photos: up to an hour). The app is usable in the meantime."
T_UPGRADE_NOTE="Accounts, settings and operating data are kept. The container backs them up under <code>docker/MPD/data/backup</code> before the new version starts. The image is rebuilt (internet connection required)."
T_NOUSERS="No local DSM users with a home folder found. Create a user in Control Panel first and enable the user home service."
fi

installer="${SYNOPKG_USERNAME}"
UJ="${SYNOPKG_TEMP_LOGFILE}.users"; : > "${UJ}"
i=0
local_users | while read -r u; do
    i=$((i+1))
    k="$(printf '%s' "${u}" | sed 's/[^A-Za-z0-9]/_/g')"
    desc="$(user_desc "${u}")"
    label="$(jq_esc "${u}")"
    [ -n "${desc}" ] && label="${label} ($(jq_esc "${desc}"))"
    d_none=true; d_admin=false; d_editor=false; d_viewer=false
    if [ "${u}" = "${installer}" ]; then
        d_none=false; d_admin=true
    elif [ -d "/volume1/homes/${u}/Photos" ]; then
        d_none=false; d_viewer=true
    fi
    item="$(cat <<EOT
{"type":"singleselect","desc":"${label}","subitems":[
 {"key":"wizard_role_${k}_none","desc":"${T_NONE}","defaultValue":${d_none}},
 {"key":"wizard_role_${k}_admin","desc":"${T_ADMIN}","defaultValue":${d_admin}},
 {"key":"wizard_role_${k}_editor","desc":"${T_EDITOR}","defaultValue":${d_editor}},
 {"key":"wizard_role_${k}_viewer","desc":"${T_VIEWER}","defaultValue":${d_viewer}}]}
EOT
)"
    [ -s "${UJ}" ] && printf ',' >> "${UJ}"
    printf '%s' "${item}" >> "${UJ}"
done
users_json="$(cat "${UJ}")"; rm -f "${UJ}"
if [ -z "${users_json}" ]; then
    users_json="{\"desc\":\"${T_NOUSERS}\"}"
fi

if [ "${WMODE}" = "upgrade" ]; then
page3="$(cat <<EOT
 {"step_title":"${T_P3}","items":[{"desc":"${T_UPGRADE_NOTE}"}]}
EOT
)"
else
page3="$(cat <<EOT
 {"step_title":"${T_P3}","invalid_next_disabled":true,"items":[
  {"desc":"${T_P3_INTRO}"},
  ${users_json},
  {"desc":"${T_PW_HINT}"},
  {"type":"password","subitems":[{"key":"wizard_pw_admin","desc":"${T_PW_ADMIN}",
    "validator":{"allowBlank":true,"minLength":12,"minLengthText":"${T_PW_ERR}"}}]},
  {"type":"password","subitems":[{"key":"wizard_pw_other","desc":"${T_PW_OTHER}",
    "validator":{"allowBlank":true,"minLength":12,"minLengthText":"${T_PW_ERR}"}}]},
  {"desc":"${T_PW_NOTE}"}
 ]}
EOT
)"
fi
RX_PORT="/^(102[4-9]|10[3-9][0-9]|1[1-9][0-9]{2}|[2-9][0-9]{3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])$/"

cat > "${SYNOPKG_TEMP_LOGFILE}" <<EOT
[
 {"step_title":"${T_P1}","invalid_next_disabled":true,"items":[
  {"desc":"${T_P1_INTRO}"},
  {"desc":"${T_SHARE_HINT}"},
  {"type":"textfield","subitems":[{"key":"wizard_photo_share","desc":"${T_SHARE}","defaultValue":"${d_share}",
    "validator":{"allowBlank":false,"regex":{"expr":"/^[^\\\\/:*?\"<>|]+$/","errorText":"${T_SHARE_ERR}"}}}]},
  {"type":"textfield","subitems":[{"key":"wizard_photo_subdir","desc":"${T_SUBDIR}","defaultValue":"${d_subdir}",
    "validator":{"allowBlank":true,"regex":{"expr":"/^[^\\\\/].*$/","errorText":"${T_PSUB_ERR}"}}}]},
  {"type":"textfield","subitems":[{"key":"wizard_homes_root","desc":"${T_HOMES}","defaultValue":"${d_homes}",
    "validator":{"allowBlank":false,"regex":{"expr":"/^\\\\/volume[0-9]+\\\\/.+/","errorText":"${T_HOMES_ERR}"}}}]},
  {"type":"textfield","subitems":[{"key":"wizard_personal_subdir","desc":"${T_PSUB}","defaultValue":"${d_psub}",
    "validator":{"allowBlank":false,"regex":{"expr":"/^[^\\\\/]+$/","errorText":"${T_PSUB_ERR}"}}}]}
 ]},
 {"step_title":"${T_P2}","invalid_next_disabled":true,"items":[
  {"desc":"${T_P2_INTRO}"},
  {"desc":"${T_BASE_HINT}"},
  {"type":"textfield","subitems":[{"key":"wizard_port","desc":"${T_PORT}","defaultValue":"${d_port}",
    "validator":{"allowBlank":false,"regex":{"expr":"${RX_PORT}","errorText":"${T_PORT_ERR}"}}}]},
  {"type":"textfield","subitems":[{"key":"wizard_base_url","desc":"${T_BASE}","defaultValue":"${d_base}",
    "validator":{"allowBlank":true,"regex":{"expr":"/^https?:\\\\/\\\\/.+/","errorText":"${T_BASE_ERR}"}}}]}
 ]},
 ${page3}
]
EOT
exit 0
