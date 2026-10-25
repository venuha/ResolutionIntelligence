import json
import pytest
from resolution.store import Store
from resolution.evidence import Corpus
from resolution.workflow import Engine


@pytest.fixture
def engine(tmp_path):
    e = Engine(Store(tmp_path), Corpus())
    yield e
    e.close()


def investigate(e, cid="CASE-1007", **kwargs):
    rid = e.create(cid, "telecom_specialist", **kwargs)
    return e.process(rid)


def approve(e, run):
    return e.decide(run["id"], run["revision"], "approve", "telecom_supervisor", "Evidence reviewed.")


def test_web_search_outage_retains_local_procedure_and_approval_gate(engine, monkeypatch):
    from resolution.integrations import ProviderError

    monkeypatch.setenv("KEENABLE_API_KEY", "keen_example")
    calls = []

    def unavailable(method, url, **kwargs):
        calls.append(url)
        raise ProviderError("Web search unavailable")

    monkeypatch.setattr("resolution.integrations.request", unavailable)
    r = investigate(engine, "CASE-2001")
    assert calls == ["https://api.keenable.ai/v1/search"]
    assert r["status"] == "awaiting_approval"
    assert r["state"]["policy"]["mode"] == "local_procedure"
    assert "unavailable" in r["state"]["policy"]["note"]
    assert not engine.store.actions("CASE-2001")


def test_full_journey_requires_separate_bank_approval(engine):
    r = investigate(engine)
    assert r["status"] == "awaiting_approval"
    assert not engine.store.actions("CASE-1007")
    assert not engine.store.messages("CASE-1007")
    r = approve(engine, r)
    assert r["status"] == "bank_review"
    task = r["state"]["bank_task"]
    assert task["data"]["amount_cents"] == 74900
    assert task["data"]["payment_id"] == "PAY-0007"
    assert task["data"]["device_id"] == "DEV-88007"
    assert engine.reconcile("CASE-1007")["posted_cents"] == 0
    engine.bank.decide(task["id"], task["revision"], "approve", "bank_reviewer", "Matched purchase.")
    assert engine.reconcile("CASE-1007")["posted_cents"] == 74900
    messages = engine.store.messages("CASE-1007")
    assert len(messages) == 2
    assert "not yet confirmed" in messages[0]["body"]
    assert "posted" in messages[1]["body"]


def test_small_matched_refund_auto_posts(engine):
    r = approve(engine, investigate(engine, "CASE-2001"))
    assert r["status"] == "bank_posted"
    assert r["state"]["bank_task"]["data"]["reviewer"] == "bank_agent"
    assert engine.reconcile("CASE-2001")["posted_cents"] == 14900


def test_partial_payment_only_proposes_approved_remainder(engine):
    r = investigate(engine, "CASE-2002")
    assert r["state"]["proposal"]["amount_cents"] == 30000
    r = approve(engine, r)
    assert engine.reconcile("CASE-2002")["posted_cents"] == 50000
    assert engine.reconcile("CASE-2002")["submitted_cents"] == 50000


def test_conflicting_bank_records_do_not_authorize_refund(engine):
    r = investigate(engine, "CASE-2003")
    assert r["state"]["proposal"]["kind"] == "investigate"
    assert r["state"]["proposal"]["amount_cents"] == 0
    assert r["state"]["reconciliation"]["input_conflicts"]


def test_provisional_credit_never_closes_refund(engine):
    r = investigate(engine, "CASE-2004")
    assert r["state"]["proposal"]["kind"] == "trace"
    assert r["state"]["reconciliation"]["posted_cents"] == 0
    assert r["state"]["reconciliation"]["provisional_cents"] == 30000


def test_submitted_refund_routes_to_trace_not_second_payment(engine):
    r = approve(engine, investigate(engine, "CASE-1002"))
    task = r["state"]["bank_task"]
    assert task["data"]["purpose"] == "trace"
    with pytest.raises(ValueError, match="cannot create credit"):
        engine.bank.decide(task["id"], 1, "approve", "bank_reviewer", "")


@pytest.mark.parametrize("case_id", ["CASE-1001", "CASE-1005", "CASE-1006"])
def test_missing_or_mismatched_return_blocks_refund(engine, case_id):
    r = investigate(engine, case_id)
    assert r["state"]["proposal"]["kind"] == "warehouse"


def test_tool_timeout_retries_then_recovers(engine):
    r = investigate(engine, fault="once")
    assert r["status"] == "awaiting_approval"
    assert r["state"]["attempts"] == 2
    assert any("timed out" in e["title"] for e in r["state"]["trace"])


def test_tool_outage_exhausts_budget_without_action(engine):
    r = investigate(engine, fault="always")
    assert r["status"] == "needs_information"
    assert r["state"]["attempts"] == 2
    assert not engine.store.actions("CASE-1007")


