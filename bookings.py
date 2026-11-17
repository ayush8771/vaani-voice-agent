"""
bookings.py — booking operations with the business rules enforced in code.

book() checks the rules, picks a free table and inserts inside one
BEGIN IMMEDIATE transaction, so two callers can never take the same table.
Notifications run after the commit and report whether they really went out.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import availability as av
from config import settings
from database import connection, set_booking_status, transaction
from security import new_booking_token, sign_booking, unsign_booking
from services import whatsapp

logger = logging.getLogger(__name__)


@dataclass
class BookingResult:
    ok: bool
    reason: str = ""                  # rule reason, or "full" when no table is free
    message: str = ""
    booking_id: Optional[int] = None
    table: str = ""
    qr_value: str = ""
    whatsapp_sent: bool = False
    alternatives: list = field(default_factory=list)


def qr_url(qr_value: str) -> Optional[str]:
    if not settings.public_base_url:
        return None
    return f"{settings.public_base_url}/qr/{qr_value}.png"


def checkin_url(qr_value: str) -> str:
    return f"{settings.public_base_url}/admin/checkin/{qr_value}"


def book(date: str, time: str, people: int, name: str, phone: str,
         notify: bool = True, notes: str = "") -> BookingResult:
    rule = av.check_slot_rules(date, time, people)
    if not rule.ok:
        return BookingResult(False, rule.reason, rule.message)
    if people > av.largest_table():
        return BookingResult(False, "too_large")

    token = new_booking_token()
    with transaction() as con:
        table = av.find_free_table(con, date, time, people)
        if table is None:
            alternatives = av.suggest_alternatives(con, date, time, people)
            return BookingResult(False, "full", alternatives=alternatives)
        table_id, label, _ = table
        cur = con.execute(
            "INSERT INTO bookings(date, time, end_time, people, name, phone, status, "
            "table_id, token, created_at, notes) VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?)",
            (date, time, av.end_time(time), people, name, phone, table_id, token,
             av.now().isoformat(timespec="seconds"), (notes or "")[:300] or None),
        )
        booking_id = cur.lastrowid

    qr_value = sign_booking(booking_id, token)
    sent = False
    if notify:
        sent = whatsapp.send_booking_confirmation(
            name=name, phone=phone, date=date, time=time, people=people,
            table=label, qr_url=qr_url(qr_value),
        )
    logger.info("Booking #%d confirmed on table %s (whatsapp_sent=%s)", booking_id, label, sent)
    return BookingResult(True, booking_id=booking_id, table=label,
                         qr_value=qr_value, whatsapp_sent=sent)


def find_active_booking(phone: str, date: str, time: str):
    with connection() as con:
        return con.execute(
            "SELECT id, name FROM bookings WHERE phone=? AND date=? AND time=? AND status='confirmed'",
            (phone, date, time),
        ).fetchone()


def cancel(booking_id: int, notify: bool = True) -> bool:
    with connection() as con:
        row = con.execute(
            "SELECT name, phone, date, time FROM bookings WHERE id=? AND status='confirmed'",
            (booking_id,),
        ).fetchone()
        if not row:
            return False
        con.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
    if notify:
        name, phone, date, time = row
        whatsapp.send_cancellation(name=name, phone=phone, date=date, time=time)
    return True


def restore(booking_id: int) -> str:
    """Re-confirm a cancelled booking only if its table is still free. Returns ok|clash|missing."""
    with transaction() as con:
        row = con.execute(
            "SELECT table_id, date, time, end_time FROM bookings WHERE id=? AND status='cancelled'",
            (booking_id,),
        ).fetchone()
        if not row:
            return "missing"
        table_id, date, start, finish = row
        if table_id is not None:
            clash = con.execute(
                "SELECT 1 FROM bookings WHERE table_id=? AND date=? AND status='confirmed' "
                "AND time < ? AND end_time > ? AND id != ? LIMIT 1",
                (table_id, date, finish, start, booking_id),
            ).fetchone()
            if clash:
                return "clash"
        con.execute("UPDATE bookings SET status='confirmed' WHERE id=?", (booking_id,))
    return "ok"


def admin_cancel(booking_id: int) -> bool:
    return set_booking_status(booking_id, "cancelled")


def lookup_by_qr(qr_value: str):
    """Return booking details for a valid signed QR value, else None."""
    parsed = unsign_booking(qr_value)
    if not parsed:
        return None
    booking_id, token = parsed
    with connection() as con:
        row = con.execute(
            "SELECT b.id, b.date, b.time, b.people, b.name, b.status, b.checked_in_at, "
            "COALESCE(t.label, '-') FROM bookings b "
            "LEFT JOIN restaurant_tables t ON t.id = b.table_id WHERE b.id=? AND b.token=?",
            (booking_id, token),
        ).fetchone()
    if not row:
        return None
    keys = ("id", "date", "time", "people", "name", "status", "checked_in_at", "table")
    return dict(zip(keys, row))


def check_in(booking_id: int) -> bool:
    with connection() as con:
        cur = con.execute(
            "UPDATE bookings SET checked_in_at=? WHERE id=? AND status='confirmed' "
            "AND checked_in_at IS NULL",
            (av.now().isoformat(timespec="seconds"), booking_id),
        )
        return cur.rowcount == 1
