import time

import pytest

import bookings
import security
from config import settings
from conftest import TOMORROW


# ── QR tokens ─────────────────────────────────────────────────────────────────
def test_qr_value_round_trips_and_check_in_works():
    result = bookings.book(TOMORROW, "19:00", 2, "Asha", "9876543210")
    booking = bookings.lookup_by_qr(result.qr_value)
    assert booking["name"] == "Asha" and booking["table"] == "T1"
    assert bookings.check_in(booking["id"])
    assert not bookings.check_in(booking["id"])  # only once


def test_tampered_or_forged_qr_rejected():
    result = bookings.book(TOMORROW, "19:00", 2, "Asha", "9876543210")
    assert bookings.lookup_by_qr(result.qr_value[:-2] + "xx") is None
    assert bookings.lookup_by_qr(security.sign_booking(result.booking_id, "guessed-token")) is None
    assert bookings.lookup_by_qr("1") is None


# ── Restore ───────────────────────────────────────────────────────────────────
def test_restore_blocked_when_table_was_rebooked():
    first = bookings.book(TOMORROW, "19:00", 6, "Asha", "9876543210")
    assert bookings.cancel(first.booking_id, notify=False)
    assert bookings.book(TOMORROW, "19:30", 6, "Ravi", "9876543211").ok
    assert bookings.restore(first.booking_id) == "clash"


def test_restore_ok_when_table_free():
    first = bookings.book(TOMORROW, "19:00", 6, "Asha", "9876543210")
    bookings.cancel(first.booking_id, notify=False)
    assert bookings.restore(first.booking_id) == "ok"


# ── Notifications are reported honestly ──────────────────────────────────────
def test_whatsapp_result_reported(fake_whatsapp):
    assert bookings.book(TOMORROW, "19:00", 2, "Asha", "9876543210").whatsapp_sent
    fake_whatsapp.succeed = False
    assert not bookings.book(TOMORROW, "19:00", 2, "Ravi", "9876543211").whatsapp_sent


# ── OTP ───────────────────────────────────────────────────────────────────────
def test_otp_correct_code():
    code = security.create_otp("9876543210", "cancel")
    assert security.verify_otp("9876543210", "cancel", code) == "ok"
    assert security.verify_otp("9876543210", "cancel", code) == "expired"  # single use


def test_otp_wrong_then_locked():
    code = security.create_otp("9876543210", "cancel")
    wrong = "000000" if code != "000000" else "111111"
    results = [security.verify_otp("9876543210", "cancel", wrong)
               for _ in range(settings.otp_max_attempts)]
    assert results[0] == "wrong" and results[-1] == "locked"
    assert security.verify_otp("9876543210", "cancel", code) == "expired"


def test_otp_expires(monkeypatch):
    code = security.create_otp("9876543210", "cancel")
    real_time = time.time
    monkeypatch.setattr(security.time, "time", lambda: real_time() + 3600)
    assert security.verify_otp("9876543210", "cancel", code) == "expired"


def test_otp_send_rate_limit():
    for _ in range(settings.otp_max_per_hour):
        security.create_otp("9876543210", "cancel")
    with pytest.raises(security.OtpRateLimited):
        security.create_otp("9876543210", "cancel")


# ── Login lockout (stored in the database, survives restarts) ────────────────
def test_login_lockout():
    for _ in range(settings.login_max_fails):
        assert not security.is_locked("ip:1.2.3.4")
        security.record_login_failure("ip:1.2.3.4")
    assert security.is_locked("ip:1.2.3.4")
    security.clear_login_failures("ip:1.2.3.4")
    assert not security.is_locked("ip:1.2.3.4")
