"""Phase 4: the owner's analytics agent (text-to-SQL over the official SQLite MCP server)."""

import sqlite3

import pytest

import admins
import analytics
import bookings
from conftest import TOMORROW


@pytest.fixture
def seeded():
    bookings.book(TOMORROW, "20:00", 4, "Ravi", "9876543210", notify=False)
    bookings.book(TOMORROW, "20:00", 2, "Asha", "9123456780", notify=False)
    bookings.book(TOMORROW, "13:00", 2, "Meera", "9988776655", notify=False)
    cancelled = bookings.book(TOMORROW, "19:00", 2, "Amit", "9000000001", notify=False)
    bookings.cancel(cancelled.booking_id, notify=False)


@pytest.mark.parametrize("sql", [
    "DELETE FROM bookings",
    "UPDATE bookings SET status='cancelled'",
    "SELECT * FROM admins",
    "SELECT * FROM otps",
    "SELECT 1; DROP TABLE bookings",
    "PRAGMA table_info(bookings)",
    "ATTACH DATABASE 'x.db' AS x",
    "WITH x AS (SELECT 1) DELETE FROM bookings",
    "SELECT * FROM sqlite_master",
])
def test_unsafe_sql_is_refused(sql):
    with pytest.raises(analytics.UnsafeQuery):
        analytics.validate_sql(sql)


def test_safe_sql_gets_a_row_limit():
    assert analytics.validate_sql("SELECT time FROM bookings").endswith("LIMIT 200")
    assert "LIMIT 5" in analytics.validate_sql("SELECT time FROM bookings LIMIT 5")
    sql = analytics.validate_sql("WITH t AS (SELECT time FROM bookings) SELECT * FROM t")
    assert sql.startswith("WITH")


def test_snapshot_masks_phones_and_drops_secrets(seeded):
    path = analytics.build_snapshot()
    con = sqlite3.connect(path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"bookings", "waitlist", "customers", "agent_calls"}
    assert con.execute("SELECT phone_last4 FROM bookings WHERE customer_name='Ravi'").fetchone() == ("3210",)
    con.close()
    assert b"9876543210" not in open(path, "rb").read()


def test_answer_through_the_sqlite_mcp_server(seeded, fake_chat):
    fake_chat.script = ["SELECT time, COUNT(*) AS bookings FROM bookings WHERE status='confirmed' "
                        "GROUP BY time ORDER BY bookings DESC"]
    result = analytics.answer("Which time slots are busiest overall?")
    assert not result.error
    assert result.rows == [{"time": "20:00", "bookings": 2}, {"time": "13:00", "bookings": 1}]
    assert result.summary == "Top result: time 20:00, bookings 2 (2 rows below)."
    assert len(fake_chat.seen) == 1                    # no second LLM call to summarise


def test_single_value_answer_needs_no_summary_call(seeded, fake_chat):
    fake_chat.script = ["```sql\nSELECT COUNT(*) AS cancelled_bookings FROM bookings WHERE status='cancelled';\n```"]
    result = analytics.answer("How many bookings were cancelled?")
    assert result.summary == "cancelled bookings 1" and len(fake_chat.seen) == 1


def test_unsafe_llm_sql_is_refused_not_run(seeded, fake_chat):
    fake_chat.script = ["DELETE FROM bookings"]
    result = analytics.answer("delete everything")
    assert result.error.startswith("Query refused")
    with sqlite3.connect(analytics.settings.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 4


@pytest.fixture
def client():
    from app import create_app
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
    return app.test_client()


def test_ask_page_owner_only(client, seeded, fake_chat):
    admins.create_admin("staff1", "correct-horse-battery", "staff")
    client.post("/admin/login", data={"username": "staff1", "password": "correct-horse-battery"})
    assert client.get("/admin/ask").status_code == 403
    client.post("/admin/logout")
    admins.create_admin("owner1", "correct-horse-battery", "owner")
    client.post("/admin/login", data={"username": "owner1", "password": "correct-horse-battery"})
    fake_chat.script = ["SELECT COUNT(*) AS confirmed FROM bookings WHERE status='confirmed'"]
    page = client.post("/admin/ask", data={"question": "How many confirmed bookings?"})
    assert page.status_code == 200 and b"confirmed 3" in page.data and b"SQL that was run" in page.data


def test_invented_date_filter_triggers_one_retry(seeded, fake_chat):
    """Eval failure: 'average party size' came back filtered to today's bookings only."""
    fake_chat.script = [
        "SELECT AVG(people) AS avg_party FROM bookings WHERE status='confirmed' AND date = date('now','localtime')",
        "SELECT ROUND(AVG(people),1) AS avg_party FROM bookings WHERE status='confirmed'",
    ]
    result = analytics.answer("What is the average party size of our confirmed bookings?")
    assert "date(" not in result.sql.lower() and result.rows == [{"avg_party": 2.7}]
    assert not analytics.invented_date_filter("How many bookings tomorrow?", "... date('now','+1 day')")


def test_example_questions_need_no_llm(seeded, fake_chat):
    for question in list(analytics.KNOWN_SQL):
        result = analytics.answer(question.capitalize() + "?")
        assert not result.error, (question, result.error)
    assert fake_chat.seen == []


def test_repeated_question_reuses_sql_and_server(seeded, fake_chat):
    fake_chat.script = ["SELECT COUNT(*) AS n FROM bookings"]
    first = analytics.answer("How many bookings are there in total?")
    server = analytics._server
    second = analytics.answer("how many bookings are there in total")
    assert first.rows == second.rows == [{"n": 4}] and len(fake_chat.seen) == 1
    assert analytics._server is server and server.alive()          # one long-lived MCP server


def test_snapshot_rebuilt_only_when_data_changes(seeded):
    path = analytics.build_snapshot()
    stamp = analytics.os.stat(path).st_mtime_ns
    assert analytics.os.stat(analytics.build_snapshot()).st_mtime_ns == stamp
    bookings.book(TOMORROW, "13:00", 2, "New", "9000000002", notify=False)
    analytics.build_snapshot()
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 5
