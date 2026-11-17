"""
services/whatsapp.py — Twilio WhatsApp messages.

Every send function returns True only when Twilio accepted the message to the
customer, so callers can tell the guest the truth.
"""

import logging
import re
from functools import lru_cache
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client():
    from twilio.rest import Client
    return Client(settings.twilio_sid, settings.twilio_auth_token)


def _digits(phone: str) -> Optional[str]:
    digits = re.sub(r"\D", "", phone or "")[-10:]
    return digits if len(digits) == 10 else None


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return f"******{digits[-4:]}" if len(digits) >= 4 else "****"


def _send(to: str, body: str, media_url: Optional[str] = None) -> bool:
    if not settings.whatsapp_configured:
        logger.warning("WhatsApp not configured; message to %s skipped", mask_phone(to))
        return False
    try:
        kwargs = {"from_": f"whatsapp:{settings.twilio_whatsapp_number}", "to": to, "body": body}
        if media_url:
            kwargs["media_url"] = [media_url]
        _client().messages.create(**kwargs)
        return True
    except Exception as exc:
        logger.error("Twilio send to %s failed: %s", mask_phone(to), exc)
        return False


def _customer(phone: str) -> Optional[str]:
    digits = _digits(phone)
    return f"whatsapp:+91{digits}" if digits else None


def _notify_owner(body: str) -> None:
    if settings.owner_whatsapp:
        _send(f"whatsapp:{settings.owner_whatsapp}", body)


def send_booking_confirmation(*, name: str, phone: str, date: str, time: str,
                              people: int, table: str, qr_url: Optional[str]) -> bool:
    to = _customer(phone)
    if not to:
        return False
    body = (
        f"*Booking Confirmed* - {settings.restaurant_name}\n\n"
        f"Name: {name}\nDate: {date}\nTime: {time}\nGuests: {people}\n\n"
        "Please show the QR code at the entrance."
    )
    # Twilio fetches media from a public URL; without PUBLIC_BASE_URL we send text only.
    sent = _send(to, body, media_url=qr_url)
    _notify_owner(f"*New booking*\n{name}, {people} guests, {date} {time}, table {table}, "
                  f"phone {mask_phone(phone)}")
    return sent


def send_cancellation(*, name: str, phone: str, date: str, time: str) -> bool:
    to = _customer(phone)
    if not to:
        return False
    sent = _send(to, f"*Booking Cancelled* - {settings.restaurant_name}\n\n"
                     f"Name: {name}\nDate: {date}\nTime: {time}")
    _notify_owner(f"*Booking cancelled*\n{name}, {date} {time}, phone {mask_phone(phone)}")
    return sent


def send_otp(phone: str, code: str) -> bool:
    to = _customer(phone)
    if not to:
        return False
    return _send(to, f"Your {settings.restaurant_name} verification code is {code}. "
                     f"It expires in {settings.otp_ttl_minutes} minutes. Do not share it.")
