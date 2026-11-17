"""
admins.py — admin accounts with hashed passwords, roles and optional TOTP 2FA.

Roles:
  owner  full access, including CSV export and the audit log
  staff  view bookings, cancel/restore, check guests in
"""

from dataclasses import dataclass
from typing import Optional

import pyotp
from werkzeug.security import check_password_hash, generate_password_hash

from config import settings
from database import connection

ROLES = ("owner", "staff")
MIN_PASSWORD_LENGTH = 10

# Used when the username does not exist, so a failed lookup costs the same
# time as a wrong password and usernames cannot be probed by timing.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


@dataclass
class Admin:
    id: int
    username: str
    role: str
    totp_enabled: bool


def count_admins() -> int:
    with connection() as con:
        return con.execute("SELECT COUNT(*) FROM admins").fetchone()[0]


def create_admin(username: str, password: str, role: str = "staff") -> None:
    username = username.strip().lower()
    if not username:
        raise ValueError("Username is required.")
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    with connection() as con:
        con.execute(
            "INSERT INTO admins(username, password_hash, role) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), role),
        )


def set_password(username: str, password: str) -> bool:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    with connection() as con:
        cur = con.execute("UPDATE admins SET password_hash=? WHERE username=?",
                          (generate_password_hash(password), username.strip().lower()))
        return cur.rowcount == 1


def enable_totp(username: str) -> Optional[str]:
    """Create a TOTP secret and return the otpauth:// URI for an authenticator app."""
    secret = pyotp.random_base32()
    with connection() as con:
        cur = con.execute("UPDATE admins SET totp_secret=? WHERE username=?",
                          (secret, username.strip().lower()))
        if cur.rowcount != 1:
            return None
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=f"Vaani ({settings.restaurant_name})")


def disable_totp(username: str) -> bool:
    with connection() as con:
        cur = con.execute("UPDATE admins SET totp_secret=NULL WHERE username=?",
                          (username.strip().lower(),))
        return cur.rowcount == 1


def list_admins() -> list:
    with connection() as con:
        return con.execute(
            "SELECT username, role, totp_secret IS NOT NULL, created_at FROM admins ORDER BY username"
        ).fetchall()


def authenticate(username: str, password: str, totp_code: str = "") -> Optional[Admin]:
    """Return the admin if password (and TOTP code, when enabled) are correct."""
    with connection() as con:
        row = con.execute(
            "SELECT id, username, password_hash, role, totp_secret FROM admins WHERE username=?",
            ((username or "").strip().lower(),),
        ).fetchone()

    if not row:
        check_password_hash(_DUMMY_HASH, password or "")
        return None

    admin_id, name, password_hash, role, totp_secret = row
    if not check_password_hash(password_hash, password or ""):
        return None
    if totp_secret and not pyotp.TOTP(totp_secret).verify((totp_code or "").strip(), valid_window=1):
        return None
    return Admin(admin_id, name, role, bool(totp_secret))
