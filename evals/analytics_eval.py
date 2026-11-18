"""
evals/analytics_eval.py — can the LLM answer owner questions with correct SQL?

Seeds two weeks of bookings, asks each question through the real analytics
agent (LLM -> SQL -> validation -> SQLite MCP server), and compares the first
row's values with a hand-written correct query.

    python -m evals.analytics_eval [--model qwen2.5:7b] [--verbose]
"""

import argparse
import logging
import os
import random
import sqlite3
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TTS_ENABLED", "false")
os.environ.setdefault("RAG_AUTOBUILD", "false")

from config import settings  # noqa: E402

CASES = [
    ("How many confirmed bookings do we have tomorrow?",
     "SELECT COUNT(*) FROM bookings WHERE status='confirmed' AND date=date('now','localtime','+1 day')"),
    ("How many guests are expected tomorrow?",
     "SELECT SUM(people) FROM bookings WHERE status='confirmed' AND date=date('now','localtime','+1 day')"),
    ("Which time slot had the most bookings in the last 7 days?",
     "SELECT time FROM bookings WHERE status='confirmed' AND date BETWEEN date('now','localtime','-6 days') "
     "AND date('now','localtime') GROUP BY time ORDER BY COUNT(*) DESC LIMIT 1"),
    ("What percentage of bookings were cancelled?",
     "SELECT ROUND(100.0*SUM(status='cancelled')/COUNT(*),1) FROM bookings"),
    ("Which weekday gets the most guests?",
     "SELECT weekday FROM bookings WHERE status='confirmed' GROUP BY weekday ORDER BY SUM(people) DESC LIMIT 1"),
    ("How many customers have visited more than once?",
     "SELECT COUNT(*) FROM customers WHERE visits > 1"),
    ("How many parties are on the waitlist right now?",
     "SELECT COUNT(*) FROM waitlist WHERE status='waiting'"),
    ("What is the average party size of confirmed bookings?",
     "SELECT ROUND(AVG(people),1) FROM bookings WHERE status='confirmed'"),
]


def seed() -> None:
    import availability as av
    from database import connection, init_db
    init_db()
    rng = random.Random(7)
    today = av.today()
    names = ["Ravi", "Asha", "Meera", "Amit", "Priya", "Neha", "Kiran", "Vikram", "Sneha", "Arjun"]
    with connection() as con:
        for offset in range(-14, 3):
            d = (today + timedelta(days=offset)).isoformat()
            for _ in range(rng.randint(2, 7)):
                t = rng.choice(["13:00", "13:30", "19:00", "19:30", "20:00", "20:00", "20:30"])
                status = "cancelled" if rng.random() < 0.15 else "confirmed"
                i = rng.randrange(len(names))
                con.execute("INSERT INTO bookings(date, time, end_time, people, name, phone, status, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (d, t, av.end_time(t), rng.choice([2, 2, 3, 4, 4, 6]), names[i], f"98765432{i:02d}",
                             status, d + "T10:00:00"))
        for i, n in enumerate(names):
            con.execute("INSERT INTO customers(phone, name, lang, visits, last_people) VALUES (?, ?, 'en', ?, 2)",
                        (f"98765432{i:02d}", n, rng.randint(1, 4)))
        for i in range(3):
            con.execute("INSERT INTO waitlist(date, time, people, name, phone, status, created_at) "
                        "VALUES (?, '20:00', 4, 'Wait', '9000000000', ?, ?)",
                        ((today + timedelta(days=1)).isoformat(), "waiting" if i < 2 else "notified",
                         today.isoformat()))


def _values(row: dict) -> set:
    out = set()
    for v in row.values():
        out.add(str(v).strip().lower())
        try:
            out.add(str(round(float(v), 1)))
        except (TypeError, ValueError):
            pass
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.model:
        settings.llm_model = args.model
    if args.base_url:
        settings.llm_base_url = args.base_url.rstrip("/")
    logging.basicConfig(level=logging.ERROR)

    settings.db_path = str(Path(tempfile.mkdtemp()) / "analytics_eval.db")
    seed()
    import analytics
    snapshot = analytics.build_snapshot()

    passed = 0
    for question, truth_sql in CASES:
        expected = sqlite3.connect(snapshot).execute(truth_sql).fetchone()[0]
        expected_norm = {str(expected).strip().lower()}
        try:
            expected_norm.add(str(round(float(expected), 1)))
        except (TypeError, ValueError):
            pass
        started = time.time()
        result = analytics.answer(question)
        got = _values(result.rows[0]) if result.rows else set()
        ok = not result.error and bool(expected_norm & got)
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {time.time() - started:5.1f}s  {question}  "
              f"(expected {expected}; got {result.rows[:1] or result.error})", flush=True)
        if args.verbose or not ok:
            print(f"        SQL: {result.sql}")
    print(f"\nAnalytics: {passed}/{len(CASES)} correct with {settings.llm_model}")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
