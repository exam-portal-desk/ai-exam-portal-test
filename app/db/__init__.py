"""
app/db/__init__.py
Provider-independent PostgreSQL access. Works against Supabase Postgres,
a local Postgres install, or any other Postgres server — only DATABASE_URL
changes, application code never knows which provider hosts the database.
"""

from contextlib import contextmanager
from datetime import date, datetime, time
import psycopg2
from psycopg2 import pool as _pg_pool
from psycopg2.extras import RealDictCursor, Json, execute_values
import app.config as config

# Match the previous Supabase/PostgREST JSON behaviour: numeric columns
# come back as float, not Decimal (which jsonify() can't serialize).
psycopg2.extensions.register_type(psycopg2.extensions.new_type(
    psycopg2.extensions.DECIMAL.values, "DEC2FLOAT",
    lambda value, curs: float(value) if value is not None else None,
))

# Python dicts passed as query params (e.g. Notes jsonb columns) adapt to JSON.
psycopg2.extensions.register_adapter(dict, Json)

_pool = _pg_pool.ThreadedConnectionPool(
    config.DB_POOL_MIN, config.DB_POOL_MAX, config.DATABASE_URL
)


def _normalize_row(row: dict) -> dict:
    """Match the previous Supabase/PostgREST JSON shape: date/time values
    come back as ISO strings, not Python objects — existing code throughout
    the app treats timestamps as strings (slicing, splitting, direct JSON
    serialization to the frontend)."""
    return {
        k: v.isoformat() if isinstance(v, (datetime, date, time)) else v
        for k, v in row.items()
    }


@contextmanager
def _cursor(commit=False):
    conn = _pool.getconn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            yield cur
            if commit:
                conn.commit()
        finally:
            cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


@contextmanager
def transaction():
    """Yield a RealDictCursor for a multi-statement transaction: every
    statement run against it via cur.execute(...)/cur.fetchall() shares one
    connection and commits together at the end, or rolls back together if
    any statement raises. Use this (instead of the fetch_one/fetch_all/
    execute helpers, which each open and commit their own connection) for
    operations — like full account deletion — that must be all-or-nothing.
    Rows come back as plain RealDictRow; callers that need the ISO-string
    date normalization fetch_all() applies should call dict(row) plus
    _normalize_row(dict(row)) themselves if they serialize dates to JSON."""
    conn = _pool.getconn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            yield cur
            conn.commit()
        finally:
            cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def fetch_one(query, params=None):
    with _cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return _normalize_row(row) if row else None


def fetch_all(query, params=None):
    with _cursor() as cur:
        cur.execute(query, params)
        return [_normalize_row(r) for r in cur.fetchall()]


def execute(query, params=None):
    """Run an INSERT/UPDATE/DELETE with no RETURNING; returns affected row count."""
    with _cursor(commit=True) as cur:
        cur.execute(query, params)
        return cur.rowcount


def execute_returning(query, params=None):
    """Run a write query with RETURNING; returns list of dicts."""
    with _cursor(commit=True) as cur:
        cur.execute(query, params)
        return [_normalize_row(r) for r in cur.fetchall()]


def set_clause(data: dict):
    """Build a parameterized 'col1=%s, col2=%s' SET clause from a dict."""
    cols = list(data.keys())
    return ", ".join(f"{c}=%s" for c in cols), [data[c] for c in cols]


def insert_returning(table: str, data: dict):
    """INSERT one row from a dict, RETURNING the full row."""
    cols = list(data.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    query = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *"
    rows = execute_returning(query, [data[c] for c in cols])
    return rows[0] if rows else None


def insert_many(table: str, rows: list):
    """Bulk INSERT a list of same-shaped dicts; returns inserted row count.

    PERFORMANCE: uses psycopg2.extras.execute_values(), NOT cur.executemany()
    — executemany() is a well-known psycopg2 gotcha: despite the name, it
    does not batch anything into one statement, it just issues one INSERT
    per row under the hood. Against a remote database (this app's is a
    Supabase instance) that means one full network round trip PER ROW —
    measured directly: a 2,000-row bulk insert (a realistic large CSV
    import) took 64 SECONDS before this fix. execute_values() sends one
    real multi-row INSERT per page_size-row chunk instead, cutting a
    2,000-row insert to ~4 round trips. page_size=500 balances "few round
    trips" against "not one unbounded statement" for very large imports.
    """
    if not rows:
        return 0
    cols = list(rows[0].keys())
    query = f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s"
    values = [[r[c] for c in cols] for r in rows]
    with _cursor(commit=True) as cur:
        execute_values(cur, query, values, page_size=500)
        return len(rows)
