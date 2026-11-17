"""
ops.py — the ops agent: what Vaani does on its own, on a schedule.

  * reminders   WhatsApp the guest once, 2-24 hours before the booking
  * waitlist    when a table frees up for a waiting party, tell them at once
  * feedback    ask checked-in guests how it went, about an hour after the meal
  * tidy-up     expire waitlist entries whose time has passed

Every job is idempotent (each booking is reminded once, each waitlist entry is
notified once), so it is safe to run from several places. Run it with:

    python manage.py run-ops            # once (e.g. from cron / Task Scheduler)
    python manage.py run-ops --loop 300 # every 5 minutes

or set OPS_IN_APP=true to run it inside the web app.
Every action is logged to the agent activity page with path "ops".
"""

import logging
import threading
import time
from datetime import datetime, timedelta

import availability as av
from config import settings
from database import connection
from services import whatsapp
from vaani_mcp import tools

logger = logging.getLogger(__name__)


def _slot(date: str, hhmm: str) -> datetime:
    return datetime.combine(av.parse_date(date), datetime.min.time()).replace(
        tzinfo=settings.tz) + timedelta(minutes=av.to_minutes(hhmm))


def _log(action: str, args: dict, ok: bool, message: str) -> None:
    tools.log_call("ops", action, args, {"ok": ok, "message": message}, 0)


# ── JOBS ──────────────────────────────────────────────────────────────────────
def send_reminders(now: datetime) -> int:
    sent = 0
    with connection() as con:
        rows = con.execute(
            "SELECT id, date, time, people, name, phone FROM bookings WHERE status='confirmed' "
            "AND reminder_sent_at IS NULL AND date BETWEEN ? AND ?",
            (now.date().isoformat(), (now + timedelta(days=1)).date().isoformat()),
        ).fetchall()
    for booking_id, date, hhmm, people, name, phone in rows:
        until = _slot(date, hhmm) - now
        if not timedelta(hours=2) <= until <= timedelta(hours=24):
            continue
        body = (f"Hi {name}, a reminder of your table for {people} at {settings.restaurant_name} on "
                f"{av.speak_date(date)} at {av.speak_time(hhmm)}. Need to change it? "
                f"Call us at {settings.restaurant_phone}.")
        ok = whatsapp.send_text(phone, body)
        if ok:
            with connection() as con:
                con.execute("UPDATE bookings SET reminder_sent_at=? WHERE id=?",
                            (now.isoformat(timespec="seconds"), booking_id))
            sent += 1
        _log("send_reminder", {"booking": booking_id, "phone": phone}, ok, body)
    return sent


def request_feedback(now: datetime) -> int:
    sent = 0
    with connection() as con:
        rows = con.execute(
            "SELECT id, date, end_time, name, phone FROM bookings WHERE status='confirmed' "
            "AND checked_in_at IS NOT NULL AND feedback_sent_at IS NULL AND end_time IS NOT NULL "
            "AND date BETWEEN ? AND ?",
            ((now - timedelta(days=1)).date().isoformat(), now.date().isoformat()),
        ).fetchall()
    for booking_id, date, end, name, phone in rows:
        if now - _slot(date, end) < timedelta(hours=1):
            continue
        body = (f"Thank you for dining with {settings.restaurant_name}, {name}! How was everything? "
                "Reply with a rating from 1 to 5 and any comments.")
        ok = whatsapp.send_text(phone, body)
        if ok:
            with connection() as con:
                con.execute("UPDATE bookings SET feedback_sent_at=? WHERE id=?",
                            (now.isoformat(timespec="seconds"), booking_id))
            sent += 1
        _log("request_feedback", {"booking": booking_id, "phone": phone}, ok, body)
    return sent


def notify_waitlist(now: datetime, date: str = None) -> int:
    """Tell waiting parties as soon as a table is free for their slot."""
    notified = 0
    query = "SELECT id, date, time, people, name, phone FROM waitlist WHERE status='waiting'"
    params = ()
    if date:
        query, params = query + " AND date=?", (date,)
    with connection() as con:
        rows = con.execute(query + " ORDER BY created_at", params).fetchall()
    for entry_id, d, hhmm, people, name, phone in rows:
        if _slot(d, hhmm) <= now:
            continue
        with connection() as con:
            free = av.find_free_table(con, d, hhmm, people)
        if not free:
            continue
        body = (f"Good news, {name}! A table for {people} at {settings.restaurant_name} on {av.speak_date(d)} "
                f"at {av.speak_time(hhmm)} just became free. Call us at {settings.restaurant_phone} "
                "or talk to Vaani to book it before someone else does.")
        ok = whatsapp.send_text(phone, body)
        with connection() as con:
            con.execute("UPDATE waitlist SET status='notified', notified_at=? WHERE id=?",
                        (now.isoformat(timespec="seconds"), entry_id))
        notified += 1
        _log("notify_waitlist", {"waitlist": entry_id, "phone": phone}, ok, body)
    return notified


def expire_waitlist(now: datetime) -> int:
    with connection() as con:
        rows = con.execute("SELECT id, date, time FROM waitlist WHERE status='waiting'").fetchall()
        expired = [entry_id for entry_id, d, hhmm in rows if _slot(d, hhmm) <= now]
        for entry_id in expired:
            con.execute("UPDATE waitlist SET status='expired' WHERE id=?", (entry_id,))
    return len(expired)


def run_once(now: datetime = None) -> dict:
    now = now or av.now()
    result = {
        "reminders": send_reminders(now),
        "waitlist_notified": notify_waitlist(now),
        "feedback_requests": request_feedback(now),
        "waitlist_expired": expire_waitlist(now),
    }
    logger.info("Ops run: %s", result)
    return result


def start_background(interval_seconds: int = 300) -> None:
    """Run the ops jobs inside the web app (OPS_IN_APP=true)."""
    def loop():
        while True:
            try:
                run_once()
            except Exception:
                logger.exception("Ops run failed")
            time.sleep(interval_seconds)
    threading.Thread(target=loop, name="vaani-ops", daemon=True).start()
