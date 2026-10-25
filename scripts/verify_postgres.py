"""Verify restart/resume in a new disposable private schema, never the app schema."""
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
import psycopg
from psycopg import sql
from resolution import database
from resolution.evidence import Corpus
from resolution.store import Store
from resolution.workflow import Engine


def phase(name, schema, run_id=None):
    database.SCHEMA = schema
    engine = Engine(Store(), Corpus())
    try:
        if name == "prepare":
            rid = engine.create("CASE-1007", "telecom_supervisor", fault="once")
            r = engine.process(rid)
            assert r["status"] == "awaiting_approval"
            assert engine.checkpointer.get(engine.config(rid))
            print(rid)
        else:
            r = engine.store.run(run_id)
            assert r["status"] == "awaiting_approval"
            r = engine.decide(run_id, r["revision"], "approve", "telecom_supervisor", "Restart verification")
            assert r["status"] == "bank_review"
            task = r["state"]["bank_task"]
            engine.bank.decide(task["id"], task["revision"], "approve", "bank_reviewer", "Verified")
            assert engine.reconcile("CASE-1007")["posted_cents"] == 74900
            assert len(engine.store.messages("CASE-1007")) == 2
            print("resume, bank posting and customer notifications verified")
    finally:
        engine.close()


def main():
    load_dotenv(".env.render")
    os.environ["SEARCH_PROVIDER"] = "local"
    if len(sys.argv) > 1:
        phase(*sys.argv[1:])
        return
    schema = "resolution_verify_" + uuid4().hex[:12]
    try:
        one = subprocess.run([sys.executable, __file__, "prepare", schema], capture_output=True, text=True)
        if one.returncode:
            raise RuntimeError("Preparation failed: " + one.stderr[-1800:])
        rid = one.stdout.strip()
        two = subprocess.run([sys.executable, __file__, "resume", schema, rid], capture_output=True, text=True)
        if two.returncode:
            raise RuntimeError("Restart verification failed: " + two.stderr[-1800:])
        with psycopg.connect(os.environ["DATABASE_URL"], sslmode="require", prepare_threshold=None) as c:
            for role in ("anon", "authenticated"):
                assert not c.execute("SELECT has_schema_privilege(%s,%s,'USAGE')", (role, schema)).fetchone()[0]
        print(json.dumps({"postgres_restart_resume": "passed", "bank_posting": "passed", "customer_notifications": "passed",
                          "anonymous_schema_access": "denied", "external_provider_calls": 0}))
    finally:
        with psycopg.connect(os.environ["DATABASE_URL"], sslmode="require", prepare_threshold=None) as c:
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    main()
