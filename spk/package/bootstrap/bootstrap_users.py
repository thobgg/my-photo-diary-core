#!/usr/bin/env python3
"""
bootstrap_users.py — legt beim Installieren des Synology-Pakets die MPD-Konten an.

Läuft EINMAL im Container (docker-compose run …), weil DSM kein Argon2 hat.
Eingabe: JSON-Datei {"users":[{"name","role","display_name","personal_path","password"}]}
Regel:   nur Konten anlegen, die in users.json noch fehlen — eine Neuinstallation
         über bestehende Daten überschreibt niemandes Passwort.
Die Eingabedatei löscht das Paketskript danach; dieses Skript liest sie nur.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app/backend")
from core.userdb import hash_password, load_raw, save_raw, ALLOWED_ROLES, SCHEMA_VERSION  # noqa: E402


def main(path: str) -> int:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    data = load_raw()
    users = data.setdefault("users", {})
    data.setdefault("version", SCHEMA_VERSION)
    created, skipped, failed = [], [], []

    for u in spec.get("users", []):
        name = (u.get("name") or "").strip()
        role = (u.get("role") or "viewer").lower()
        if not name or role not in ALLOWED_ROLES:
            failed.append(name or "?")
            continue
        if name in users:
            skipped.append(name)
            continue
        entry = {
            "pw_hash": hash_password(u["password"]) if u.get("password") else "",
            "role": role,
            "personal_path": u.get("personal_path") or "",
            "is_demo": False,
            # Erstanmelde-Zwang (seit 3.0): Initialpasswort aus dem Assistenten
            # muss beim ersten Login ersetzt werden.
            "must_change_password": True,
        }
        if u.get("display_name"):
            entry["display_name"] = u["display_name"]
        users[name] = entry
        created.append(name)

    if created:
        save_raw(data)

    print("bootstrap: created=%s skipped(existing)=%s failed=%s"
          % (",".join(created) or "-", ",".join(skipped) or "-", ",".join(failed) or "-"))
    admins = [n for n, e in users.items() if e.get("role") == "admin" and e.get("pw_hash")]
    if not admins:
        print("bootstrap: ERROR no admin with password in users.json", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: bootstrap_users.py <spec.json>", file=sys.stderr)
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
