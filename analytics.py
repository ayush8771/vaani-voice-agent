"""
analytics.py — the owner's analytics agent: questions in plain language, answered from data.

  owner question
     -> LLM writes one SQLite SELECT (schema + examples in the prompt)
     -> validated with sqlglot: one SELECT, known tables only, row limit added
     -> run through the official SQLite MCP server (mcp-server-sqlite, a separate
        process over stdio) against a fresh read-only analytics copy
     -> answer + the rows + the exact SQL, shown to the owner (no LLM summary: nothing invented)

The analytics copy holds bookings, waitlist, customers and agent activity, with
phone numbers reduced to their last 4 digits and no secrets (admins, codes,
tokens, audit log). Vaani only ever calls the server's read tools.
"""

import ast
import asyncio
import logging
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import sqlglot
from sqlglot import exp

import availability as av
import llm_client
from config import settings

logger = logging.getLogger(__name__)

ALLOWED_TABLES = {"bookings", "waitlist", "customers", "agent_calls"}
READ_TOOLS = {"read_query", "list_tables", "describe_table"}
ROW_LIMIT = 200

SCHEMA = """Tables (SQLite):
bookings(id, date TEXT 'YYYY-MM-DD', weekday TEXT 'Monday'.., time TEXT 'HH:MM', hour INTEGER 0-23,
         people INTEGER, status TEXT 'confirmed'|'cancelled', table_label TEXT, checked_in INTEGER 0/1,
         created_date TEXT, notes TEXT, customer_name TEXT, phone_last4 TEXT)
waitlist(date TEXT, time TEXT, people INTEGER, status TEXT 'waiting'|'notified'|'expired')
customers(name TEXT, lang TEXT 'en'|'hi'|'hinglish'|'kn', visits INTEGER, last_people INTEGER, notes TEXT)
agent_calls(day TEXT 'YYYY-MM-DD', path TEXT 'rules'|'agent:reservation'|..., tool TEXT, ok INTEGER, latency_ms INTEGER)"""

EXAMPLES = """Q: How many confirmed bookings do we have tomorrow?
SQL: SELECT COUNT(*) AS bookings FROM bookings WHERE status='confirmed' AND date=date('now','localtime','+1 day')
Q: Which time slots were busiest this week?
SQL: SELECT time, COUNT(*) AS bookings FROM bookings WHERE status='confirmed' AND date >= date('now','localtime','-6 days') GROUP BY time ORDER BY bookings DESC LIMIT 5
Q: What share of bookings were cancelled?
SQL: SELECT ROUND(100.0*SUM(status='cancelled')/COUNT(*),1) AS cancelled_percent FROM bookings
Q: How many people are on the waitlist?
SQL: SELECT COUNT(*) AS waiting FROM waitlist WHERE status='waiting'"""


@dataclass
class Answer:
    question: str
    sql: str = ""
    rows: list = field(default_factory=list)
    summary: str = ""
    error: str = ""
    seconds: float = 0.0


# ── 1. THE ANALYTICS COPY ─────────────────────────────────────────────────────
def snapshot_path() -> str:
    """Outside the project folder (cloud-sync tools like OneDrive lock files they upload)."""
    folder = os.path.join(tempfile.gettempdir(), "vaani")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"analytics-{os.getpid()}.db")


_built_from = None


def _data_version() -> tuple:
    """Changes whenever the main database (or its write-ahead log) changes."""
    stamp = [settings.db_path]
    for suffix in ("", "-wal"):
        try:
            st = os.stat(settings.db_path + suffix)
            stamp += [st.st_mtime_ns, st.st_size]
        except OSError:
            stamp += [0, 0]
    return tuple(stamp)


