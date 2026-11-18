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


FULL_MSG = "Book a table for 4 tomorrow at 8 pm, name Ravi, 9876543210"
FULL = {"date": TOMORROW, "time": "20:00", "people": 4, "name": "Ravi", "phone": "9876543210"}


def test_read_back_before_booking(fake_llm):
    fake_llm.slots[FULL_MSG] = FULL
    reply, state = say(FULL_MSG, {})
    assert "Just to confirm" in reply and "Thursday 19 November" in reply and "8 PM" in reply
    assert booking_count() == 0

    reply, state = say("yes", state)
    assert "is confirmed" in reply and "WhatsApp" in reply
    assert booking_count() == 1 and "flow" not in state


def test_correction_during_confirmation(fake_llm):
    fake_llm.slots[FULL_MSG] = FULL
    fake_llm.slots["No, make it 9 pm instead please"] = {"time": "21:00"}
    _, state = say(FULL_MSG, {})
    reply, state = say("No, make it 9 pm instead please", state)
    assert "9 PM" in reply and "Just to confirm" in reply
    say("yes", state)
    with connection() as con:
        assert con.execute("SELECT time FROM bookings").fetchone()[0] == "21:00"


def test_invented_date_is_ignored_when_only_time_changes(fake_llm):
    """Transcript bug: 'make it 9 am' made the LLM set the date to today."""
    fake_llm.slots["Book a table for tomorrow for 4 at 8 pm"] = {"date": TOMORROW, "time": "20:00", "people": 4}
    fake_llm.slots["No, make it at 9 pm"] = {"date": "2026-11-18", "time": "21:00"}
    _, state = say("Book a table for tomorrow for 4 at 8 pm", {})
    _, state = say("No, make it at 9 pm", state)
    assert state["slots"]["date"] == TOMORROW and state["slots"]["time"] == "21:00"


def test_booking_works_while_llm_is_down(fake_llm):
    """Live-test bug: the LLM timed out, so 'tomorrow' and '18th November' were never read."""
    fake_llm.down = True
    reply, state = say("I wanted to book a table for 4 to for tomorrow at 8:00 PM", {})
    assert state["slots"]["date"] == TOMORROW and state["slots"]["time"] == "20:00"
    assert state["slots"]["people"] == 4 and reply == brain.BOOKING_Q["phone"]

    reply, state = say("9876543210", state)
    reply, state = say("Ravi", state)
    assert "Just to confirm" in reply
    reply, state = say("yes", state)
    assert "is confirmed" in reply


def test_simple_sentences_skip_the_llm(fake_llm):
    say("Book a table for 4 tomorrow at 8 pm, my name is Ravi, 9876543210", {})
    assert fake_llm.calls == 0


def test_hours_checked_before_asking_name(fake_llm):
    """Transcript bug: 8 AM was only rejected after name and phone were collected."""
    fake_llm.slots["Book a table for tomorrow for 4 at 8 am"] = {"date": TOMORROW, "time": "08:00", "people": 4}
    reply, state = say("Book a table for tomorrow for 4 at 8 am", {})
    assert "we are open" in reply and state["slots"]["time"] is None


def test_answer_to_question_is_accepted_without_keywords(fake_llm):
    fake_llm.slots["I want to book a table"] = {}
    fake_llm.slots["Ravi"] = {"name": "Ravi"}
    fake_llm.slots["Thursday at 8 pm for 4 people"] = {"date": TOMORROW, "time": "20:00", "people": 4}
    _, state = say("I want to book a table", {})
    _, state = say("Thursday at 8 pm for 4 people", state)
    assert state["asked"] == "phone"           # phone before name: returning callers are recognised
    reply, state = say("9876543210", state)
    assert reply == brain.BOOKING_Q["name"]
    reply, state = say("Ravi", state)
    assert state["slots"]["name"] == "Ravi" and reply.startswith("Just to confirm")


def test_small_talk_does_not_wipe_booking(fake_llm):
    fake_llm.slots["I want to book a table"] = {}
    fake_llm.slots["Thanks, my name is Ravi"] = {"name": "Ravi"}
    _, state = say("I want to book a table", {})
    reply, state = say("Thanks, my name is Ravi", state)
    assert state.get("flow") == "booking" and state["slots"]["name"] == "Ravi"
    assert reply == brain.BOOKING_Q["date"]


