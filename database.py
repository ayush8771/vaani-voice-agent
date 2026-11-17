"""
database.py — SQLite storage for Vaani.

This module only reads and writes. Business rules live in bookings.py and
availability.py; notifications live in services/.

Connections use autocommit mode (isolation_level=None) so callers can open an
explicit `BEGIN IMMEDIATE` transaction when they need an atomic
check-then-write (see bookings.book()).
"""

import logging
import sqlite3
from contextlib import contextmanager

from config import settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurant_tables (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    label  TEXT    NOT NULL UNIQUE,
    seats  INTEGER NOT NULL CHECK(seats > 0)
);

CREATE TABLE IF NOT EXISTS bookings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    date    TEXT    NOT NULL,
    time    TEXT    NOT NULL,
    people  INTEGER NOT NULL,
    name    TEXT    NOT NULL,
    phone   TEXT    NOT NULL,
    status  TEXT    NOT NULL DEFAULT 'confirmed'
                CHECK(status IN ('confirmed', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS otps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT    NOT NULL,
    purpose     TEXT    NOT NULL,
    code_hash   TEXT    NOT NULL,
    created_at  REAL    NOT NULL,
    expires_at  REAL    NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    used        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_otps_phone ON otps(phone, purpose, created_at);

CREATE TABLE IF NOT EXISTS admins (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT    NOT NULL UNIQUE,
    password_hash  TEXT    NOT NULL,
    role           TEXT    NOT NULL DEFAULT 'staff' CHECK(role IN ('owner', 'staff')),
    totp_secret    TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS login_attempts (
    key           TEXT PRIMARY KEY,
    fails         INTEGER NOT NULL DEFAULT 0,
    locked_until  REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL DEFAULT (datetime('now')),
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    target    TEXT,
    ip        TEXT
);
"""

# Columns added after v1. Old databases are upgraded in place by init_db().
BOOKING_MIGRATIONS = {
    "end_time":      "ALTER TABLE bookings ADD COLUMN end_time TEXT",
    "table_id":      "ALTER TABLE bookings ADD COLUMN table_id INTEGER REFERENCES restaurant_tables(id)",
    "token":         "ALTER TABLE bookings ADD COLUMN token TEXT",
    "checked_in_at": "ALTER TABLE bookings ADD COLUMN checked_in_at TEXT",
    "created_at":    "ALTER TABLE bookings ADD COLUMN created_at TEXT",
}

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_bookings_table_slot ON bookings(table_id, date, status, time, end_time)",
    "CREATE INDEX IF NOT EXISTS idx_bookings_phone ON bookings(phone)",
    "CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(date)",
    "CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_token ON bookings(token)",
)


def get_connection() -> sqlite3.Connection:
    con = sqlite3.connect(settings.db_path, timeout=10, isolation_level=None,
                          check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    con.execute("PRAGMA busy_timeout=10000;")
    return con


@contextmanager
def connection():
    con = get_connection()
    try:
        yield con
    finally:
        con.close()


@contextmanager
def transaction():
    """BEGIN IMMEDIATE takes the write lock up front, so check-then-insert is atomic."""
    con = get_connection()
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def init_db() -> None:
    with connection() as con:
        con.executescript(SCHEMA)
        existing = {row[1] for row in con.execute("PRAGMA table_info(bookings)")}
        for column, sql in BOOKING_MIGRATIONS.items():
            if column not in existing:
                con.execute(sql)
                logger.info("Migrated bookings table: added %s", column)
        for sql in INDEXES:
            con.execute(sql)
        _sync_tables(con)
    logger.info("Database ready: %s", settings.db_path)


def _sync_tables(con: sqlite3.Connection) -> None:
    """Make restaurant_tables match TABLE_SEATS. Labels are T1, T2, ..."""
    for i, seats in enumerate(settings.table_seats, start=1):
        con.execute(
            "INSERT INTO restaurant_tables(label, seats) VALUES (?, ?) "
            "ON CONFLICT(label) DO UPDATE SET seats=excluded.seats",
            (f"T{i}", seats),
        )


def active_tables(con: sqlite3.Connection) -> list:
    """Tables currently configured, smallest first."""
    labels = [f"T{i}" for i in range(1, len(settings.table_seats) + 1)]
    if not labels:
        return []
    marks = ",".join("?" * len(labels))
    return con.execute(
        f"SELECT id, label, seats FROM restaurant_tables WHERE label IN ({marks}) "
        "ORDER BY seats, id",
        labels,
    ).fetchall()


# ── ADMIN QUERIES ─────────────────────────────────────────────────────────────
BOOKING_COLUMNS = (
    "b.id, b.date, b.time, b.people, b.name, b.phone, b.status, "
    "COALESCE(t.label, '-'), b.checked_in_at"
)
BOOKING_FROM = "FROM bookings b LEFT JOIN restaurant_tables t ON t.id = b.table_id"


def _build_admin_filters(query="", status="all", date_from="", date_to=""):
    clauses, params = [], []
    query = (query or "").strip()
    status = (status or "all").strip().lower()

    if query:
        q = f"%{query}%"
        clauses.append("(b.name LIKE ? OR b.phone LIKE ?)")
        params.extend([q, q])
    if status in ("confirmed", "cancelled"):
        clauses.append("b.status = ?")
        params.append(status)
    if date_from:
        clauses.append("b.date >= ?")
        params.append(date_from.strip())
    if date_to:
        clauses.append("b.date <= ?")
        params.append(date_to.strip())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def get_admin_bookings(query="", status="all", date_from="", date_to="",
                       page=1, page_size=10):
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 10), 100))
    where_sql, params = _build_admin_filters(query, status, date_from, date_to)

    with connection() as con:
        total = con.execute(f"SELECT COUNT(*) {BOOKING_FROM} {where_sql}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT {BOOKING_COLUMNS} {BOOKING_FROM} {where_sql} "
            "ORDER BY b.date DESC, b.time DESC, b.id DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return rows, total


def get_admin_stats(today: str, query="", status="all", date_from="", date_to=""):
    where_sql, params = _build_admin_filters(query, status, date_from, date_to)
    with connection() as con:
        row = con.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN b.status='confirmed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN b.status='cancelled' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN b.date=? THEN 1 ELSE 0 END) "
            f"{BOOKING_FROM} {where_sql}",
            [today, *params],
        ).fetchone()
    values = [int(v or 0) for v in row]
    return dict(zip(("total", "confirmed", "cancelled", "today"), values))


def get_admin_bookings_for_export(query="", status="all", date_from="", date_to=""):
    where_sql, params = _build_admin_filters(query, status, date_from, date_to)
    with connection() as con:
        return con.execute(
            f"SELECT {BOOKING_COLUMNS} {BOOKING_FROM} {where_sql} "
            "ORDER BY b.date DESC, b.time DESC, b.id DESC",
            params,
        ).fetchall()


def set_booking_status(booking_id: int, status: str) -> bool:
    with connection() as con:
        cur = con.execute("UPDATE bookings SET status=? WHERE id=?", (status, booking_id))
        return cur.rowcount == 1


# ── AUDIT LOG ─────────────────────────────────────────────────────────────────
def audit(actor: str, action: str, target: str = "", ip: str = "") -> None:
    with connection() as con:
        con.execute(
            "INSERT INTO audit_log(actor, action, target, ip) VALUES (?, ?, ?, ?)",
            (actor, action, target, ip),
        )


def recent_audit(limit: int = 20):
    with connection() as con:
        return con.execute(
            "SELECT ts, actor, action, target FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
