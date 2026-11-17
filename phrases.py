"""phrases.py — sentences Vaani speaks, shared by the rule flow, the agent and the tools."""

import availability as av
from config import settings

BOOKING_Q = {
    "date":   "What date would you like to book the table for?",
    "time":   "What time works best for you?",
    "people": "How many guests will be joining?",
    "name":   "What name should I put the reservation under?",
    "phone":  "Could I get your 10-digit mobile number for the booking?",
}
CANCEL_Q = {
    "phone": "What is the 10-digit mobile number registered for the booking?",
    "date":  "On which date was the reservation?",
    "time":  "At what time was the reservation?",
}
PAST_DATE_REPLY = "That date has already passed. Could you choose a future date for the booking?"


def unsure_reply() -> str:
    return ("I want to be accurate, and I am not fully sure from our records. "
            f"Please call us at {settings.restaurant_phone}.")


def large_group_reply() -> str:
    return (f"For groups of more than {settings.large_group_threshold} guests, we use our private "
            "dining room with at least 7 days advance notice. "
            f"Please call us at {settings.restaurant_phone} or email {settings.private_dining_email}.")


def too_large_reply() -> str:
    return (f"Our largest table seats {av.largest_table()}. "
            f"For bigger groups please call us at {settings.restaurant_phone}.")


def alternatives_phrase(times: list) -> str:
    if not times:
        return "We have no free tables that day. Would you like to try another date?"
    spoken = [av.speak_time(t) for t in times]
    joined = spoken[0] if len(spoken) == 1 else ", ".join(spoken[:-1]) + " or " + spoken[-1]
    return f"I can offer {joined}. Which would you prefer?"


def read_back(date: str, time: str, people: int, name: str, phone: str) -> str:
    return (
        f"Just to confirm: a table for {people} on {av.speak_date(date)} "
        f"at {av.speak_time(time)}, under the name {name}, "
        f"mobile ending {' '.join(phone[-4:])}. Shall I book it?"
    )


def booking_confirmed(date: str, time: str, people: int, name: str, whatsapp_sent: bool) -> str:
    reply = (f"Your table for {people} on {av.speak_date(date)} at "
             f"{av.speak_time(time)} is confirmed under {name}.")
    if whatsapp_sent:
        return reply + " I have sent the confirmation and QR code to your WhatsApp."
    return reply + (" I could not send the WhatsApp confirmation right now, but your booking is saved. "
                    "Just give your name at the entrance.")
