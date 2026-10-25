"""Private Supabase persistence; local development keeps SQLite."""
import os
import sqlite3
from contextlib import contextmanager

SCHEMA = "resolution_private"


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        if sql.strip().upper() == "BEGIN IMMEDIATE":
            # Serialize bank validation/posting across overlapping deployments.
            return self.connection.execute("SELECT pg_advisory_xact_lock(7249103301)")
        sql = sql.replace("?", "%s").replace(" REAL", " DOUBLE PRECISION")
        if "INSERT OR IGNORE INTO" in sql:
            sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO").rstrip("; ") + " ON CONFLICT DO NOTHING"
        return self.connection.execute(sql, parameters)

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)


def initialize(url):
    if not url:
        return
    import psycopg
    with psycopg.connect(url, sslmode="require", prepare_threshold=None, connect_timeout=10) as c:
        c.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        c.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC, anon, authenticated")
        c.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated")


@contextmanager
def connect(path, url):
    if url:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(url, sslmode="require", prepare_threshold=None,
                             connect_timeout=10, row_factory=dict_row) as c:
            c.execute(f"SET LOCAL search_path TO {SCHEMA}")
            yield PostgresConnection(c)
    else:
        c = sqlite3.connect(path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()


def postgres_checkpointer(url):
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    # A session pooler (5432) preserves the schema and supports checkpointer pipelines.
    def configure(c):
        c.execute(f"SET search_path TO {SCHEMA}")

    pool = ConnectionPool(url, min_size=1, max_size=1, timeout=15,
                         kwargs={"autocommit": True, "prepare_threshold": None,
                                 "row_factory": dict_row, "sslmode": "require", "connect_timeout": 10},
                         configure=configure, check=ConnectionPool.check_connection)
    try:
        pool.wait(timeout=15)
        saver = PostgresSaver(pool)
        saver.setup()
        return pool, saver
    except Exception:
        pool.close()
        raise RuntimeError("Supabase checkpoint initialization failed; verify the session-pooler configuration.") from None
