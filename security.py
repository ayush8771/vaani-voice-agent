"""
security.py — signed booking tokens, one-time codes, and login lockout.

Everything here is stored in SQLite, so limits survive restarts and are shared
by every worker process.
"""

import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional

from itsdangerous import BadSignature, URLSafeSerializer

from config import settings
from database import connection

logger = logging.getLogger(__name__)


def _secret() -> bytes:
    key = settings.flask_secret_key
    if not key:
        raise RuntimeError("FLASK_SECRET_KEY is not set")
    return key.encode()


# ── BOOKING TOKENS (QR codes) ─────────────────────────────────────────────────
def new_booking_token() -> str:
    """Random 128-bit token stored with the booking."""
    return secrets.token_urlsafe(16)


def sign_booking(booking_id: int, token: str) -> str:
    """Signed value placed in the QR code. Forging it needs FLASK_SECRET_KEY."""
    return URLSafeSerializer(_secret(), salt="vaani-booking").dumps({"b": booking_id, "t": token})


def unsign_booking(signed: str) -> Optional[tuple]:
    try:
        data = URLSafeSerializer(_secret(), salt="vaani-booking").loads(signed)
        return int(data["b"]), str(data["t"])
    except (BadSignature, KeyError, TypeError, ValueError):
        return None


# ── ONE-TIME CODES ────────────────────────────────────────────────────────────
def _hash_code(phone: str, code: str) -> str:
    return hmac.new(_secret(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


class OtpRateLimited(Exception):
    pass


def create_otp(phone: str, purpose: str) -> str:
    """Create a 6-digit code. Raises OtpRateLimited after OTP_MAX_PER_HOUR sends."""
    now = time.time()
    with connection() as con:
        sent = con.execute(
            "SELECT COUNT(*) FROM otps WHERE phone=? AND purpose=? AND created_at > ?",
            (phone, purpose, now - 3600),
        ).fetchone()[0]
        if sent >= settings.otp_max_per_hour:
            raise OtpRateLimited(phone)

        code = f"{secrets.randbelow(1_000_000):06d}"
        con.execute("UPDATE otps SET used=1 WHERE phone=? AND purpose=? AND used=0", (phone, purpose))
        con.execute(
            "INSERT INTO otps(phone, purpose, code_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (phone, purpose, _hash_code(phone, code), now, now + settings.otp_ttl_minutes * 60),
        )
    return code


def verify_otp(phone: str, purpose: str, code: str) -> str:
    """Returns 'ok', 'wrong', 'expired' or 'locked'."""
    now = time.time()
    with connection() as con:
        row = con.execute(
            "SELECT id, code_hash, expires_at, attempts FROM otps "
            "WHERE phone=? AND purpose=? AND used=0 ORDER BY id DESC LIMIT 1",
            (phone, purpose),
        ).fetchone()
        if not row:
            return "expired"
        otp_id, code_hash, expires_at, attempts = row
        if expires_at < now:
            con.execute("UPDATE otps SET used=1 WHERE id=?", (otp_id,))
            return "expired"
        if attempts >= settings.otp_max_attempts:
            con.execute("UPDATE otps SET used=1 WHERE id=?", (otp_id,))
            return "locked"
        if hmac.compare_digest(code_hash, _hash_code(phone, code)):
            con.execute("UPDATE otps SET used=1 WHERE id=?", (otp_id,))
            return "ok"
        if attempts + 1 >= settings.otp_max_attempts:
            # Locked codes are burned, so the right code cannot be guessed afterwards.
            con.execute("UPDATE otps SET attempts=attempts+1, used=1 WHERE id=?", (otp_id,))
            return "locked"
        con.execute("UPDATE otps SET attempts=attempts+1 WHERE id=?", (otp_id,))
        return "wrong"


# ── LOGIN LOCKOUT ─────────────────────────────────────────────────────────────
def is_locked(key: str) -> bool:
    with connection() as con:
        row = con.execute("SELECT locked_until FROM login_attempts WHERE key=?", (key,)).fetchone()
    return bool(row and row[0] > time.time())


def record_login_failure(key: str) -> None:
    with connection() as con:
        con.execute(
            "INSERT INTO login_attempts(key, fails) VALUES (?, 1) "
            "ON CONFLICT(key) DO UPDATE SET fails = fails + 1",
            (key,),
        )
        fails = con.execute("SELECT fails FROM login_attempts WHERE key=?", (key,)).fetchone()[0]
        if fails >= settings.login_max_fails:
            con.execute(
                "UPDATE login_attempts SET fails=0, locked_until=? WHERE key=?",
                (time.time() + settings.login_lockout_seconds, key),
            )
            logger.warning("Admin login locked for %s", key)


def clear_login_failures(key: str) -> None:
    with connection() as con:
        con.execute("DELETE FROM login_attempts WHERE key=?", (key,))