def build_snapshot(path: Optional[str] = None) -> str:
    """A copy with only what analytics needs; phones masked, no secrets.
    Rebuilt only when the main database has changed."""
    global _built_from
    explicit = path is not None
    path = path or snapshot_path()
    version = _data_version()
    if not explicit and _built_from == version and os.path.exists(path):
        return path
    if os.path.exists(path):
        os.remove(path)
    src = sqlite3.connect(settings.db_path)
    dst = sqlite3.connect(path)
    try:
        dst.executescript("""
            CREATE TABLE bookings(id INTEGER, date TEXT, weekday TEXT, time TEXT, hour INTEGER, people INTEGER,
                                  status TEXT, table_label TEXT, checked_in INTEGER, created_date TEXT,
                                  notes TEXT, customer_name TEXT, phone_last4 TEXT);
            CREATE TABLE waitlist(date TEXT, time TEXT, people INTEGER, status TEXT);
            CREATE TABLE customers(name TEXT, lang TEXT, visits INTEGER, last_people INTEGER, notes TEXT);
            CREATE TABLE agent_calls(day TEXT, path TEXT, tool TEXT, ok INTEGER, latency_ms INTEGER);
        """)
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for (bid, d, t, people, status, label, checked, created, notes, name, phone) in src.execute(
                "SELECT b.id, b.date, b.time, b.people, b.status, t.label, b.checked_in_at IS NOT NULL, "
                "substr(b.created_at,1,10), b.notes, b.name, b.phone "
                "FROM bookings b LEFT JOIN restaurant_tables t ON t.id=b.table_id"):
            dst.execute("INSERT INTO bookings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (bid, d, weekdays[av.parse_date(d).weekday()], t, int(t[:2]), people, status, label,
                         int(checked), created, notes, name, (phone or "")[-4:]))
        dst.executemany("INSERT INTO waitlist VALUES (?,?,?,?)",
                        src.execute("SELECT date, time, people, status FROM waitlist"))
        dst.executemany("INSERT INTO customers VALUES (?,?,?,?,?)",
                        src.execute("SELECT name, lang, visits, last_people, notes FROM customers"))
        dst.executemany("INSERT INTO agent_calls VALUES (?,?,?,?,?)",
                        src.execute("SELECT substr(ts,1,10), path, tool, ok, latency_ms FROM tool_calls"))
        dst.commit()
    finally:
        src.close()
        dst.close()
    if not explicit:
        _built_from = version
    return path


# ── 2. SQL: WRITE AND VALIDATE ────────────────────────────────────────────────
_PERIOD = re.compile(r"\b(today|tonight|tomorrow|yesterday|week|weekend|month|year|last|next|this|since|"
                     r"past|upcoming|recent|daily|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\d",
                     re.I)
_DATE_FILTER = re.compile(r"date\s*\(\s*'now'", re.I)


def invented_date_filter(question: str, sql: str) -> bool:
    """The model filtered by the current date although the question names no period."""
    return bool(_DATE_FILTER.search(sql)) and not _PERIOD.search(question)


def generate_sql(question: str, feedback: str = "") -> Optional[str]:
    today = av.today()
    raw = llm_client.complete(
        question + (f"\n({feedback})" if feedback else ""),
        system=(f"You write one SQLite SELECT query that answers the restaurant owner's question.\n{SCHEMA}\n"
                f"Today is {today.strftime('%A')} {today.isoformat()}. Use date('now','localtime') for today.\n"
                f"Examples:\n{EXAMPLES}\n"
                "Rules: only SELECT; only these tables; give columns readable aliases. "
                "Filter by date ONLY when the question names a period (today, tomorrow, this week, last "
                "month...); otherwise use all rows. 'Right now' or 'currently' means status, not today's date. "
                "Output only the SQL, no explanation, no markdown."),
        max_tokens=250,
    )
    if not raw:
        return None
    sql = re.sub(r"```(?:sql)?", "", raw, flags=re.I).strip()
    sql = re.sub(r"^\s*SQL:\s*", "", sql, flags=re.I)
    return sql.rstrip(";").strip()


class UnsafeQuery(ValueError):
    pass


def validate_sql(sql: str) -> str:
    """Return a safe single SELECT with a row limit, or raise UnsafeQuery."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise UnsafeQuery(f"Could not parse the query: {exc}") from exc
    if len(statements) != 1:
        raise UnsafeQuery("Only one statement is allowed.")
    query = statements[0]
    if not isinstance(query, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise UnsafeQuery("Only SELECT queries are allowed.")
    for node in query.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
                             exp.Command, exp.Pragma, exp.Attach)):
            raise UnsafeQuery("The query may only read data.")
    ctes = {c.alias_or_name.lower() for c in query.find_all(exp.CTE)}
    tables = {t.name.lower() for t in query.find_all(exp.Table)} - ctes
    unknown = tables - ALLOWED_TABLES
    if unknown:
        raise UnsafeQuery(f"Unknown table(s): {', '.join(sorted(unknown))}")
    if isinstance(query, exp.Select) and not query.args.get("limit"):
        query = query.limit(ROW_LIMIT)
    return query.sql(dialect="sqlite")


# ── 3. RUN THROUGH THE SQLITE MCP SERVER (one long-lived process) ─────────────
class SqliteMcp:
    """One mcp-server-sqlite process and client session, kept open for the app's lifetime.
    Starting the server costs several seconds; reusing it costs milliseconds. The
    server opens the analytics copy afresh for every query, so rebuilding the copy
    in place is safe."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop: Optional[asyncio.Event] = None
        self._session = None
        self._error: Optional[BaseException] = None
        threading.Thread(target=self._run, name="vaani-sqlite-mcp", daemon=True).start()
        if not self._ready.wait(timeout=60):
            raise RuntimeError("SQLite MCP server did not start")
        if self._error:
            raise RuntimeError(f"SQLite MCP server failed: {self._error}")

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", "from mcp_server_sqlite import main; main()", "--db-path", self.db_path],
        )
        try:
            self._stop = asyncio.Event()
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._stop.wait()
        except BaseException as exc:
            self._error = exc
            self._ready.set()

    def alive(self) -> bool:
        return self._session is not None and self._error is None and self._loop.is_running()

    def query(self, sql: str) -> list:
        tool = "read_query"
        assert tool in READ_TOOLS                  # Vaani never calls the server's write tools
        result = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(tool, {"query": sql}), self._loop).result(60)
        text = "".join(getattr(block, "text", "") for block in result.content or [])
        if text.startswith(("Database error", "Error")):
            raise RuntimeError(text)
        rows = ast.literal_eval(text) if text else []   # the server returns a Python list of dicts
        return rows if isinstance(rows, list) else []

    def close(self):
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)


