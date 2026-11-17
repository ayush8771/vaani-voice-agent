import threading

import availability as av
import bookings
from conftest import MONDAY, TOMORROW
from database import connection


def test_overlapping_bookings_on_same_table_clash():
    # Party of 6 only fits the single 6-seat table.
    assert bookings.book(TOMORROW, "19:00", 6, "Asha", "9876543210").ok
    second = bookings.book(TOMORROW, "19:15", 6, "Ravi", "9876543211")
    assert not second.ok and second.reason == "full"


def test_non_overlapping_bookings_share_a_table():
    assert bookings.book(TOMORROW, "19:00", 6, "Asha", "9876543210").ok
    assert bookings.book(TOMORROW, "20:30", 6, "Ravi", "9876543211").ok


def test_smallest_fitting_table_is_used():
    assert bookings.book(TOMORROW, "19:00", 2, "Asha", "9876543210").table == "T1"
    assert bookings.book(TOMORROW, "19:00", 2, "Ravi", "9876543211").table == "T2"
    assert bookings.book(TOMORROW, "19:00", 3, "Meera", "9876543212").table == "T3"


def test_party_size_is_respected():
    assert not bookings.book(TOMORROW, "19:00", 7, "Big", "9876543210").ok


def test_outside_opening_hours_rejected():
    for time in ("03:00", "16:00", "22:00"):  # 22:00 ends after the 23:00 close
        check = av.check_slot_rules(TOMORROW, time, 2)
        assert not check.ok and check.reason == "outside_hours"
    assert av.check_slot_rules(TOMORROW, "21:30", 2).ok  # last seating


def test_closed_day_rejected():
    check = av.check_slot_rules(MONDAY, "19:00", 2)
    assert not check.ok and check.reason == "closed"


def test_past_time_rejected():
    check = av.check_slot_rules("2026-11-18", "09:00", 2)
    assert not check.ok and check.reason == "past"


def test_large_group_flagged():
    assert av.check_slot_rules(TOMORROW, "19:00", 11).reason == "large_group"


def test_alternatives_offered_when_full():
    assert bookings.book(TOMORROW, "19:00", 6, "Asha", "9876543210").ok
    result = bookings.book(TOMORROW, "19:00", 6, "Ravi", "9876543211")
    assert result.reason == "full"
    assert "20:30" in result.alternatives
    with connection() as con:
        for t in result.alternatives:
            assert av.find_free_table(con, TOMORROW, t, 6)


def test_concurrent_bookings_never_double_book():
    results = []

    def attempt(i):
        results.append(bookings.book(TOMORROW, "19:00", 6, f"Guest{i}", f"98765432{i:02d}",
                                     notify=False))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r.ok for r in results) == 1


def test_speech_formatting():
    assert av.speak_time("19:30") == "7:30 PM"
    assert av.speak_time("20:00") == "8 PM"
    assert av.speak_time("12:00") == "12 PM"
    assert av.speak_date(TOMORROW) == "Thursday 19 November"
