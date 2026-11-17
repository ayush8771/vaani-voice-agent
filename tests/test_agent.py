"""
Agent tests. The LLM is scripted (FakeChat); everything else is real:
the MCP server, the in-memory MCP client session, the tools and the database.
"""

import asyncio
import json

import brain
import phrases
from conftest import TOMORROW
from database import connection


def say(msg, state):
    reply, _audio, state = brain.get_ai_response(msg, state)
    return reply, state


def rows(sql, *args):
    with connection() as con:
        return con.execute(sql, args).fetchall()


def test_multi_intent_uses_two_tools(fake_chat):
    msg = "Is there a table for 4 tomorrow at 8 pm, and do you have parking?"
    fake_chat.script = [
        [("check_availability", {"date": TOMORROW, "time": "20:00", "party_size": 4}),
         ("query_menu_faq", {"query": "parking"})],
        "Parking is free for everyone!",   # must never be spoken: not from our records
    ]
    reply, state = say(msg, {})
    assert "we have a table on Thursday 19 November at 8 PM" in reply
    assert "Parking is free" not in reply and phrases.unsure_reply() in reply
    assert reply.endswith("Would you like me to book it?")
    assert len(fake_chat.seen) == 1                    # one LLM call: tool words are spoken directly
    calls = rows("SELECT tool, path FROM tool_calls ORDER BY id")
    assert ("check_availability", "agent") in calls and ("query_menu_faq", "agent") in calls

    # Saying yes continues the booking with the details already known.
    reply, state = say("yes please", state)
    assert reply == phrases.BOOKING_Q["name"] and state["slots"]["people"] == 4


def test_agent_prepares_booking_and_caller_yes_commits(fake_chat):
    msg = "My name is Ravi, 9876543210, we are four, tomorrow evening at eight, sort it out please"
    fake_chat.script = [[("prepare_booking", {"date": "tomorrow", "time": "8 pm", "party_size": "four",
                                              "name": "Ravi", "phone": "+91 98765 43210"})]]
    reply, state = say(msg, {})
    assert reply.startswith("Just to confirm") and state["stage"] == "confirm"
    assert rows("SELECT COUNT(*) FROM bookings")[0][0] == 0      # nothing written yet

    reply, state = say("yes", state)
    assert "is confirmed" in reply
    assert rows("SELECT date, time, people, name FROM bookings") == [(TOMORROW, "20:00", 4, "Ravi")]
    paths = [r[0] for r in rows("SELECT path FROM tool_calls WHERE tool IN ('prepare_booking','book_table') ORDER BY id")]
    assert paths == ["agent", "rules"]   # agent prepared it, the caller's yes committed it


def test_agent_cannot_book_by_itself(fake_chat):
    fake_chat.script = [[("book_table", {"hold_token": "anything", "caller_confirmed": True})],
                        "Your table is booked!"]
    say("just sort it all out for me right now", {})
    assert rows("SELECT COUNT(*) FROM bookings")[0][0] == 0
    tool_msgs = [m for m in fake_chat.seen[-1] if m["role"] == "tool"]
    assert "not available" in tool_msgs[0]["content"]


def test_agent_missing_details_hands_off_to_questions(fake_chat):
    fake_chat.script = [[("prepare_booking", {"date": TOMORROW, "time": "20:00", "party_size": 2,
                                              "name": None, "phone": None})]]
    reply, state = say("could you sort out dinner for two tomorrow at 8 pm", {})
    assert reply == phrases.BOOKING_Q["name"]
    assert state["flow"] == "booking" and state["asked"] == "name"
    reply, state = say("Ravi", state)
    assert reply == phrases.BOOKING_Q["phone"]


def test_escalation_creates_handoff(fake_chat):
    fake_chat.script = [[("escalate_to_human", {"reason": "complaint about last visit",
                                                "summary": "Food was cold"})]]
    reply, _ = say("I want to speak to the manager about my last visit", {})
    assert "passed this to our team" in reply
    assert rows("SELECT reason FROM handoffs") == [("complaint about last visit",)]


def test_booking_status(fake_chat):
    from vaani_mcp import tools
    tools.run("book_table", caller_confirmed=True, hold_token=tools.prepare_booking(
        TOMORROW, "20:00", 2, "Asha", "9876543210")["hold_token"])
    fake_chat.script = [[("get_booking_status", {"phone": "9876543210"})], "You have a table for 2 tomorrow at 8 PM."]
    reply, _ = say("is my reservation for 9876543210 still on?", {})
    assert "8 PM" in reply


def test_llm_down_falls_back_to_knowledge_base(fake_chat):
    fake_chat.script = []          # chat() returns None
    reply, state = say("what is the meaning of life", {})
    assert reply == phrases.unsure_reply()


def test_tool_calls_are_logged_with_masked_secrets(fake_chat):
    fake_chat.script = [[("prepare_booking", {"date": TOMORROW, "time": "20:00", "party_size": 2,
                                              "name": "Asha", "phone": "9876543210"})]]
    say("could you arrange dinner for Asha, two people, tomorrow 8 pm, 9876543210", {})
    args, result = rows("SELECT args, result FROM tool_calls WHERE tool='prepare_booking'")[0]
    assert "9876543210" not in args and "9876543210" not in result
    assert '"hold_token": "***"' in result


def test_mcp_server_exposes_all_tools_and_guards_book_table():
    from mcp.shared.memory import create_connected_server_and_client_session
    from vaani_mcp.server import build_server, result_to_dict

    async def main():
        async with create_connected_server_and_client_session(build_server("mcp-external")._mcp_server) as s:
            names = {t.name for t in (await s.list_tools()).tools}
            forged = result_to_dict(await s.call_tool("book_table", {"hold_token": "x", "caller_confirmed": True}))
            unconfirmed = result_to_dict(await s.call_tool("book_table", {"hold_token": "x", "caller_confirmed": False}))
            return names, forged, unconfirmed

    names, forged, unconfirmed = asyncio.run(main())
    assert {"check_availability", "prepare_booking", "book_table", "start_cancellation",
            "confirm_cancellation", "query_menu_faq", "escalate_to_human"} <= names
    assert forged["reason"] == "invalid_hold" and unconfirmed["reason"] == "not_confirmed"
    assert ("book_table", "mcp-external") in rows("SELECT tool, path FROM tool_calls")
