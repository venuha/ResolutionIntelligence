"""Fresh held-out-style scenarios, distinct from the Week 2 development corpus."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "data"
cases = json.loads((root / "cases.json").read_text())
docs = json.loads((root / "documents.json").read_text())
cases = [c for c in cases if not c["id"].startswith("CASE-2")]
docs = [d for d in docs if not (d.get("case_id") or "").startswith("CASE-2")]
for num, name, amount, submitted, posted, provisional, label in [
    (2001, "Morgan Lee", 14900, 0, 0, 0, "Approved accessory return"),
    (2002, "Jamie Patel", 50000, 20000, 20000, 0, "Partial payment · $300 remaining"),
    (2003, "Drew Wilson", 50000, 20000, 20000, 0, "Contradictory bank evidence"),
    (2004, "Robin Shaw", 30000, 30000, 0, 30000, "Provisional credit · not a refund"),
]:
    cid = f"CASE-{num}"
    case = dict(id=cid, customer=name, initials="".join(n[0] for n in name.split()),
                order_id=f"ORD-{num}", device_id=f"DEV-{num}", scenario="fresh", amount_cents=amount,
                currency="USD", age_days=8, contacts=2, label=label, opened="2026-10-16", snapshot="2026-10-24",
                product="Continuum device · demo edition")
    cases.append(case)
    facts = {
        "order": {"purchase_cents": amount, "device_id": case["device_id"]},
        "payment": {"captured_cents": amount},
        "return": {"return_requested_cents": amount, "label_created": True},
        "shipping": {"carrier_accepted": True, "delivered": True},
        "inspection": {"expected_device": case["device_id"], "scanned_device": case["device_id"], "accepted": True, "accepted_cents": amount},
        "approval": {"approved_cents": amount},
        "statement": {"posted_cents": posted, "provisional_cents": provisional, "window_end": "2026-10-24"},
        "conversation": {"refund_claimed_complete": num in (2002, 2003)},
        "bank_status": {"posted_cents": 0 if num == 2003 else posted, "status": "review_pending",
                        "summary": "The supplied records require review.", "next_owner": "Bank review",
                        "required_information": "Original payment and refund reference", "order_id": case["order_id"]},
    }
    if submitted:
        facts["refund"] = {"submitted_cents": submitted, "trace_reference": f"DEMO-TRACE-{num}"}
    for kind, values in facts.items():
        title = kind.replace("_", " ").title()
        text = f"{title} for {cid}. Order {case['order_id']}. Device {case['device_id']}.\n\n" + "\n".join(f"{k}: {v}" for k, v in values.items())
        if kind == "conversation" and values["refund_claimed_complete"]:
            text += "\nSupport agent said the refund was complete. This author report is contradicted by the partial payment records."
        docs.append(dict(id=f"{cid}-{kind}", case_id=cid, kind=kind, title=title,
                         date="2026-10-24", source="Fresh synthetic test record", synthetic=True, text=text, facts=values))
(root / "cases.json").write_text(json.dumps(cases, indent=2))
(root / "documents.json").write_text(json.dumps(docs, indent=2))
