"""Bounded orchestration, durable human interrupts and idempotent action execution."""
import copy
import json
import sqlite3
import threading
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agents import Models
from .bank import Bank
from .evidence import POLICY
from .integrations import ProviderError, StripeSandbox, research_policy
from .legacy_reconciliation import investigate
from .store import fingerprint, now
from .database import postgres_checkpointer
import os


class State(TypedDict, total=False):
    run_id: str
    case_id: str
    mode: str
    models: bool
    fault: str
    status: str
    completed: list
    next_agent: str
    trace: list
    evidence: list
    policy: dict
    reconciliation: dict
    drafts: dict
    review: dict
    proposal: dict
    decision: dict
    execution: dict
    bank_task: dict
    errors: list
    attempts: int
    repairs: int
    basis: str
    tool_observations: list


def event(state, actor, title, detail="", kind="success"):
    return {"trace": state.get("trace", []) + [{"actor": actor, "title": title, "detail": detail,
                                               "kind": kind, "at": now()}]}


class Engine:
    def __init__(self, store, corpus):
        self.store, self.corpus = store, corpus
        self.bank = Bank(store, corpus)
        self.lock = threading.RLock()
        if store.database_url:
            self.checkpoint_connection, self.checkpointer = postgres_checkpointer(os.environ["CHECKPOINT_DATABASE_URL"])
        else:
            self.checkpoint_connection = sqlite3.connect(store.directory / "checkpoints.sqlite", check_same_thread=False)
            self.checkpointer = SqliteSaver(self.checkpoint_connection)
        self.graph = self.build()
        if not store.database_url:
            (store.directory / "checkpoints.sqlite").chmod(0o600)

    def close(self):
        self.checkpoint_connection.close()

    def config(self, run_id):
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 40}

    def create(self, case_id, owner, mode="demo", models=False, fault="none"):
        with self.lock:
            financial_actions = [a for a in self.store.actions(case_id) if a["kind"] == "refund"]
            if any(a["provider"] != mode for a in financial_actions):
                raise ValueError("This case already has a different processor history. Use a fresh workspace for a different integration mode.")
            active = [r for r in self.store.runs([case_id]) if r["status"] in ("investigating", "awaiting_approval", "execution_unknown")]
            if active:
                raise ValueError("This case already has an active investigation. Open it to continue.")
            run_id = uuid4().hex
            state = dict(run_id=run_id, case_id=case_id, mode=mode, models=models, fault=fault,
                         status="investigating", completed=[], trace=[], drafts={}, errors=[], attempts=0, repairs=0)
            self.store.create_run(run_id, case_id, owner, state)
            return run_id

    def process(self, run_id, resume=None):
        with self.lock:
            run = self.store.run(run_id)
            config = self.config(run_id)
            snapshot = self.graph.get_state(config)
            payload = Command(resume=resume) if resume else (None if snapshot.values else run["state"])
            try:
                self.graph.invoke(payload, config)
                state = dict(self.graph.get_state(config).values)
                snapshot = self.graph.get_state(config)
                if snapshot.next and snapshot.next[0] == "approval":
                    state["status"] = "awaiting_approval"
                self.store.save_run(run_id, state)
            except Exception:
                state = dict(self.graph.get_state(config).values) or run["state"]
                state.update(status="error", **event(state, "system", "Investigation paused", "An unexpected error occurred. Resume from the saved checkpoint.", "error"))
                self.store.save_run(run_id, state)
                raise
            return self.store.run(run_id)

    def decide(self, run_id, revision, decision, reviewer, notes):
        with self.lock:
            run = self.store.run(run_id)
            if run["revision"] != revision or run["status"] != "awaiting_approval":
                raise ValueError("This proposal changed or was already reviewed. Refresh it.")
            return self.process(run_id, {"decision": decision, "reviewer": reviewer, "notes": notes,
                                         "proposal_hash": fingerprint(run["state"]["proposal"]), "at": now()})

    def context(self, s):
        return {"case": self.corpus.cases[s["case_id"]], "sources": self.corpus.docs(s["case_id"]),
                "procedure": POLICY, "reconciliation": s.get("reconciliation"),
                "proposal": s.get("proposal"), "public_guidance": s.get("policy"),
                "ledger": self.bank.ledger(s["case_id"]), "tool_observations": s.get("tool_observations", []),
                "review_feedback": s.get("review", {}).get("issues", [])}

    def followup_tools(self, s, role):
        """Three model-selected, read-only tool calls maximum, scoped by the server."""
        if not s["models"]:
            return {}
        available = ["search_evidence", "read_source"] if role == "evidence" else ["warehouse_lookup", "bank_ledger", "refund_reference", "read_source"]
        docs = self.corpus.docs(s["case_id"])
        by_id = {d["id"]: d for d in docs}
        observations = list(s.get("tool_observations", []))
        current = {**s}
        seen = set()
        for step in range(3):
            try:
                choice = Models(True).choose_tool(role, available,
                    {"case_id": s["case_id"], "sources": [{"id": d["id"], "title": d["title"]} for d in docs],
                     "reconciliation": s.get("reconciliation"), "observations": observations, "steps_remaining": 3-step})
                name = choice["tool"]
                if name == "finish":
                    break
                key = fingerprint(choice)
                if name not in available or key in seen:
                    break
                seen.add(key)
                if name == "search_evidence":
                    result = self.corpus.search(s["case_id"], choice.get("query") or "refund status", 5)
                elif name == "read_source":
                    if choice.get("source_id") not in by_id:
                        result = {"error": "Source is not in the permitted case."}
                    else:
                        result = by_id[choice["source_id"]]
                elif name == "warehouse_lookup":
                    result = [d for d in docs if d["kind"] in ("shipping", "inspection")]
                elif name == "bank_ledger":
                    result = self.bank.ledger(s["case_id"])
                else:
                    result = [d for d in docs if d["kind"] in ("refund", "bank_status")]
                observations.append({"agent": role, "tool": name, "result": result})
                current.update(event(current, role, "Called " + name.replace("_", " "), "Read-only follow-up selected by the model; scoped to this case."))
            except Exception:
                current.update(event(current, role, "Follow-up tool selection unavailable", "Required deterministic checks still run; model output will need review.", "warning"))
                return {"tool_observations": observations, "trace": current.get("trace", []),
                        "errors": s.get("errors", []) + [role + ": follow-up model unavailable."]}
        return {"tool_observations": observations, "trace": current.get("trace", [])}

    def model_draft(self, s, name):
        drafts = dict(s.get("drafts", {}))
        if s["models"]:
            try:
                drafts[name] = Models(True).specialist(name, self.context(s))
            except Exception:
                return {"drafts": drafts, "errors": s.get("errors", []) + [f"{name}: model unavailable; deterministic results retained for human review."]}
        return {"drafts": drafts}

    def reconcile(self, case_id, mode="demo"):
        case = self.corpus.cases[case_id]
        docs = self.corpus.docs(case_id)
        records = {d["kind"]: d.get("facts", {}) for d in docs}
        result = investigate(case, docs)
        # Preserve the Week 2 calculator; extend with current simulated ledger and submitted actions.
        ledger = self.bank.ledger(case_id)
        result["posted_cents"] = sum(x["amount"] for x in ledger if x["kind"] == "merchant_refund")
        result["provisional_cents"] = sum(x["amount"] for x in ledger if x["kind"] == "provisional_credit")
        result["captured_cents"] = records.get("payment", {}).get("captured_cents", 0)
        local_submitted = sum(a["amount"] for a in self.store.actions(case_id)
                              if a["kind"] == "refund" and a["status"] not in ("failed", "canceled") and a["provider"] == "demo")
        result["submitted_cents"] += local_submitted
        if mode == "stripe_sandbox":
            snapshot = StripeSandbox().charge(self.store.charge(case_id), case)
            result["submitted_cents"] = snapshot["submitted_cents"]
            result["processor"] = snapshot
        result["unconfirmed_cents"] = max(result["requested_cents"] - result["posted_cents"], 0)
        result["unsubmitted_approved_cents"] = max(result["approved_cents"] - result["submitted_cents"], 0)
        inspection = records.get("inspection", {})
        result["inspection_matched"] = bool(inspection.get("accepted") and inspection.get("expected_device") == inspection.get("scanned_device"))
        result["carrier_accepted"] = bool(records.get("shipping", {}).get("carrier_accepted"))
        result["input_conflicts"] = []
        stmt, bank = records.get("statement", {}), records.get("bank_status", {})
        if "posted_cents" in stmt and "posted_cents" in bank and stmt["posted_cents"] != bank["posted_cents"]:
            result["input_conflicts"].append("Statement and bank snapshot disagree on the posted amount.")
        if not 0 <= result["posted_cents"] <= result["captured_cents"] or result["approved_cents"] > result["captured_cents"]:
            result["input_conflicts"].append("Recorded financial amounts exceed the original purchase.")
        if any(not 0 <= result[key] <= result["captured_cents"] for key in ("requested_cents", "approved_cents", "submitted_cents", "provisional_cents")):
            result["input_conflicts"].append("Financial input bounds failed; negative or excessive values require review.")
        result["source_ids"] = [d["id"] for d in docs if d["kind"] in ("payment", "return", "inspection", "approval", "refund", "statement", "bank_status")]
        return result

    def basis(self, s, reconciliation):
        return fingerprint([self.corpus.version(s["case_id"]), reconciliation, POLICY["version"], s["mode"]])

    def orchestrator(self, s):
        done = s.get("completed", [])
        available = [x for x in ("evidence", "policy") if x not in done]
        if not available:
            available = ["trace"] if "trace" not in done else ["resolution"]
        chosen, detail = available[0], "Assign the next required investigation step."
        if s["models"] and not s.get("errors"):
            try:
                assignment = Models(True).assign(done, {"available": available, "case_id": s["case_id"], "findings": s.get("reconciliation")})
                if assignment["next_agent"] in available:
                    chosen, detail = assignment["next_agent"], assignment["task"]
            except Exception:
                detail = "Model routing unavailable; required workflow order applied."
        return {"next_agent": chosen, **event(s, "orchestrator", f"Assigned {chosen}", detail)}

    def evidence(self, s):
        evidence = self.corpus.search(s["case_id"], "refund approval submitted posted warehouse device inspection carrier trace partial provisional", 12)
        followups = self.followup_tools(s, "evidence")
        updated = {**s, **followups, "evidence": evidence}
        return {"evidence": evidence, "completed": s["completed"] + ["evidence"],
                **followups,
                **self.model_draft(updated, "evidence"),
                **event(updated, "evidence", "Case evidence retrieved", f"{len(evidence)} scoped passages; full financial records retained for reconciliation.")}

    def policy(self, s):
        try:
            policy = research_policy()
        except ProviderError:
            policy = {"mode": "local_procedure", "sources": [], "note": "Web research unavailable; versioned demo procedure retained."}
        updated = {**s, "policy": policy}
        return {"policy": policy, "completed": s["completed"] + ["policy"],
                **self.model_draft(updated, "policy"), **event(s, "policy", "Procedure checked", policy["note"])}

    def trace(self, s):
        attempts = s.get("attempts", 0) + 1
        if s["fault"] == "always" or (s["fault"] == "once" and attempts == 1):
            return {"attempts": attempts, "next_agent": "retry" if attempts < 2 else "failed",
                    **event(s, "trace", "Processor lookup timed out", "Injected failure: retry budget is two attempts.", "warning")}
        try:
            result = self.reconcile(s["case_id"], s["mode"])
        except ProviderError:
            return {"attempts": attempts, "next_agent": "retry" if attempts < 2 else "failed",
                    **event(s, "trace", "Processor unavailable", "No action will be proposed from an unverified processor state.", "warning")}
        followups = self.followup_tools({**s, "reconciliation": result}, "trace")
        updated = {**s, **followups, "reconciliation": result}
        return {"attempts": attempts, "reconciliation": result, "basis": self.basis(s, result),
                "next_agent": "done", "completed": s["completed"] + ["trace"],
                **followups,
                **self.model_draft(updated, "trace"),
                **event(updated, "trace", "Payment journey reconciled", "Captured, approved, submitted, posted and provisional amounts checked separately.")}

    def tool_failure(self, s):
        return {"status": "needs_information", **event(s, "orchestrator", "Human investigation required", "Required lookup failed twice. Restore the provider and start a fresh investigation.", "warning")}

    def proposal_for(self, s):
        r = s["reconciliation"]
        amount = 0
        if r["input_conflicts"]:
            kind, owner, title = "investigate", "Telecom reconciliation", "Resolve contradictory financial records"
        elif r["posted_cents"] >= r["requested_cents"] > 0:
            kind, owner, title = "confirm", "Customer care", "Confirm the matching credit with the customer"
        elif not r["inspection_matched"]:
            kind, owner, title = "warehouse", "Returns operations", "Request return and device verification"
        elif r["unsubmitted_approved_cents"] > 0:
            kind, owner, title = "refund", "Payments operations", "Submit the remaining approved refund"
            amount = r["unsubmitted_approved_cents"]
        elif r["submitted_cents"] > r["posted_cents"]:
            kind, owner, title = "trace", "Bank review", "Trace the submitted refund"
        else:
            kind, owner, title = "investigate", "Telecom reconciliation", "Request missing approval or return evidence"
        return {"id": fingerprint([s["case_id"], s["basis"], kind, amount])[:32],
                "kind": kind, "owner": owner, "title": title, "amount_cents": amount,
                "source_ids": r["source_ids"], "policy_id": POLICY["id"], "basis": s["basis"],
                "environment": s["mode"], "customer_message": "",
                "notification_scope": "Publish the reviewed portal update and factual simulated bank status updates; SMS requires a separate explicit send."}

    def resolution(self, s):
        proposal = self.proposal_for(s)
        updated = {**s, "proposal": proposal}
        return {"proposal": proposal, **self.model_draft(updated, "resolution"),
                **event(s, "resolution", "Internal action prepared", proposal["title"] + "; awaiting review.")}

    def communication(self, s):
        p = dict(s["proposal"])
        if p["kind"] == "refund":
            message = f"A ${p['amount_cents']/100:,.2f} refund submission is proposed for your returned device. It is awaiting telecom approval. Bank receipt is not yet confirmed."
        elif p["kind"] == "trace":
            message = "A refund submission is recorded. We propose a bank trace because matching credit is not yet fully evidenced in the available records."
        elif p["kind"] == "confirm":
            message = "A matching merchant credit is recorded in the supplied simulated bank evidence. Please confirm you recognize it."
        else:
            message = "Additional return or payment evidence is needed before this case can be resolved. A review request is awaiting telecom approval."
        p["customer_message"] = "Demo update: " + message
        updated = {**s, "proposal": p}
        return {"proposal": p, **self.model_draft(updated, "communications"),
                **event(s, "communications", "Customer update drafted", "Message states the current stage and requires telecom approval.")}

    def ground(self, s):
        expected = self.proposal_for(s)
        issues = []
        p = s["proposal"]
        for key in ("kind", "owner", "amount_cents", "source_ids", "basis"):
            if p[key] != expected[key]:
                issues.append("Proposal differs from the permitted action: " + key)
        issues += s.get("errors", [])
        model_review = None
        if s["models"]:
            allowed_sources = {d["id"] for d in self.corpus.docs(s["case_id"])} | {POLICY["id"]}
            allowed_sources |= {x["id"] for x in self.bank.ledger(s["case_id"])}
            allowed_sources |= {x["url"] for x in s.get("policy", {}).get("sources", [])}
            for draft in s.get("drafts", {}).values():
                for claim in draft.get("claims", []):
                    if not claim.get("source_ids") or not set(claim["source_ids"]) <= allowed_sources:
                        issues.append("Generated claim has missing or out-of-scope source references.")
            try:
                model_review = Models(True).review({"specialists": s.get("drafts", {}), "proposal": p}, self.context(s))
                if model_review["verdict"] != "pass":
                    issues.extend(model_review["issues"] or ["Model reviewer requires human review."])
            except Exception:
                issues.append("Independent model review unavailable. Generated explanations withheld.")
        result = {"passed": not issues, "issues": issues, "mode": "model_and_rules" if s["models"] else "deterministic_demo",
                  "calculation_check": "inputs require review" if s["reconciliation"]["input_conflicts"] else "passed", "model_review": model_review,
                  "faithfulness_score": None,
                  "note": "Rule checks and model review are distinct from measured explanation faithfulness; no score is invented."}
        repair = bool(issues and s["models"] and not s.get("errors") and s.get("repairs", 0) < 1)
        return {"review": result, "next_agent": "repair" if repair else "pass" if not issues else "blocked",
                **event(s, "grounding", "Whole-output review passed" if not issues else "Review requires changes",
                        "Facts, interpretations, action and customer update checked." if not issues else "; ".join(issues),
                        "success" if not issues else "warning")}

    def repair(self, s):
        # One bounded regeneration round. The final checker must still pass.
        return {"repairs": s.get("repairs", 0) + 1, "drafts": {},
                **event(s, "orchestrator", "Requested revised explanation", "Prior generated drafts withheld. One repair round is allowed.")}

    def review_blocked(self, s):
        return {"status": "review_blocked", "drafts": {},
                **event(s, "orchestrator", "Execution blocked by review", "Unsupported or unavailable model output needs investigation. Rule findings remain visible.", "warning")}

    def approval(self, s):
        decision = interrupt({"type": "telecom_approval", "proposal": s["proposal"], "review": s["review"]})
        if decision["proposal_hash"] != fingerprint(s["proposal"]):
            raise ValueError("Approval no longer matches the proposal")
        return {"decision": decision, **event(s, "human", "Telecom " + decision["decision"], decision["notes"])}

    def rejected(self, s):
        return {"status": "rejected" if s["decision"]["decision"] == "reject" else "needs_information",
                **event(s, "orchestrator", "Action held", "No action was executed. Reviewer feedback is saved.", "warning")}

    def execute(self, s):
        p, cid = s["proposal"], s["case_id"]
        old = self.store.action(p["id"])
        if old and old["status"] not in ("submitting", "unknown"):
            return {"execution": {"reference": old["reference"], "status": old["status"], "action_id": p["id"]},
                    **event(s, "execution", "Existing action recovered", "Idempotent replay reused the stored receipt.")}
        if old and s["mode"] == "stripe_sandbox":
            return {"execution": {"status": "unknown", "action_id": p["id"]},
                    **event(s, "execution", "Reconciliation required", "A previous provider submission has an uncertain result. Do not issue another refund.", "warning")}
        try:
            current = self.reconcile(cid, s["mode"])
            if not old and self.basis(s, current) != p["basis"]:
                return {"execution": {"status": "stale"}, **event(s, "execution", "Approval expired", "Evidence or balance changed. Investigate again.", "warning")}
            self.store.reserve_action(s["run_id"], cid, p, s["mode"])
            if p["kind"] == "refund" and s["mode"] == "stripe_sandbox":
                result = StripeSandbox().refund(self.store.charge(cid), p["amount_cents"], p["id"], self.corpus.cases[cid])
            else:
                result = {"reference": ("SIM-REF-" if p["kind"] == "refund" else "TASK-") + p["id"][:10].upper(),
                          "status": "succeeded" if p["kind"] == "refund" else "created"}
            self.store.action_result(p["id"], result["status"], result["reference"])
            return {"execution": {**result, "action_id": p["id"]},
                    **event(s, "execution", "Approved action recorded", result["reference"] + " · " + result["status"])}
        except ProviderError:
            if self.store.action(p["id"]):
                self.store.action_result(p["id"], "unknown", None)
            return {"execution": {"status": "unknown", "action_id": p["id"]},
                    **event(s, "execution", "Provider result uncertain", "Reconcile the provider result before any retry.", "warning")}

    def notify(self, s):
        p, execution = s["proposal"], s["execution"]
        status = execution["status"]
        if status in ("unknown", "stale", "failed", "canceled"):
            return {"status": "execution_unknown" if status == "unknown" else "needs_information"}
        body = p["customer_message"]
        bank_task = None
        if p["kind"] == "refund":
            body = f"Demo update: your ${p['amount_cents']/100:,.2f} refund was submitted to {'the Stripe test processor' if s['mode']=='stripe_sandbox' else 'the simulated processor'}. Reference: {execution['reference']}. Bank posting is not yet confirmed."
        elif p["kind"] == "trace":
            body = "Demo update: a bank-trace request has been created for the recorded refund submission. Matching bank credit is not yet fully evidenced in the available records."
        elif p["kind"] in ("warehouse", "investigate"):
            body = "Demo update: an internal review task has been created to obtain the missing return or payment evidence. Your refund remains under investigation."
        self.store.notify(s["run_id"], s["case_id"], body)
        if p["kind"] == "refund" and status == "succeeded":
            bank_task = self.bank.receive(s["run_id"], self.corpus.cases[s["case_id"]], p["amount_cents"], execution["reference"], origin=s["mode"])
        elif p["kind"] == "trace":
            r = s["reconciliation"]
            bank_task = self.bank.receive(s["run_id"], self.corpus.cases[s["case_id"]], max(r["submitted_cents"] - r["posted_cents"], 0), execution["reference"], "trace", s["mode"])
        return {"status": "bank_posted" if bank_task and bank_task["status"] == "posted" else "bank_review" if bank_task else "action_created",
                "bank_task": bank_task or {},
                **event(s, "communications", "Customer portal updated", "Approved status published. " + ("Bank agent notified in the simulation queue." if bank_task else "No bank receipt inferred."))}

    def build(self):
        graph = StateGraph(State)
        names = ["orchestrator", "evidence", "policy", "trace", "tool_failure", "resolution", "communication",
                 "ground", "repair", "review_blocked", "approval", "rejected", "execute", "notify"]
        for name in names:
            fn = getattr(self, name)
            def wrapped(s, fn=fn):
                result = fn(s)
                self.store.save_run(s["run_id"], {**s, **result})
                return result
            graph.add_node(name, wrapped)
        graph.add_edge(START, "orchestrator")
        graph.add_conditional_edges("orchestrator", lambda s: s["next_agent"], {x: x for x in ("evidence", "policy", "trace", "resolution")})
        for name in ("evidence", "policy"):
            graph.add_edge(name, "orchestrator")
        graph.add_conditional_edges("trace", lambda s: s["next_agent"], {"retry": "trace", "failed": "tool_failure", "done": "orchestrator"})
        graph.add_edge("resolution", "communication")
        graph.add_edge("communication", "ground")
        graph.add_conditional_edges("ground", lambda s: s["next_agent"], {"repair": "repair", "pass": "approval", "blocked": "review_blocked"})
        graph.add_edge("repair", "resolution")
        graph.add_conditional_edges("approval", lambda s: "execute" if s["decision"]["decision"] == "approve" else "rejected")
        graph.add_edge("execute", "notify")
        for name in ("tool_failure", "review_blocked", "rejected", "notify"):
            graph.add_edge(name, END)
        return graph.compile(checkpointer=self.checkpointer)
