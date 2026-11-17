"""Phase 4: the ops agent (reminders, waitlist offers, feedback requests, expiry)."""

from datetime import timedelta

import bookings
import ops
from conftest import FIXED_NOW, TOMORROW
from database import connection
from vaani_mcp import tools


def rows(sql, *args):
    with connection() as con:
        return con.execute(sql, args).fetchall()


def test_reminder_sent_once_inside_the_window(fake_whatsapp):
    bookings.book(TOMORROW, "20:00", 4, "Ravi", "9876543210", notify=False)   # 34 hours away
    assert ops.send_reminders(FIXED_NOW) == 0                                 # too early
    later = FIXED_NOW + timedelta(hours=12)                                   # 22 hours before
    assert ops.send_reminders(later) == 1
    assert ops.send_reminders(later) == 0                                     # only once
    assert "reminder of your table for 4" in fake_whatsapp.sent[-1]["body"]


def test_reminder_retried_if_whatsapp_fails(fake_whatsapp):
    bookings.book(TOMORROW, "20:00", 4, "Ravi", "9876543210", notify=False)
    fake_whatsapp.succeed = False
    assert ops.send_reminders(FIXED_NOW + timedelta(hours=12)) == 0
    fake_whatsapp.succeed = True
    assert ops.send_reminders(FIXED_NOW + timedelta(hours=13)) == 1


def test_cancellation_offers_table_to_waitlist(fake_whatsapp):
    taken = bookings.book(TOMORROW, "20:00", 6, "Asha", "9123456780", notify=False)   # only 6-seater
    tools.join_waitlist(TOMORROW, "20:00", 6, "Ravi", "9876543210")
    assert ops.notify_waitlist(FIXED_NOW) == 0                                # still full
    bookings.cancel(taken.booking_id, notify=False)                           # table frees up
    assert rows("SELECT status FROM waitlist") == [("notified",)]
    assert "just became free" in fake_whatsapp.sent[-1]["body"]
    assert ("notify_waitlist", "ops") in rows("SELECT tool, path FROM tool_calls")


def test_admin_cancel_also_offers_table(fake_whatsapp):
    taken = bookings.book(TOMORROW, "20:00", 6, "Asha", "9123456780", notify=False)
    tools.join_waitlist(TOMORROW, "20:00", 6, "Ravi", "9876543210")
    bookings.admin_cancel(taken.booking_id)
    assert rows("SELECT status FROM waitlist") == [("notified",)]


def test_feedback_after_meal_for_checked_in_guests(fake_whatsapp):
    result = bookings.book(TOMORROW, "13:00", 2, "Meera", "9988776655", notify=False)
    bookings.check_in(result.booking_id)
    during_meal = FIXED_NOW + timedelta(days=1, hours=4)                      # 14:00 tomorrow
    assert ops.request_feedback(during_meal) == 0
    after = FIXED_NOW + timedelta(days=1, hours=6)                            # 16:00, ends 14:30
    assert ops.request_feedback(after) == 1 and ops.request_feedback(after) == 0
    assert "How was everything?" in fake_whatsapp.sent[-1]["body"]


def test_no_feedback_for_guests_who_never_came(fake_whatsapp):
    bookings.book(TOMORROW, "13:00", 2, "Meera", "9988776655", notify=False)
    assert ops.request_feedback(FIXED_NOW + timedelta(days=1, hours=6)) == 0


def test_expired_waitlist_and_run_once(fake_whatsapp):
    tools.join_waitlist(TOMORROW, "13:00", 2, "Ravi", "9876543210")
    # nothing is full, so the entry is offered the table straight away
    assert ops.run_once(FIXED_NOW)["waitlist_notified"] == 1
    tools.join_waitlist(TOMORROW, "13:00", 2, "Asha", "9123456780")
    result = ops.run_once(FIXED_NOW + timedelta(days=2))
    assert result["waitlist_expired"] == 1
