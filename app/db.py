"""Portable DB layer: Postgres (Supabase/Neon) in production, SQLite fallback for local dev/tests.

SQL is written with `?` placeholders and converted for Postgres. Only TEXT / INTEGER / DOUBLE PRECISION
column types are used so the same DDL works on both engines.
"""
import os
import sqlite3
import threading
import uuid
import datetime as dt

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith("postgres")
SQLITE_PATH = os.getenv("SQLITE_PATH", "adforge.db")

_pool = None
_sqlite = None
_lock = threading.Lock()

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS memories (
        user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY (user_id, key))""",
    """CREATE TABLE IF NOT EXISTS campaigns (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, product TEXT NOT NULL,
        platform TEXT NOT NULL, audience TEXT NOT NULL DEFAULT '', tone TEXT NOT NULL DEFAULT '',
        budget_usd DOUBLE PRECISION NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft',
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_campaigns_user ON campaigns(user_id)",
    """CREATE TABLE IF NOT EXISTS variants (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, campaign_id TEXT NOT NULL, label TEXT NOT NULL,
        headline TEXT NOT NULL, body TEXT NOT NULL, cta TEXT NOT NULL DEFAULT '',
        impressions INTEGER NOT NULL DEFAULT 0, clicks INTEGER NOT NULL DEFAULT 0,
        conversions INTEGER NOT NULL DEFAULT 0, creative_style TEXT NOT NULL DEFAULT 'bold',
        creative_svg TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_variants_campaign ON variants(campaign_id)",
    """CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, campaign_id TEXT NOT NULL, action TEXT NOT NULL,
        status TEXT NOT NULL, turn_id TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS usage_log (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, thread_id TEXT NOT NULL,
        tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
        cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0,
        tools TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT 'ok', created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS messages_log (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, thread_id TEXT NOT NULL, role TEXT NOT NULL,
        content TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_msgs_thread ON messages_log(user_id, thread_id)",
]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def get_pool():
    global _pool
    if _pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=int(os.getenv("DB_POOL_MAX", "6")),
            kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
            check=ConnectionPool.check_connection, open=True, timeout=20,
        )
    return _pool


def _sqlite_conn():
    global _sqlite
    if _sqlite is None:
        _sqlite = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        _sqlite.row_factory = sqlite3.Row
        _sqlite.execute("PRAGMA journal_mode=WAL")
    return _sqlite


def query(sql: str, params: tuple = ()) -> list[dict]:
    if IS_PG:
        with get_pool().connection() as conn:
            cur = conn.execute(sql.replace("?", "%s"), params or None)
            return list(cur.fetchall()) if cur.description else []
    with _lock:
        cur = _sqlite_conn().execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        _sqlite_conn().commit()
        return rows


def execute(sql: str, params: tuple = ()) -> int:
    if IS_PG:
        with get_pool().connection() as conn:
            return conn.execute(sql.replace("?", "%s"), params or None).rowcount
    with _lock:
        cur = _sqlite_conn().execute(sql, params)
        _sqlite_conn().commit()
        return cur.rowcount


def init() -> None:
    for stmt in SCHEMA:
        execute(stmt)


def get_checkpointer():
    """LangGraph short-term memory store (keyed by thread_id)."""
    if IS_PG:
        from langgraph.checkpoint.postgres import PostgresSaver
        saver = PostgresSaver(get_pool())
        saver.setup()
        return saver
    from langgraph.checkpoint.sqlite import SqliteSaver
    conn = sqlite3.connect(SQLITE_PATH + ".ckpt", check_same_thread=False)
    return SqliteSaver(conn)
