"""Phase 4: customer memory, booking notes, occasion offers, waitlist and 'forget me'."""

import brain
import memory
from conftest import TOMORROW
from database import connection


def say(msg, state, code=None):
    reply, _audio, state = brain.get_ai_response(msg, state, stt_language=code)
    return reply, state


def book(first_msg, name="Ravi", phone="9876543210", code=None):
    reply, state = say(first_msg, {}, code)
    reply, state = say(phone, state, code)
    if "Just to confirm" not in reply and "ಒಮ್ಮೆ" not in reply and "कन्फर्म" not in reply:
        reply, state = say(name, state, code)
    return say("yes", state, code)


def rows(sql, *args):
    with connection() as con:
        return con.execute(sql, args).fetchall()


def test_first_booking_creates_profile_and_says_so():
    reply, _ = book("Book a table for 4 tomorrow at 8 pm")
    assert "I will remember your preferences" in reply and "forget me" in reply
    assert memory.get("9876543210")["name"] == "Ravi"
    assert memory.get("9876543210")["visits"] == 1


def test_returning_caller_is_welcomed_and_not_asked_their_name():
    book("Book a table for 4 tomorrow at 8 pm")
    reply, state = say("Book a table for 2 tomorrow at 1 pm", {})
    reply, state = say("9876543210", state)
    assert reply.startswith("Welcome back, Ravi!") and "Just to confirm" in reply
    reply, _ = say("yes", state)
    assert "I will remember" not in reply                      # told only the first time
    assert memory.get("9876543210")["visits"] == 2


def test_usual_table_uses_remembered_party_and_time():
    book("Book a table for 4 tomorrow at 8 pm")
    reply, state = say("Book my usual table for tomorrow", {})
    reply, state = say("9876543210", state)
    assert "Welcome back, Ravi!" in reply and "a table for 4" in reply and "8 PM" in reply


def test_dietary_note_and_occasion_reach_booking_and_offer():
    reply, _ = book("Book a table for 2 tomorrow at 8 pm for my wife's birthday, she has a nut allergy")
    assert "complimentary dessert" in reply                    # birthday offer from knowledge/offers.json
    assert rows("SELECT notes FROM bookings") == [("nut allergy; birthday",)]
    assert memory.get("9876543210")["notes"] == ["nut allergy"]


def test_returning_caller_notes_carry_over():
    book("Book a table for 2 tomorrow at 8 pm, we are vegetarian")
    book("Book a table for 2 tomorrow at 1 pm")
    assert rows("SELECT notes FROM bookings ORDER BY id") == [("vegetarian",), ("vegetarian",)]


def test_forget_me_removes_profile():
    book("Book a table for 4 tomorrow at 8 pm")
    reply, state = say("Please forget me", {})
    assert "10-digit mobile number" in reply
    reply, state = say("9876543210", state)
    assert reply.startswith("Done, I have removed") and memory.get("9876543210") is None
    reply, _ = say("forget me, my number is 9876543210", {})
    assert reply == "I had nothing saved for that number."


def test_forget_me_in_hindi():
    book("Book a table for 4 tomorrow at 8 pm")
    reply, state = say("मुझे भूल जाओ, मेरा नंबर 9876543210 है", {}, "hi-IN")
    assert memory.get("9876543210") is None and "हटा दी है" in reply


def test_welcome_back_in_hindi():
    book("Book a table for 4 tomorrow at 8 pm")
    reply, state = say("कल रात 8 बजे 2 लोगों के लिए टेबल बुक करनी है", {}, "hi-IN")
    reply, state = say("9876543210", state, "hi-IN")
    assert reply.startswith("फिर से स्वागत है, Ravi!")


def test_full_slot_offers_waitlist_and_joins():
    book("Book a table for 6 tomorrow at 8 pm", name="Asha", phone="9123456780")   # only 6-seater taken
    reply, state = say("Book a table for 6 tomorrow at 8 pm", {})
    assert "fully booked" in reply and "Say waitlist" in reply
    reply, state = say("waitlist please", state)
    reply, state = say("9876543210", state)
    reply, state = say("Ravi", state)
    assert reply.startswith("You are on the waitlist for Thursday 19 November at 8 PM")
    assert rows("SELECT date, time, people, name, status FROM waitlist") == [
        (TOMORROW, "20:00", 6, "Ravi", "waiting")]
