"""Conversation tests. The fake LLM returns the slot JSON registered per message."""

import brain
from conftest import TOMORROW
from database import connection


def say(msg, state):
    reply, _audio, state = brain.get_ai_response(msg, state)
    return reply, state


def booking_count():
    with connection() as con:
        return con.execute("SELECT COUNT(*) FROM bookings WHERE status='confirmed'").fetchone()[0]


FULL = {"date": TOMORROW, "time": "20:00", "people": 4, "name": "Ravi", "phone": "9876543210"}


def test_read_back_before_booking(fake_llm):
    fake_llm.slots["Book a table for 4 tomorrow at 8 pm, Ravi, 9876543210"] = FULL
    reply, state = say("Book a table for 4 tomorrow at 8 pm, Ravi, 9876543210", {})
    assert "Just to confirm" in reply and "Thursday 19 November" in reply and "8 PM" in reply
    assert booking_count() == 0

    reply, state = say("yes", state)
    assert "is confirmed" in reply and "WhatsApp" in reply
    assert booking_count() == 1 and state == {}


def test_correction_during_confirmation(fake_llm):
    fake_llm.slots["book a table"] = {}
    fake_llm.slots["details"] = FULL
    fake_llm.slots["No, make it 9 pm instead please"] = {"time": "21:00"}
    _, state = say("book a table", {})
    _, state = say("details", state)
    reply, state = say("No, make it 9 pm instead please", state)
    assert "9 PM" in reply and "Just to confirm" in reply
    say("yes", state)
    with connection() as con:
        assert con.execute("SELECT time FROM bookings").fetchone()[0] == "21:00"


def test_small_talk_does_not_wipe_booking(fake_llm):
    fake_llm.slots["I want to book a table"] = {}
    fake_llm.slots["Thanks, my name is Ravi"] = {"name": "Ravi"}
    _, state = say("I want to book a table", {})
    reply, state = say("Thanks, my name is Ravi", state)
    assert state.get("flow") == "booking" and state["slots"]["name"] == "Ravi"
    assert reply == brain.BOOKING_Q["date"]


def test_side_question_keeps_booking(fake_llm):
    fake_llm.slots["book a table"] = {}
    _, state = say("book a table", {})
    reply, state = say("Do you have parking?", state)
    assert state.get("flow") == "booking"
    assert "what date would you like" in reply


def test_large_group_goes_to_private_dining(fake_llm):
    fake_llm.slots["Book a table for 25 people"] = {"people": 25}
    reply, state = say("Book a table for 25 people", {})
    assert "private dining" in reply and state == {}


def test_closed_day_and_hours_explained(fake_llm):
    fake_llm.slots["book"] = {**FULL, "date": "2026-11-23"}
    reply, state = say("book", {})
    assert "closed" in reply and state["slots"]["date"] is None

    fake_llm.slots["book it"] = {**FULL, "time": "16:00"}
    reply, state = say("book it", {})
    assert "last seating" in reply and state["slots"]["time"] is None


def test_honest_message_when_whatsapp_fails(fake_llm, fake_whatsapp):
    fake_whatsapp.succeed = False
    fake_llm.slots["book now"] = FULL
    _, state = say("book now", {})
    reply, _ = say("yes", state)
    assert "is confirmed" in reply and "could not send" in reply


def test_stop_booking_with_cancel_that(fake_llm):
    fake_llm.slots["book a table"] = {}
    _, state = say("book a table", {})
    reply, state = say("cancel that", state)
    assert "stopped this booking" in reply and state == {}


def test_cancel_requires_whatsapp_code(fake_llm, fake_whatsapp):
    fake_llm.slots["book now"] = FULL
    _, state = say("book now", {})
    say("yes", state)

    fake_llm.slots["I want to cancel my booking"] = {}
    fake_llm.slots["details"] = {"phone": "9876543210", "date": TOMORROW, "time": "20:00"}
    _, state = say("I want to cancel my booking", {})
    reply, state = say("details", state)
    assert "6-digit code" in reply and booking_count() == 1

    reply, state = say("123", state)
    assert "6-digit code" in reply

    code = fake_whatsapp.last_code()
    wrong = "000000" if code != "000000" else "111111"
    reply, state = say(wrong, state)
    assert "does not match" in reply and booking_count() == 1

    reply, state = say(" ".join(code), state)
    assert "cancelled successfully" in reply and booking_count() == 0


def test_cancel_refused_when_code_cannot_be_sent(fake_llm, fake_whatsapp):
    fake_llm.slots["book now"] = FULL
    _, state = say("book now", {})
    say("yes", state)

    fake_whatsapp.succeed = False
    fake_llm.slots["cancel my booking"] = {"phone": "9876543210", "date": TOMORROW, "time": "20:00"}
    reply, state = say("cancel my booking", {})
    assert "cannot verify" in reply and booking_count() == 1


def test_phone_extraction_ignores_dates_and_times():
    assert brain._phone_from_text("on 2026-11-20 at 20:00") is None
    assert brain._phone_from_text("my number is 98765 43210") == "9876543210"
    assert brain._phone_from_text("+91 98765-43210") == "9876543210"
    assert brain._phone_from_text("1234567890") is None  # not a mobile number
