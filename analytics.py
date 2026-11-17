"""
analytics.py — the owner's analytics agent: questions in plain language, answered from data.

  owner question
     -> LLM writes one SQLite SELECT (schema + examples in the prompt)
     -> validated with sqlglot: one SELECT, known tables only, row limit added
     -> run through the official SQLite MCP server (mcp-server-sqlite, a separate
        process over stdio) against a fresh read-only analytics copy
     -> short answer + the rows + the exact SQL, shown to the owner

The analytics copy holds bookings, waitlist, customers and agent activity, with
phone numbers reduced to their last 4 digits and no secrets (admins, codes,
tokens, audit log). Vaani only ever calls the server's read tools.
"""

import ast
import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
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
    return os.path.splitext(settings.db_path)[0] + ".analytics.db"


def build_snapshot(path: Optional[str] = None) -> str:
    """A fresh copy with only what analytics needs; phones masked, no secrets."""
    path = path or snapshot_path()
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


# ── 3. RUN THROUGH THE SQLITE MCP SERVER ──────────────────────────────────────
async def _run_via_mcp(db_path: str, sql: str) -> list:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from mcp_server_sqlite import main; main()", "--db-path", db_path],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool = "read_query"
            assert tool in READ_TOOLS              # Vaani never calls the server's write tools
            result = await session.call_tool(tool, {"query": sql})
    text = "".join(getattr(block, "text", "") for block in result.content or [])
    if text.startswith(("Database error", "Error")):
        raise RuntimeError(text)
    rows = ast.literal_eval(text) if text else []     # the server returns a Python list of dicts
    return rows if isinstance(rows, list) else []


def run_query(sql: str) -> list:
    db = build_snapshot()
    return asyncio.run(asyncio.wait_for(_run_via_mcp(db, sql), timeout=60))


# ── 4. ANSWER ─────────────────────────────────────────────────────────────────
def _summarise(question: str, rows: list) -> str:
    if not rows:
        return "No matching data."
    if len(rows) == 1 and len(rows[0]) == 1:
        (key, value), = rows[0].items()
        return f"{key.replace('_', ' ')}: {value}"
    text = llm_client.complete(
        f"Question: {question}\nResult rows (JSON): {json.dumps(rows[:30], default=str)}",
        system=("Answer the restaurant owner's question in one or two plain sentences using ONLY the result "
                "rows. Do not invent numbers."),
        max_tokens=120,
    )
    return text or f"{len(rows)} rows found."


def answer(question: str) -> Answer:
    started = time.time()
    result = Answer(question=question.strip()[:300])
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
    result.summary = _summarise(result.question, result.rows)
    result.seconds = time.time() - started
    return result
