"""Case state derived from source facts, never from a generated summary."""

from decimal import Decimal


def money(cents):
    if cents is None:
        return "Restricted"
    return f"USD {Decimal(cents) / 100:,.2f}"


def investigate(case, docs):
    records = {d["kind"]: d for d in docs}

    def facts(kind):
        return records.get(kind, {}).get("facts", {})

    def cite(*kinds):
        return [records[k]["id"] for k in kinds if k in records]

    amount = facts("return").get("return_requested_cents", 0)
    approved = facts("approval").get("approved_cents", 0)
    submitted = facts("refund").get("submitted_cents", 0)
    posted = facts("statement").get(
        "posted_cents", facts("bank_status").get("posted_cents", 0)
    )
    provisional = facts("statement").get("provisional_cents", 0)
    inspection = facts("inspection")
    shipping = facts("shipping")
    mismatch = bool(
        inspection
        and inspection.get("expected_device") != inspection.get("scanned_device")
    )
    timeline = []
    for key, label in [
        ("order", "Order placed"),
        ("payment", "Payment captured"),
        ("return", "Return authorized"),
        ("shipping", "Carrier delivery"),
        ("inspection", "Device verified"),
        ("approval", "Refund approved"),
        ("refund", "Refund submitted"),
        ("statement", "Refund posted"),
    ]:
        r = records.get(key)
        if key == "statement" and r is None:
            r = records.get("bank_status")
        valid = bool(r)
        if key == "shipping":
            valid = shipping.get("delivered", False)
        if key == "inspection":
            valid = inspection.get("accepted", False)
        if key == "statement":
            valid = posted >= amount and amount > 0
        timeline.append(
            dict(
                label=label,
                verified=valid,
                date=r["date"] if valid else None,
                source_id=r["id"] if r else None,
            )
        )
    if posted >= amount and amount > 0:
        status = "resolved"
        headline = "The matching refund appears in the supplied statement."
        owner = "Case review"
        action = "Confirm the customer recognizes the credit before closing the case."
        evidence = cite("refund", "statement")
    elif mismatch:
        status = "blocked"
        headline = "Warehouse device identifiers do not match."
        owner = "Return reconciliation"
        action = "Reconcile expected and scanned device identifiers against intake images and shipment records."
        evidence = cite("order", "inspection")
    elif not shipping.get("carrier_accepted"):
        status = "waiting"
        headline = "The label exists; carrier acceptance is not evidenced."
        owner = "Return logistics"
        action = "Obtain the carrier acceptance receipt or tracking update before treating the device as received."
        evidence = cite("return", "shipping")
    elif shipping.get("delivered") and not inspection:
        status = "blocked"
        headline = "Delivery is confirmed. Warehouse verification is missing."
        owner = "Return reconciliation"
        action = "Locate the intake scan and reconcile the delivered package with the return authorization."
        evidence = cite("shipping", "return")
    elif approved and submitted < approved:
        status = "blocked"
        headline = "Refund approval is recorded; processor submission is not evidenced."
        owner = "Payments operations"
        action = "Obtain the processor submission result or investigate the missing submission evidence."
        evidence = cite("approval")
    elif posted > 0 and posted < amount:
        status = "blocked"
        headline = "A partial refund posted; the remaining return is unresolved."
        owner = "Return reconciliation"
        action = (
            "Locate the second device inspection and determine its refund eligibility."
        )
        evidence = cite("inspection", "statement", "refund")
    elif submitted:
        status = "tracing"
        headline = "A refund was submitted; bank posting is not evidenced."
        owner = "Payments operations"
        action = "Verify processor outcome and use an available trace reference to prepare a bank handoff."
        evidence = cite("refund", "statement")
    else:
        status = "waiting"
        headline = "The available records do not establish refund completion."
        owner = "Case review"
        action = "Request the missing approval, submission, and statement evidence."
        evidence = cite("return", "statement")
    shared = facts("bank_status")
    if shared and "statement" not in records:
        headline = shared["summary"]
        owner = shared["next_owner"]
        action = "Request: " + shared["required_information"]
        evidence = cite("bank_status")
        status = "resolved" if shared["status"] == "posted" else "tracing"
    conflicts = []
    if facts("conversation").get("refund_claimed_complete") and posted < amount:
        conflicts.append(
            dict(
                text="A support note claims completion, but the supplied records do not establish refund submission or posting.",
                source_ids=cite("conversation", "statement"),
            )
        )
    if mismatch:
        conflicts.append(
            dict(
                text="The scanned device identifier differs from the order device identifier.",
                source_ids=cite("order", "inspection"),
            )
        )
    if provisional:
        conflicts.append(
            dict(
                text="Temporary dispute credit is present. It must not be counted as a posted merchant refund.",
                source_ids=cite("statement"),
            )
        )
    milestones = [x for x in timeline if x["verified"]]
    return dict(
        status=status,
        headline=headline,
        owner=owner,
        next_action=action,
        source_ids=evidence,
        requested_cents=amount,
        approved_cents=approved,
        submitted_cents=submitted,
        posted_cents=posted,
        provisional_cents=provisional,
        unconfirmed_cents=max(amount - posted, 0),
        bank=facts("bank_status"),
        timeline=timeline,
        conflicts=conflicts,
        last_verified=milestones[-1]["label"] if milestones else "Unknown",
        coverage="Only documents permitted for your role are included. Missing or restricted records remain unknown. Supplied bank evidence covers only its account and review window.",
        source_count=len(docs),
        synthetic=True,
    )
