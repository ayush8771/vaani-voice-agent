import pyotp
import pytest

import admins
import bookings
from conftest import TOMORROW
from database import connection


@pytest.fixture
def client():
    from app import create_app
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
    return app.test_client()


def login(client, username, password, otp=""):
    return client.post("/admin/login", data={"username": username, "password": password, "otp": otp})


def test_health(client):
    assert client.get("/health").get_json()["status"] == "ok"


def test_send_message_validates_input(client):
    assert client.post("/send_message", json={}).status_code == 400
    assert client.post("/send_message", json={"message": "x" * 501}).status_code == 400
    assert "VAANI" in client.post("/send_message", json={"message": "__WELCOME__"}).get_json()["response"]


def test_admin_requires_login(client):
    response = client.get("/admin")
    assert response.status_code == 302 and "/admin/login" in response.location


def test_login_page_explains_first_setup(client):
    assert b"manage.py create-admin" in client.get("/admin/login").data


def test_password_login_and_logout(client):
    admins.create_admin("owner1", "correct-horse-battery", "owner")
    assert login(client, "owner1", "wrong-password").status_code == 401
    assert login(client, "owner1", "correct-horse-battery").status_code == 302
    assert client.get("/admin").status_code == 200
    client.post("/admin/logout")
    assert client.get("/admin").status_code == 302


def test_two_factor_required_when_enabled(client):
    admins.create_admin("owner1", "correct-horse-battery", "owner")
    uri = admins.enable_totp("owner1")
    secret = pyotp.parse_uri(uri).secret
    assert login(client, "owner1", "correct-horse-battery").status_code == 401
    assert login(client, "owner1", "correct-horse-battery", pyotp.TOTP(secret).now()).status_code == 302


def test_staff_cannot_export(client):
    admins.create_admin("staff1", "correct-horse-battery", "staff")
    login(client, "staff1", "correct-horse-battery")
    assert client.get("/admin/export").status_code == 403


def test_dashboard_renders_with_bookings(client):
    """Regression: the dashboard crashed (500) as soon as one booking existed."""
    bookings.book(TOMORROW, "19:00", 2, "Asha", "9876543210")
    cancelled = bookings.book(TOMORROW, "20:30", 2, "Ravi", "9876543211")
    bookings.cancel(cancelled.booking_id, notify=False)
    admins.create_admin("owner1", "correct-horse-battery", "owner")
    login(client, "owner1", "correct-horse-battery")
    page = client.get("/admin")
    assert page.status_code == 200 and b"Asha" in page.data and b"Restore" in page.data


def test_owner_export_is_audited(client):
    admins.create_admin("owner1", "correct-horse-battery", "owner")
    login(client, "owner1", "correct-horse-battery")
    assert client.get("/admin/export").status_code == 200
    with connection() as con:
        actions = [r[0] for r in con.execute("SELECT action FROM audit_log")]
    assert "login" in actions and "export" in actions


def test_lockout_after_repeated_failures(client):
    admins.create_admin("owner1", "correct-horse-battery", "owner")
    for _ in range(5):
        login(client, "owner1", "wrong-password")
    assert login(client, "owner1", "correct-horse-battery").status_code == 429


def test_qr_image_and_checkin(client):
    result = bookings.book(TOMORROW, "19:00", 2, "Asha", "9876543210")
    assert client.get(f"/qr/{result.qr_value}.png").mimetype == "image/png"
    assert client.get("/qr/forged-value.png").status_code == 404

    assert client.get(f"/admin/checkin/{result.qr_value}").status_code == 302  # login needed
    admins.create_admin("staff1", "correct-horse-battery", "staff")
    login(client, "staff1", "correct-horse-battery")
    page = client.post(f"/admin/checkin/{result.qr_value}")
    assert b"Guest checked in" in page.data


def test_open_redirect_blocked(client):
    admins.create_admin("owner1", "correct-horse-battery", "owner")
    response = client.post("/admin/login?next=//evil.example.com",
                           data={"username": "owner1", "password": "correct-horse-battery"})
    assert response.location.endswith("/admin")