def test_pause_survives_process_restart(tmp_path):
    e = Engine(Store(tmp_path), Corpus())
    r = investigate(e)
    e.close()
    e2 = Engine(Store(tmp_path), Corpus())
    try:
        assert approve(e2, e2.store.run(r["id"]))["status"] == "bank_review"
    finally:
        e2.close()


def test_duplicate_telecom_approval_cannot_repeat_refund(engine):
    r = investigate(engine)
    approve(engine, r)
    with pytest.raises(ValueError):
        approve(engine, r)
    assert len(engine.store.actions("CASE-1007")) == 1


def test_duplicate_bank_approval_cannot_repeat_credit(engine):
    r = approve(engine, investigate(engine))
    t = r["state"]["bank_task"]
    engine.bank.decide(t["id"], 1, "approve", "bank_reviewer", "")
    with pytest.raises(ValueError):
        engine.bank.decide(t["id"], 1, "approve", "bank_reviewer", "")
    assert len([x for x in engine.bank.ledger("CASE-1007") if x["kind"] == "merchant_refund"]) == 1


def test_case_evidence_change_invalidates_approval(engine):
    r = investigate(engine)
    next(d for d in engine.corpus.documents if d["id"] == "CASE-1007-approval")["facts"]["approved_cents"] = 100
    r = approve(engine, r)
    assert r["status"] == "needs_information"
    assert not engine.store.actions("CASE-1007")


def test_bank_mismatch_cannot_be_overridden_by_reviewer(engine):
    case = engine.corpus.cases["CASE-2001"]
    t = engine.bank.receive("test", case, 14900, "MISMATCH", incoming={"card_token": "wrong_card"})
    assert t["status"] == "needs_review"
    assert not t["data"]["checks"]["card_matches"]
    with pytest.raises(ValueError, match="validation failed"):
        engine.bank.decide(t["id"], 1, "approve", "bank_reviewer", "")


def test_replayed_action_receipt_does_not_duplicate(engine):
    r = approve(engine, investigate(engine))
    execution = engine.execute(r["state"])
    assert execution["execution"]["reference"] == r["state"]["execution"]["reference"]
    engine.notify({**r["state"], **execution})
    assert len(engine.store.actions("CASE-1007")) == 1
    assert len(engine.bank.tasks(["CASE-1007"])) == 1
    assert len(engine.store.messages("CASE-1007")) == 1


def test_rejection_has_no_write_to_processor_bank_or_customer(engine):
    r = investigate(engine)
    r = engine.decide(r["id"], r["revision"], "reject", "telecom_supervisor", "Need clearer evidence")
    assert r["status"] == "rejected"
    assert not engine.store.actions("CASE-1007")
    assert not engine.bank.tasks(["CASE-1007"])
    assert not engine.store.messages("CASE-1007")


def test_search_cannot_return_other_case(engine):
    rows = engine.corpus.search("CASE-1007", "CASE-1001 refund")
    assert rows and all(r["source_id"].startswith("CASE-1007") for r in rows)


def test_unsupported_model_output_is_blocked(monkeypatch, engine):
    from resolution.agents import Models
    monkeypatch.setattr(Models, "assign", lambda *a: {"next_agent": "evidence", "task": "Find evidence"})
    monkeypatch.setattr(Models, "choose_tool", lambda *a: {"tool": "finish"})
    monkeypatch.setattr(Models, "specialist", lambda *a: {"claims": [{"section": "action", "text": "The bank paid everything", "source_ids": ["CASE-1007-statement"]}], "missing_evidence": []})
    monkeypatch.setattr(Models, "review", lambda *a: {"verdict": "revise", "issues": ["Bank payment not supported"], "supported_claims": []})
    r = investigate(engine, models=True)
    assert r["status"] == "review_blocked"
    assert r["state"]["repairs"] == 1
    assert not r["state"]["drafts"]
    assert not engine.store.actions("CASE-1007")


def test_model_selected_tool_cannot_read_other_case(monkeypatch, engine):
    from resolution.agents import Models
    calls = iter([{"tool": "read_source", "source_id": "CASE-1001-order"}, {"tool": "finish"}])
    monkeypatch.setattr(Models, "choose_tool", lambda *a: next(calls))
    result = engine.followup_tools({"case_id": "CASE-1007", "models": True, "trace": []}, "evidence")
    assert result["tool_observations"][0]["result"] == {"error": "Source is not in the permitted case."}


def test_external_sandbox_bank_credit_always_requires_review(engine):
    t = engine.bank.receive("test", engine.corpus.cases["CASE-2001"], 14900, "re_test", origin="stripe_sandbox")
    assert t["status"] == "needs_review"
    assert engine.reconcile("CASE-2001")["posted_cents"] == 0
