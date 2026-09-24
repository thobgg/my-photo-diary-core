#!/usr/bin/env python3
"""
backend/scripts/manage_users.py
CLI for managing users.json (Argon2id hashes).

Run inside the container:
  docker compose exec mpd python scripts/manage_users.py <command>

Commands:
  list                           List users (without hashes)
  add <username>                 Create a new user (interactive)
  add-demo <username>            Create a demo user (is_demo=true, viewer,
                                 short passwords allowed — demo is public)
  set-password <username>        Change password (interactive)
  set-role <username> <role>     Change role (admin|editor|viewer)
  set-path <username> <path>     Change personal_path
  set-display-name <user> <name> Set display name (e.g. "Anna"); falls
                                 back to login name when unset
  set-must-change <user> <on|off> Force password change on next login
                                 (cleared automatically once the user
                                 sets a new password)
  set-modules <user> <spec>      Unlock paid modules. spec = comma list
                                 (e.g. tours,stats), or "all" / "none" /
                                 "clear" (clear = remove field = Grandfather,
                                 all modules free). Known: tours,trails,
                                 search,stats.
  delete <username>              Delete user

Passwords are ALWAYS read via getpass, NEVER passed as a CLI argument.
"""

import getpass
import sys
from pathlib import Path

# Import path for /app/backend inside the container
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.userdb import (
    hash_password, load_raw, save_raw,
    ALLOWED_ROLES, SCHEMA_VERSION,
)
from config import USERS_JSON_PATH, default_personal_path

from core.module_keys import MODULE_KEYS


def _read_password(prompt: str = "New password: ", min_len: int = 12) -> str:
    """Prompt twice for a password and verify both entries match. Default minimum length 12."""
    while True:
        pw = getpass.getpass(prompt)
        if len(pw) < min_len:
            print("  x Password too short (min. %d chars). Try again." % min_len, file=sys.stderr)
            continue
        pw2 = getpass.getpass("Confirm password: ")
        if pw != pw2:
            print("  x Passwords do not match. Try again.", file=sys.stderr)
            continue
        return pw


def cmd_list():
    data = load_raw()
    users = data.get("users") or {}
    if not users:
        print("(no users in %s)" % USERS_JSON_PATH)
        return
    print("%-16s  %-14s  %-7s  %-5s  %s" %
          ("USER", "DISPLAY", "ROLE", "DEMO", "PERSONAL_PATH"))
    print("-" * 90)
    for name, entry in sorted(users.items()):
        role     = entry.get("role", "?")
        demo     = "yes" if entry.get("is_demo") else ""
        path     = entry.get("personal_path", "")
        display  = entry.get("display_name") or "-"
        hash_ok  = "ok" if entry.get("pw_hash") else "no PW"
        mods     = entry.get("modules")
        mods_str = "all" if mods is None else (",".join(sorted(mods)) if mods else "core-only")
        print("%-16s  %-14s  %-7s  %-5s  %s  [%s]  mod=%s" %
              (name, display, role, demo, path, hash_ok, mods_str))


def cmd_add(username: str):
    data = load_raw()
    users = data.setdefault("users", {})
    if username in users:
        print("x User %r already exists. Use 'set-password' or 'set-role'." % username, file=sys.stderr)
        sys.exit(1)

    role = input("Role [admin|editor|viewer]: ").strip().lower()
    if role not in ALLOWED_ROLES:
        print("x Invalid role.", file=sys.stderr)
        sys.exit(1)

    default_path = default_personal_path(username)
    path = input("personal_path [%s]: " % default_path).strip() or default_path

    pw = _read_password()
    pw_hash = hash_password(pw)
    del pw

    users[username] = {
        "pw_hash": pw_hash,
        "role": role,
        "personal_path": path,
        "is_demo": False,
    }
    data.setdefault("version", SCHEMA_VERSION)
    save_raw(data)
    print("+ User %s created (%s, %s)" % (username, role, path))


def cmd_add_demo(username: str):
    """Create a demo user with is_demo=true and a short password (demo is public)."""
    data = load_raw()
    users = data.setdefault("users", {})
    if username in users:
        print("x User %r already exists." % username, file=sys.stderr)
        sys.exit(1)

    default_path = default_personal_path(username)
    path = input("personal_path [%s]: " % default_path).strip() or default_path

    print("(Demo password is public - minimum length 6)")
    pw = _read_password(min_len=6)
    pw_hash = hash_password(pw)
    del pw

    users[username] = {
        "pw_hash": pw_hash,
        "role": "viewer",
        "personal_path": path,
        "is_demo": True,
    }
    data.setdefault("version", SCHEMA_VERSION)
    save_raw(data)
    print("+ Demo user %s created (viewer, is_demo=true, %s)" % (username, path))


def cmd_set_password(username: str):
    data = load_raw()
    users = data.setdefault("users", {})
    if username not in users:
        print("x Unknown user %r." % username, file=sys.stderr)
        sys.exit(1)
    pw = _read_password()
    users[username]["pw_hash"] = hash_password(pw)
    del pw
    data.setdefault("version", SCHEMA_VERSION)
    save_raw(data)
    print("+ Password set for %s." % username)


def cmd_set_role(username: str, role: str):
    role = role.lower()
    if role not in ALLOWED_ROLES:
        print("x Role must be one of %s." % sorted(ALLOWED_ROLES), file=sys.stderr)
        sys.exit(1)
    data = load_raw()
    users = data.setdefault("users", {})
    if username not in users:
        print("x Unknown user %r." % username, file=sys.stderr)
        sys.exit(1)
    users[username]["role"] = role
    data.setdefault("version", SCHEMA_VERSION)
    save_raw(data)
    print("+ Role for %s: %s" % (username, role))


