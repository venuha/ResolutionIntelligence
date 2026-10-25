"""Local durable state. SQLite and graph checkpoints are private, ignored files."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from . import database

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Store:
    def __init__(self, directory=None):
        self.database_url = os.getenv("DATABASE_URL", "") if directory is None else ""
        database.initialize(self.database_url)
        self.directory = Path(directory or os.getenv("REFUND_WORK_DIR", ROOT / "work"))
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "operations.sqlite"
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, case_id TEXT NOT NULL, owner TEXT NOT NULL,
              status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
              created TEXT NOT NULL, updated TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS actions (
              id TEXT PRIMARY KEY, case_id TEXT NOT NULL, run_id TEXT NOT NULL,
              kind TEXT NOT NULL, amount INTEGER NOT NULL, status TEXT NOT NULL,
              provider TEXT NOT NULL, reference TEXT, payload TEXT NOT NULL,
              created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, case_id TEXT NOT NULL, run_id TEXT NOT NULL,
              body TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL,
              created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, identity TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS links (
              case_id TEXT PRIMARY KEY, charge_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bank_tasks (
              id TEXT PRIMARY KEY, case_id TEXT NOT NULL, run_id TEXT NOT NULL,
              status TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
              created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bank_ledger (
              id TEXT PRIMARY KEY, case_id TEXT NOT NULL, kind TEXT NOT NULL,
              amount INTEGER NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS login_attempts (
              id TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires REAL NOT NULL);
            """)
        if not self.database_url:
            self.path.chmod(0o600)

    def connect(self):
        return database.connect(self.path, self.database_url)

    def create_run(self, run_id, case_id, owner, state):
        with self.connect() as c:
            c.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                      (run_id, case_id, owner, "investigating", 1, now(), now(), json.dumps(state)))

    def save_run(self, run_id, state, status=None):
        with self.connect() as c:
            c.execute("UPDATE runs SET state=?,status=?,updated=?,revision=revision+1 WHERE id=?",
                      (json.dumps(state), status or state.get("status", "investigating"), now(), run_id))

    def run(self, run_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError("Investigation not found")
        return {**dict(row), "state": json.loads(row["state"])}

    def runs(self, case_ids):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM runs ORDER BY created DESC").fetchall()
        return [{**dict(r), "state": json.loads(r["state"])} for r in rows if r["case_id"] in case_ids]

    def actions(self, case_id):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM actions WHERE case_id=? ORDER BY created", (case_id,)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def action(self, action_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        return dict(row) if row else None

    def reserve_action(self, run_id, case_id, proposal, provider):
        aid = proposal["id"]
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO actions VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (aid, case_id, run_id, proposal["kind"], proposal["amount_cents"],
                       "submitting", provider, None, json.dumps(proposal), now()))
        return aid

    def action_result(self, aid, status, reference):
        with self.connect() as c:
            c.execute("UPDATE actions SET status=?,reference=? WHERE id=?", (status, reference, aid))

    def notify(self, run_id, case_id, body, channel="portal", status="published"):
        mid = fingerprint([run_id, channel, body])[:24]
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?)",
                      (mid, case_id, run_id, body, channel, status, now()))
        return mid

    def messages(self, case_id):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM messages WHERE case_id=? ORDER BY created", (case_id,))]

    def charge(self, case_id):
        with self.connect() as c:
            r = c.execute("SELECT charge_id FROM links WHERE case_id=?", (case_id,)).fetchone()
        return r["charge_id"] if r else None
