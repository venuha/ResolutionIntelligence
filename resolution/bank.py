"""Fictional receiving-bank agent and ledger. Never represents a live bank connection."""
import json
import os
import re
from .store import fingerprint, now


def threshold():
    return max(1, int(os.getenv("BANK_REVIEW_THRESHOLD_CENTS", "50000")))


class Bank:
    def __init__(self, store, corpus):
        self.store, self.corpus = store, corpus
        self.seed()

    def seed(self):
        with self.store.connect() as c:
            for case in self.corpus.cases.values():
                source_docs = {d["kind"]: d for d in self.corpus.docs(case["id"])}
                records = {kind: d.get("facts", {}) for kind, d in source_docs.items()}
                payment_text = next((d["text"] for d in self.corpus.docs(case["id"]) if d["kind"] == "payment"), "")
                payment_match = re.search(r"\bPAY-\d+\b", payment_text)
                base = {"case_id": case["id"], "order_id": case["order_id"], "device_id": case["device_id"],
                        "card_token": "card_demo_" + case["id"], "card_last4": "4242", "currency": "USD",
                        "merchant": "Continuum Telecom (fictional)", "source": "Week 2 synthetic snapshot",
                        "payment_id": payment_match.group() if payment_match else "PAY-" + case["id"].split("-")[1], "environment": "bank_simulation"}
                items = [("purchase", records.get("payment", {}).get("captured_cents", 0)),
                         ("merchant_refund", records.get("statement", records.get("bank_status", {})).get("posted_cents", 0)),
                         ("provisional_credit", records.get("statement", {}).get("provisional_cents", 0))]
                for kind, amount in items:
                    if amount:
                        source = source_docs.get("payment" if kind == "purchase" else "statement", source_docs.get("bank_status", {}))
                        source_date = source.get("date", case["snapshot"])
                        c.execute("INSERT INTO bank_ledger VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET created=excluded.created,data=excluded.data",
                                  (f"seed:{case['id']}:{kind}", case["id"], kind, amount, json.dumps(base), source_date))

    def ledger(self, case_id):
        with self.store.connect() as c:
            return [{**dict(r), "data": json.loads(r["data"])} for r in c.execute(
                "SELECT * FROM bank_ledger WHERE case_id=? ORDER BY created", (case_id,))]

    def tasks(self, case_ids):
        with self.store.connect() as c:
            return [{**dict(r), "data": json.loads(r["data"])} for r in c.execute(
                "SELECT * FROM bank_tasks ORDER BY created DESC") if r["case_id"] in case_ids]

    def task(self, task_id):
        with self.store.connect() as c:
            row = c.execute("SELECT * FROM bank_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise KeyError("Bank task not found")
        return {**dict(row), "data": json.loads(row["data"])}

    def receive(self, run_id, case, amount, reference, purpose="incoming_refund", origin="demo", incoming=None):
        tid = fingerprint([case["id"], reference, purpose])[:24]
        ledger = self.ledger(case["id"])
        purchase = next(x for x in ledger if x["kind"] == "purchase")
        posted = sum(x["amount"] for x in ledger if x["kind"] == "merchant_refund")
        packet = {**purchase["data"], "order_id": case["order_id"], "device_id": case["device_id"], **(incoming or {})}
        original = purchase["data"]
        data = {**packet, "amount_cents": amount, "refund_reference": reference,
                "purpose": purpose, "origin": origin, "threshold_cents": threshold(),
                "checks": {"original_purchase_found": packet["payment_id"] == original["payment_id"],
                           "order_matches": packet["order_id"] == original["order_id"],
                           "device_matches": packet["device_id"] == original["device_id"],
                           "card_matches": packet["card_token"] == original["card_token"],
                           "currency_matches": packet["currency"] == original["currency"],
                           "positive_amount": amount > 0, "within_remaining_purchase": 0 < amount <= purchase["amount"] - posted},
                "notes": "Bank simulation, explicitly linked by our scenario adapter. No real bank notification or posting.",
                "reviewer": None}
        status = "needs_review"
        if purpose == "trace":
            data["reason"] = "Existing refund requires evidence of bank posting; a trace request cannot create credit."
        elif origin != "demo":
            data["reason"] = "External sandbox result needs explicit simulated-bank review; it does not synchronize with Plaid."
        elif not all(data["checks"].values()):
            data["reason"] = "Matching or amount checks failed."
        elif amount >= threshold():
            data["reason"] = f"Refund meets the fictional human-review threshold of ${threshold()/100:,.2f}."
        else:
            status = "auto_ready"
            data["reason"] = "All scenario checks passed and the amount is below the fictional threshold."
        with self.store.connect() as c:
            c.execute("INSERT OR IGNORE INTO bank_tasks VALUES (?,?,?,?,?,?,?,?)",
                      (tid, case["id"], run_id, status, 1, json.dumps(data), now(), now()))
        result = self.task(tid)
        if result["status"] == "auto_ready":
            self.decide(tid, 1, "approve", "bank_agent", "Automatic simulated match under the demo threshold.", automated=True)
        return self.task(tid)

    def decide(self, task_id, revision, decision, reviewer, notes, automated=False):
        # One SQLite transaction validates and posts. Duplicate approvals cannot double-credit.
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM bank_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError("Bank task not found")
            data = json.loads(row["data"])
            if row["revision"] != revision or row["status"] not in ("needs_review", "auto_ready"):
                raise ValueError("This bank task changed or was already processed. Refresh before reviewing.")
            status = "rejected" if decision == "reject" else "information_required" if decision == "request_information" else "posted"
            if status == "posted":
                if data["purpose"] == "trace":
                    raise ValueError("A trace request cannot create credit. Request information or reject it.")
                if automated and (data["amount_cents"] >= threshold() or data["origin"] != "demo"):
                    raise ValueError("Human bank review required")
                rows = c.execute("SELECT * FROM bank_ledger WHERE case_id=?", (row["case_id"],)).fetchall()
                purchase = next(x for x in rows if x["kind"] == "purchase")
                original = json.loads(purchase["data"])
                identity_ok = all(data[k] == original[k] for k in ("case_id", "order_id", "device_id", "card_token", "currency", "payment_id"))
                posted = sum(x["amount"] for x in rows if x["kind"] == "merchant_refund")
                if not identity_ok or not all(data["checks"].values()) or not 0 < data["amount_cents"] <= purchase["amount"] - posted:
                    raise ValueError("Bank validation failed. Request information; approval cannot override mismatches or excess credit.")
                c.execute("INSERT INTO bank_ledger VALUES (?,?,?,?,?,?)",
                          ("refund:" + task_id, row["case_id"], "merchant_refund", data["amount_cents"], json.dumps(data), now()))
            data.update(reviewer=reviewer, review_notes=notes)
            c.execute("UPDATE bank_tasks SET status=?,revision=revision+1,data=?,updated=? WHERE id=?",
                      (status, json.dumps(data), now(), task_id))
            if status == "posted":
                body = f"Demo bank confirmation: a ${data['amount_cents']/100:,.2f} merchant refund was posted to your simulated card ending {data['card_last4']}. Reference: {data['refund_reference']}."
            elif status == "information_required":
                body = "The simulated bank reviewer requested more information. Your refund is still under review."
            else:
                body = "The simulated bank reviewer did not approve this request. The telecom team must review the case."
            mid = fingerprint([task_id, status])[:24]
            c.execute("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?)",
                      (mid, row["case_id"], row["run_id"], body, "portal", "published", now()))
        return self.task(task_id)