def cmd_set_path(username: str, path: str):
    data = load_raw()
    users = data.setdefault("users", {})
    if username not in users:
        print("x Unknown user %r." % username, file=sys.stderr)
        sys.exit(1)
    users[username]["personal_path"] = path
    data.setdefault("version", SCHEMA_VERSION)
    save_raw(data)
    print("+ personal_path for %s: %s" % (username, path))


def cmd_set_display_name(username: str, display_name: str):
    """Set the display name (e.g. first name). An empty value clears the field."""
    data = load_raw()
    users = data.setdefault("users", {})
    if username not in users:
        print("x Unknown user %r." % username, file=sys.stderr)
        sys.exit(1)
    display_name = display_name.strip()
    if display_name:
        users[username]["display_name"] = display_name
    else:
        users[username].pop("display_name", None)
    data.setdefault("version", SCHEMA_VERSION)
    save_raw(data)
    shown = display_name or "(cleared - falls back to login name)"
    print("+ display_name for %s: %s" % (username, shown))


def cmd_set_must_change(username: str, value: str):
    """Force (or unforce) a password change on next login."""
    value = value.strip().lower()
    if value not in ("on", "off", "true", "false", "1", "0"):
        print("x Value must be on|off.", file=sys.stderr)
        sys.exit(1)
    flag = value in ("on", "true", "1")
    data = load_raw()
    users = data.setdefault("users", {})
    if username not in users:
        print("x Unknown user %r." % username, file=sys.stderr)
        sys.exit(1)
    if flag:
        users[username]["must_change_password"] = True
    else:
        users[username].pop("must_change_password", None)
    data.setdefault("version", SCHEMA_VERSION)
    save_raw(data)
    print("+ must_change_password for %s: %s" % (username, "on" if flag else "off"))


def cmd_set_modules(username: str, spec: str):
    """Set the user's unlocked add-on modules.

    spec: comma list (tours,stats) | "all" | "none"/"core" | "clear".
    "clear" removes the field entirely → Grandfather (all modules free).
    "none" sets an empty list → only the base software.
    """
    data = load_raw()
    users = data.setdefault("users", {})
    if username not in users:
        print("x Unknown user %r." % username, file=sys.stderr)
        sys.exit(1)

    spec = spec.strip().lower()
    if spec in ("clear", "grandfather", "-"):
        users[username].pop("modules", None)
        data.setdefault("version", SCHEMA_VERSION)
        save_raw(data)
        print("+ modules cleared for %s (Grandfather: all modules free)" % username)
        return

    if spec in ("all", "*"):
        mods = sorted(MODULE_KEYS)
    elif spec in ("none", "core"):
        mods = []
    else:
        req = [m.strip() for m in spec.split(",") if m.strip()]
        unknown = [m for m in req if m not in MODULE_KEYS]
        if unknown:
            print("x Unknown module(s): %s. Known: %s" %
                  (", ".join(unknown), ", ".join(sorted(MODULE_KEYS))), file=sys.stderr)
            sys.exit(1)
        mods = sorted(set(req))

    users[username]["modules"] = mods
    data.setdefault("version", SCHEMA_VERSION)
    save_raw(data)
    print("+ modules for %s: %s" % (username, mods if mods else "[] (base only)"))


def cmd_delete(username: str):
    data = load_raw()
    users = data.setdefault("users", {})
    if username not in users:
        print("x Unknown user %r." % username, file=sys.stderr)
        sys.exit(1)
    confirm = input("Really delete '%s'? [yes/NO]: " % username).strip()
    if confirm != "yes":
        print("Aborted.")
        return
    del users[username]
    save_raw(data)
    print("+ User %s deleted." % username)


USAGE = __doc__


def _hinweis_neuanmeldung(username: str) -> None:
    """Die Sitzung friert personal_path, Rolle und Module beim Anmelden ein.

    Wer eines davon aendert, sieht in einer laufenden Sitzung NICHTS — und
    zwar ohne Fehlermeldung. Das hat am 15.09.2026 zweimal Zeit gekostet
    ("warum wieder lokal kein persoenlicher Ordner?"), beim zweiten Mal
    obwohl der Pfad nachweislich richtig stand. Darum sagt es das Werkzeug
    jetzt selbst."""
    print(f"  Hinweis: '{username}' muss sich neu anmelden, damit das wirkt — "
          "die Sitzung haelt Pfad, Rolle und Module vom Anmeldezeitpunkt fest.")


def main():
    argv = sys.argv[1:]
    if not argv:
        print(USAGE, file=sys.stderr)
        sys.exit(1)
    cmd = argv[0]
    args = argv[1:]

    if cmd == "list":
        cmd_list()
    elif cmd == "add" and len(args) == 1:
        cmd_add(args[0])
    elif cmd == "add-demo" and len(args) == 1:
        cmd_add_demo(args[0])
    elif cmd == "set-password" and len(args) == 1:
        cmd_set_password(args[0])
    elif cmd == "set-role" and len(args) == 2:
        cmd_set_role(args[0], args[1])
        _hinweis_neuanmeldung(args[0])
    elif cmd == "set-path" and len(args) == 2:
        cmd_set_path(args[0], args[1])
        _hinweis_neuanmeldung(args[0])
    elif cmd == "set-display-name" and len(args) == 2:
        cmd_set_display_name(args[0], args[1])
    elif cmd == "set-must-change" and len(args) == 2:
        cmd_set_must_change(args[0], args[1])
    elif cmd == "set-modules" and len(args) == 2:
        cmd_set_modules(args[0], args[1])
        _hinweis_neuanmeldung(args[0])
    elif cmd == "delete" and len(args) == 1:
        cmd_delete(args[0])
    else:
        print(USAGE, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
