"""
manage.py — admin account management from the terminal.

  python manage.py create-admin [--username NAME] [--role owner|staff]
  python manage.py set-password --username NAME
  python manage.py enable-2fa --username NAME
  python manage.py disable-2fa --username NAME
  python manage.py list-admins
  python manage.py build-index
"""

import argparse
import getpass
import sqlite3
import sys

import admins
from database import init_db


def _ask_password() -> str:
    first = getpass.getpass(f"Password (min {admins.MIN_PASSWORD_LENGTH} characters): ")
    if first != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    return first


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Vaani admin tools")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin")
    create.add_argument("--username")
    create.add_argument("--role", choices=admins.ROLES, default="staff")

    for name in ("set-password", "enable-2fa", "disable-2fa"):
        sub.add_parser(name).add_argument("--username", required=True)

    sub.add_parser("list-admins")
    sub.add_parser("build-index")

    args = parser.parse_args(argv)
    init_db()

    if args.command == "create-admin":
        username = args.username or input("Username: ")
        try:
            admins.create_admin(username, _ask_password(), args.role)
        except (ValueError, sqlite3.IntegrityError) as exc:
            sys.exit(f"Could not create admin: {exc}")
        print(f"Created {args.role} account '{username.strip().lower()}'.")

    elif args.command == "set-password":
        try:
            ok = admins.set_password(args.username, _ask_password())
        except ValueError as exc:
            sys.exit(str(exc))
        print("Password updated." if ok else "No such admin.")

    elif args.command == "enable-2fa":
        uri = admins.enable_totp(args.username)
        if not uri:
            sys.exit("No such admin.")
        print("Add this account to Google Authenticator, Authy or similar.")
        print("Scan the QR below, or enter the secret from this link manually:\n")
        print(uri + "\n")
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(uri)
            qr.print_ascii(invert=True)
        except Exception:
            pass

    elif args.command == "disable-2fa":
        print("Two-factor disabled." if admins.disable_totp(args.username) else "No such admin.")

    elif args.command == "list-admins":
        rows = admins.list_admins()
        if not rows:
            print("No admins yet. Run: python manage.py create-admin --role owner")
        for username, role, has_2fa, created in rows:
            print(f"{username:20} {role:6} 2FA={'on' if has_2fa else 'off':3}  created {created}")

    elif args.command == "build-index":
        from rag_store import build_index
        build_index(force=True)


if __name__ == "__main__":
    main()
