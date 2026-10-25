"""Local-only fictional demonstration; role identities are intentionally selectable."""
import hashlib
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .bank import threshold
from .evidence import Corpus, POLICY
from .integrations import ProviderError, StripeSandbox, configuration, plaid_candidates, send_test_sms
from .store import ROOT, Store, fingerprint
from .workflow import Engine
from .hosting import hosted, validate_settings, check_password

load_dotenv(ROOT / ".env")
# Telemetry is opt-in; do not export synthetic case traces by default.
os.environ.setdefault("LANGSMITH_TRACING", "false")

IDENTITIES = {
    "telecom_supervisor": {"name": "Telecom supervisor", "role": "supervisor", "cases": "all"},
    "telecom_specialist": {"name": "Case specialist", "role": "specialist", "cases": ["CASE-1001", "CASE-1002", "CASE-1007", "CASE-2001", "CASE-2002"]},
    "bank_reviewer": {"name": "Bank reviewer", "role": "bank", "cases": "all"},
    "customer_riley": {"name": "Riley Quinn · customer", "role": "customer", "cases": ["CASE-1007"]},
    "customer_morgan": {"name": "Morgan Lee · customer", "role": "customer", "cases": ["CASE-2001"]},
    "customer_jamie": {"name": "Jamie Patel · customer", "role": "customer", "cases": ["CASE-2002"]},
}


class Login(BaseModel):
    identity: str
    password: str = Field(default="", max_length=512)


class StartRun(BaseModel):
    case_id: str
    mode: Literal["demo", "stripe_sandbox"] = "demo"
    models: bool = False
    fault: Literal["none", "once", "always"] = "none"


class Decision(BaseModel):
    revision: int
    decision: Literal["approve", "reject", "request_information"]
    notes: str = Field(default="", max_length=1000)