_server: Optional[SqliteMcp] = None
_server_lock = threading.Lock()


def run_query(sql: str) -> list:
    global _server
    db = build_snapshot()
    with _server_lock:
        if _server is None or not _server.alive() or _server.db_path != db:
            if _server is not None:
                _server.close()
            _server = SqliteMcp(db)
        server = _server
    return server.query(sql)


# ── 4. ANSWER ─────────────────────────────────────────────────────────────────
# The example questions on the page have hand-written SQL: no LLM needed.
KNOWN_SQL = {
    "which time slots were busiest this week":
        "SELECT time, COUNT(*) AS bookings FROM bookings WHERE status='confirmed' "
        "AND date BETWEEN date('now','localtime','-6 days') AND date('now','localtime') "
        "GROUP BY time ORDER BY bookings DESC LIMIT 5",
    "how many confirmed bookings do we have tomorrow":
        "SELECT COUNT(*) AS confirmed_bookings_tomorrow FROM bookings WHERE status='confirmed' "
        "AND date=date('now','localtime','+1 day')",
    "what share of bookings were cancelled":
        "SELECT ROUND(100.0*SUM(status='cancelled')/COUNT(*),1) AS cancelled_percent FROM bookings",
    "which weekday gets the most guests":
        "SELECT weekday, SUM(people) AS guests FROM bookings WHERE status='confirmed' "
        "GROUP BY weekday ORDER BY guests DESC LIMIT 7",
    "how many returning customers do we have":
        "SELECT COUNT(*) AS returning_customers FROM customers WHERE visits > 1",
    "how many people are on the waitlist":
        "SELECT COUNT(*) AS parties_waiting, COALESCE(SUM(people),0) AS guests_waiting "
        "FROM waitlist WHERE status='waiting'",
}
_learned_sql: dict = {}            # questions answered before (by the LLM), while the app runs


def _key(question: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", question.lower()).strip()


def _summarise(rows: list) -> str:
    """Describe the result from the rows themselves: instant, and nothing invented."""
    if not rows:
        return "No matching data."

    def fmt(v):
        return round(v, 1) if isinstance(v, float) else v

    def describe(row):
        return ", ".join(f"{k.replace('_', ' ')} {fmt(v)}" for k, v in row.items())
    if len(rows) == 1:
        return describe(rows[0])
    return f"Top result: {describe(rows[0])} ({len(rows)} rows below)."


def answer(question: str) -> Answer:
    started = time.time()
    result = Answer(question=question.strip()[:300])
    key = _key(result.question)
    sql = KNOWN_SQL.get(key) or _learned_sql.get(key)
    if not sql:
        sql = generate_sql(result.question)
        if sql and invented_date_filter(result.question, sql):
            # Small models like to add "today only" filters nobody asked for: ask once more.
            sql = generate_sql(result.question, feedback="The question names no time period: do not filter "
                                                         "by date; use all rows.") or sql
    if not sql:
        result.error = "The language model is not available right now."
        return result
    try:
        result.sql = validate_sql(sql)
    except UnsafeQuery as exc:
        result.sql, result.error = sql, f"Query refused: {exc}"
        return result
    try:
        result.rows = run_query(result.sql)
    except Exception as exc:
        logger.error("Analytics query failed: %s", exc)
        result.error = f"The query could not run: {str(exc)[:200]}"
        return result
    if key not in KNOWN_SQL:
        _learned_sql[key] = result.sql
    result.summary = _summarise(result.rows)
    result.seconds = time.time() - started
    return result
