#!/usr/bin/env python3
"""
scripts/share_admin.py — CLI helper for the share registry (Phase 4 will replace this).

Run from inside the container:
    python3 scripts/share_admin.py create -s shared -a ALBUM -o OWNER
    python3 scripts/share_admin.py revoke TOKEN
    python3 scripts/share_admin.py list-owner OWNER
    python3 scripts/share_admin.py list-album -s shared -a ALBUM

Short flags so the docker-exec invocation stays on a single line.
"""
import argparse
import sys
from pathlib import Path

# Adjust sys.path so 'core' stays importable regardless of caller cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import shares


def _fmt(info) -> str:
    return f"{info.token_hash[:12]}… space={info.space} album={info.album_name} owner={info.owner} created={info.created_at:.0f}"


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="Neuen Share-Token erzeugen")
    c.add_argument("-s", "--space", required=True, choices=["shared", "personal"])
    c.add_argument("-a", "--album", required=True)
    c.add_argument("-o", "--owner", required=True)
    c.add_argument("-t", "--theme", default=None)

    r = sub.add_parser("revoke", help="Token widerrufen")
    r.add_argument("token")

    lo = sub.add_parser("list-owner", help="Alle Shares eines Owners zeigen")
    lo.add_argument("owner")

    la = sub.add_parser("list-album", help="Alle Shares eines Albums zeigen")
    la.add_argument("-s", "--space", required=True, choices=["shared", "personal"])
    la.add_argument("-a", "--album", required=True)

    args = p.parse_args()

    if args.cmd == "create":
        token = shares.create(space=args.space, album_name=args.album,
                              owner=args.owner, theme=args.theme)
        print(token)
    elif args.cmd == "revoke":
        ok = shares.revoke(args.token)
        print("revoked" if ok else "not-found")
    elif args.cmd == "list-owner":
        for info in shares.list_for_owner(args.owner):
            print(_fmt(info))
    elif args.cmd == "list-album":
        for info in shares.list_for_album(args.space, args.album):
            print(_fmt(info))


if __name__ == "__main__":
    main()