def test_thanks_with_new_booking_starts_booking(fake_llm):
    """Transcript bug: 'thanks ... I want to make a new booking' said goodbye."""
    msg = "Uh, thanks. My name is uh Ravi and I want to make a new booking"
    fake_llm.slots[msg] = {"name": "Ravi"}
    reply, state = say(msg, {})
    assert state.get("flow") == "booking" and "Goodbye" not in reply


def test_new_booking_mid_flow_starts_over(fake_llm):
    _, state = say("I wanted to book a table for 4 tomorrow at 8 pm", {})
    reply, state = say("Actually, my name is Ravi and I want to make a new booking", state)
    assert state["slots"] == {"name": "Ravi"} or state["slots"].get("date") is None
    assert reply == brain.BOOKING_Q["date"]


def test_plain_thanks_still_says_goodbye():
    reply, state = say("thanks", {})
    assert "Goodbye" in reply


def test_party_size_alone_starts_booking(fake_llm):
    """Transcript bug: 'tomorrow for 25 people at 3 AM' went to the FAQ answer."""
    msg = "At Tomorrow for far 25 people at 3:00 AM"
    fake_llm.slots[msg] = {"date": TOMORROW, "time": "03:00", "people": 25}
    reply, state = say(msg, {})
    assert "private dining" in reply


def test_side_question_keeps_booking(fake_llm):
    fake_llm.slots["book a table"] = {}
    _, state = say("book a table", {})
    reply, state = say("Do you have parking?", state)
    assert state.get("flow") == "booking"
    assert "what date would you like" in reply


def test_large_group_goes_to_private_dining(fake_llm):
    fake_llm.slots["Book a table for 25 people"] = {"people": 25}
    reply, state = say("Book a table for 25 people", {})
    assert "private dining" in reply and "flow" not in state


def test_closed_day_and_hours_explained(fake_llm):
    fake_llm.slots["book for Monday 23rd at 8 pm for 4"] = {"date": "2026-11-23", "time": "20:00", "people": 4}
    reply, state = say("book for Monday 23rd at 8 pm for 4", {})
    assert "closed" in reply and state["slots"]["date"] is None

    fake_llm.slots["book tomorrow at 4 pm for 4"] = {"date": TOMORROW, "time": "16:00", "people": 4}
    reply, state = say("book tomorrow at 4 pm for 4", {})
    assert "last seating" in reply and state["slots"]["time"] is None


def test_honest_message_when_whatsapp_fails(fake_llm, fake_whatsapp):
    fake_whatsapp.succeed = False
    fake_llm.slots[FULL_MSG] = FULL
    _, state = say(FULL_MSG, {})
    reply, _ = say("yes", state)
    assert "is confirmed" in reply and "could not send" in reply


def test_stop_booking_with_cancel_that(fake_llm):
    fake_llm.slots["book a table"] = {}
    _, state = say("book a table", {})
    reply, state = say("cancel that", state)
    assert "stopped this booking" in reply and "flow" not in state


CANCEL_MSG = "cancel my booking for tomorrow at 8 pm, number 9876543210"
CANCEL_SLOTS = {"phone": "9876543210", "date": TOMORROW, "time": "20:00"}


def _book_one(fake_llm):
    fake_llm.slots[FULL_MSG] = FULL
    _, state = say(FULL_MSG, {})
    say("yes", state)


def test_cancel_requires_whatsapp_code(fake_llm, fake_whatsapp):
    _book_one(fake_llm)
    fake_llm.slots[CANCEL_MSG] = CANCEL_SLOTS
    reply, state = say(CANCEL_MSG, {})
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
    _book_one(fake_llm)
    fake_whatsapp.succeed = False
    fake_llm.slots[CANCEL_MSG] = CANCEL_SLOTS
    reply, state = say(CANCEL_MSG, {})
    assert "cannot verify" in reply and booking_count() == 1


def test_phone_extraction_ignores_dates_and_times():
    assert brain._phone_from_text("on 2026-11-20 at 20:00") is None
    assert brain._phone_from_text("my number is 98765 43210") == "9876543210"
    assert brain._phone_from_text("+91 98765-43210") == "9876543210"
    assert brain._phone_from_text("1234567890") is None  # not a mobile number