def create_app(directory=None, testing=False):
    validate_settings(testing)
    is_hosted = hosted()
    store, corpus = Store(directory), Corpus()
    engine = Engine(store, corpus)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.close()

    app = FastAPI(title="Refund Resolution Command Center", lifespan=lifespan)
    app.state.engine, app.state.store, app.state.corpus = engine, store, corpus
    hosts = ["localhost", "127.0.0.1", "[::1]"] + (["testserver"] if testing else [])
    public_host = os.getenv("RENDER_EXTERNAL_HOSTNAME") or urlsplit(os.getenv("REFUND_PUBLIC_URL", "")).hostname
    if public_host:
        hosts.append(public_host)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware("http")
    async def local_boundary(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            expected_origin = (os.getenv("REFUND_PUBLIC_URL") or ("https://" + public_host if public_host else "")) if is_hosted else str(request.base_url).rstrip("/")
            if origin and origin.rstrip("/") != expected_origin.rstrip("/"):
                return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site writes are not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if is_hosted:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Record not found"}, status_code=404)

    @app.exception_handler(ProviderError)
    async def provider(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    def principal(request: Request):
        token = request.cookies.get("rr_session", "")
        with store.connect() as c:
            row = c.execute("SELECT identity FROM sessions WHERE token_hash=? AND expires>?",
                            (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not row:
            raise HTTPException(401, "Sign in to the demo workspace to continue")
        identity = row["identity"]
        p = {"id": identity, **IDENTITIES[identity]}
        p["cases"] = list(corpus.cases) if p["cases"] == "all" else p["cases"]
        return p

    def allowed(p, roles, case_id=None):
        if p["role"] not in roles or case_id and case_id not in p["cases"]:
            raise HTTPException(403, "Your demo role is not assigned to this action or case")

    def public_run(row):
        if not row["state"].get("review", {}).get("passed"):
            row["state"]["drafts"] = {}
        return row

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"identities": [{"id": k, **v} for k, v in IDENTITIES.items()], "local_demo": not is_hosted,
                "password_required": is_hosted}

    @app.get("/health")
    def health():
        with store.connect() as c:
            c.execute("SELECT 1")
        return {"status": "ok", "storage": "postgres" if store.database_url else "sqlite", "hosted": is_hosted}

    @app.post("/api/login")
    def login(body: Login, response: Response, request: Request):
        if is_hosted:
            check_password(store, request, body.password)
        elif not testing and request.client.host not in ("127.0.0.1", "::1"):
            raise HTTPException(403, "This fictional demo is local-only")
        if body.identity not in IDENTITIES:
            raise HTTPException(400, "Unknown identity")
        token = secrets.token_urlsafe(32)
        with store.connect() as c:
            c.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            c.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), body.identity, time.time()+28800))
        response.set_cookie("rr_session", token, httponly=True, samesite="strict", max_age=28800, secure=is_hosted)
        return {"ok": True}

    @app.post("/api/perspective")
    def perspective(body: Login, request: Request, p=Depends(principal)):
        # An authenticated demo operator may explore each fictional perspective.
        # This is a private portfolio demonstration, not employee role provisioning.
        if body.identity not in IDENTITIES:
            raise HTTPException(400, "Unknown identity")
        with store.connect() as c:
            c.execute("UPDATE sessions SET identity=? WHERE token_hash=?",
                      (body.identity, hashlib.sha256(request.cookies["rr_session"].encode()).hexdigest()))
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        with store.connect() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(request.cookies.get("rr_session", "").encode()).hexdigest(),))
        response.delete_cookie("rr_session", secure=is_hosted, httponly=True, samesite="strict")
        return {"ok": True}

    @app.get("/api/me")
    def me(p=Depends(principal)):
        return {**p, "bank_threshold_cents": threshold(), "configuration": configuration() if p["role"] != "customer" else {}}

    @app.get("/api/cases")
    def cases(p=Depends(principal)):
        return [{**corpus.cases[c], "messages": len(store.messages(c)),
                 "run_status": next((r["status"] for r in store.runs([c])), "not_started")}
                for c in p["cases"]]

    @app.get("/api/cases/{case_id}")
    def case(case_id: str, p=Depends(principal)):
        allowed(p, ("supervisor", "specialist", "bank", "customer"), case_id)
        result = {"case": corpus.cases[case_id], "messages": store.messages(case_id)}
        if p["role"] != "customer":
            result.update(documents=corpus.docs(case_id), reconciliation=engine.reconcile(case_id),
                          ledger=engine.bank.ledger(case_id), actions=store.actions(case_id),
                          runs=[public_run(r) for r in store.runs([case_id])], bank_tasks=engine.bank.tasks([case_id]), policy=POLICY)
        else:
            result["ledger"] = engine.bank.ledger(case_id)
        return result

    @app.get("/api/runs")
    def runs(p=Depends(principal)):
        allowed(p, ("supervisor", "specialist"))
        return [public_run(r) for r in store.runs(p["cases"])]

    def safe_process(run_id):
        try:
            engine.process(run_id)
        except Exception:
            # Detailed state contains no secrets; the UI receives the saved sanitized error.
            pass

    @app.post("/api/runs")
    def start(body: StartRun, background: BackgroundTasks, p=Depends(principal)):
        allowed(p, ("supervisor", "specialist"), body.case_id)
        if body.models and not configuration()["model"]["configured"]:
            raise HTTPException(400, "Configure a model key privately first, or use the labeled demo mode")
        if body.mode == "stripe_sandbox" and not configuration()["stripe"]["configured"]:
            raise HTTPException(400, "Configure and link a Stripe test charge first")
        rid = engine.create(body.case_id, p["id"], body.mode, body.models, body.fault)
        background.add_task(safe_process, rid)
        return {"id": rid}

    @app.get("/api/runs/{run_id}")
    def run(run_id: str, p=Depends(principal)):
        result = store.run(run_id)
        allowed(p, ("supervisor", "specialist"), result["case_id"])
        # Generated drafts are withheld until whole-output review passes.
        if not result["state"].get("review", {}).get("passed"):
            result["state"]["drafts"] = {}
        return result

    @app.post("/api/runs/{run_id}/decision")
    def decide(run_id: str, body: Decision, p=Depends(principal)):
        row = store.run(run_id)
        allowed(p, ("supervisor",), row["case_id"])
        return engine.decide(run_id, body.revision, body.decision, p["id"], body.notes)

    @app.post("/api/runs/{run_id}/continue")
    def resume(run_id: str, background: BackgroundTasks, p=Depends(principal)):
        row = store.run(run_id)
        allowed(p, ("supervisor", "specialist"), row["case_id"])
        if row["status"] not in ("error", "investigating"):
            raise HTTPException(409, "Only interrupted investigations can resume here")
        background.add_task(safe_process, run_id)
        return {"ok": True}

    @app.get("/api/bank")
    def bank_tasks(p=Depends(principal)):
        allowed(p, ("bank", "supervisor"))
        return {"tasks": engine.bank.tasks(p["cases"]), "threshold_cents": threshold(), "environment": "bank_simulation"}

    @app.post("/api/bank/{task_id}/decision")
    def bank_decide(task_id: str, body: Decision, p=Depends(principal)):
        row = engine.bank.task(task_id)
        allowed(p, ("bank",), row["case_id"])
        with engine.lock:
            result = engine.bank.decide(task_id, body.revision, body.decision, p["id"], body.notes)
            run = store.run(row["run_id"])
            state = {**run["state"], "bank_task": result, "status": "bank_posted" if result["status"] == "posted" else "bank_review"}
            store.save_run(row["run_id"], state)
        return result

    @app.post("/api/plaid/inspect")
    def plaid(p=Depends(principal)):
        allowed(p, ("bank",))
        return plaid_candidates()

    @app.post("/api/runs/{run_id}/refresh-provider")
    def refresh(run_id: str, p=Depends(principal)):
        row = store.run(run_id)
        allowed(p, ("supervisor",), row["case_id"])
        s = row["state"]
        if s["mode"] != "stripe_sandbox" or not s.get("execution") or s.get("proposal", {}).get("kind") != "refund":
            raise HTTPException(409, "No Stripe refund submission is available")
        with engine.lock:
            reference = s["execution"].get("reference", "")
            if reference.startswith("re_"):
                result = StripeSandbox().status(reference)
            else:
                result = StripeSandbox().find_refund(store.charge(row["case_id"]), corpus.cases[row["case_id"]], s["proposal"]["id"])
                if not result:
                    raise HTTPException(409, "No matching provider receipt was found. The outcome remains uncertain; no new refund was issued.")
            if result["case_id"] != row["case_id"] or result["amount_cents"] != s["proposal"]["amount_cents"]:
                raise HTTPException(409, "Provider result does not match the approved case")
            store.action_result(s["proposal"]["id"], result["status"], result["reference"])
            s["execution"].update(result)
            update = engine.notify(s)
            store.save_run(run_id, {**s, **update})
        return store.run(run_id)

    @app.post("/api/runs/{run_id}/send-test-sms")
    def sms(run_id: str, p=Depends(principal)):
        row = store.run(run_id)
        allowed(p, ("supervisor",), row["case_id"])
        messages = [m for m in store.messages(row["case_id"]) if m["run_id"] == run_id and m["channel"] == "portal"]
        if not messages:
            raise HTTPException(409, "Publish an approved customer update first")
        body = messages[-1]["body"]
        aid = "sms:" + fingerprint([run_id, body])[:24]
        with engine.lock:
            if store.action(aid):
                raise HTTPException(409, "This test message was already attempted. Inspect the provider before retrying.")
            store.reserve_action(run_id, row["case_id"], {"id": aid, "kind": "sms", "amount_cents": 0}, "twilio")
            try:
                result = send_test_sms(body, aid)
            except ProviderError:
                store.action_result(aid, "unknown", None)
                raise
            store.action_result(aid, result["status"], result["reference"])
            store.notify(run_id, row["case_id"], body, "sms", result["status"])
        return result

    @app.get("/")
    def index():
        return FileResponse(ROOT / "resolution/static/index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "resolution/static"), name="static")
    return app


app = create_app()
