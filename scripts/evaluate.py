"""Separate arithmetic, action, workflow and model-quality measurements."""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resolution.evidence import Corpus
from resolution.store import ROOT, Store
from resolution.workflow import Engine


def main():
    expected = [
        {"case": "CASE-2001", "posted": 0, "provisional": 0, "unconfirmed": 14900, "action": "refund", "amount": 14900},
        {"case": "CASE-2002", "posted": 20000, "provisional": 0, "unconfirmed": 30000, "action": "refund", "amount": 30000},
        {"case": "CASE-2003", "posted": 20000, "provisional": 0, "unconfirmed": 30000, "action": "investigate", "amount": 0},
        {"case": "CASE-2004", "posted": 0, "provisional": 30000, "unconfirmed": 30000, "action": "trace", "amount": 0},
    ]
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        e = Engine(Store(directory), Corpus())
        for x in expected:
            rid = e.create(x["case"], "evaluation")
            result = e.process(rid)
            state = result["state"]
            r, p = state["reconciliation"], state["proposal"]
            arithmetic = all(r[k] == x[v] for k, v in (("posted_cents", "posted"), ("provisional_cents", "provisional"), ("unconfirmed_cents", "unconfirmed")))
            action = p["kind"] == x["action"] and p["amount_cents"] == x["amount"]
            approved = e.decide(rid, result["revision"], "approve", "telecom_supervisor", "Evaluation of fictional flow")
            if x["action"] == "refund":
                workflow = approved["status"] == "bank_posted" and e.reconcile(x["case"])["unconfirmed_cents"] == 0
            elif x["action"] == "trace":
                workflow = approved["status"] == "bank_review" and e.reconcile(x["case"])["posted_cents"] == 0
            else:
                workflow = approved["status"] == "action_created" and all(a["kind"] != "refund" for a in e.store.actions(x["case"]))
            rows.append({"case_id": x["case"], "calculation_correct": arithmetic,
                         "action_justified": action, "workflow_correct": workflow,
                         "input_conflict_detected": bool(r["input_conflicts"]),
                         "explanation_faithfulness": None})
        e.close()
    old = json.loads((ROOT / "data/week2-evaluation.json").read_text())
    old_rows = [{"configuration": config, **row} for config, results in old["results"].items() for row in results]
    low = [r for r in old_rows if isinstance(r.get("faithfulness"), (float, int)) and r["faithfulness"] < .5]
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "environment": "rule_based_demo",
        "fresh_case_count": len(rows),
        "calculation_accuracy": sum(r["calculation_correct"] for r in rows) / len(rows),
        "action_justification": sum(r["action_justified"] for r in rows) / len(rows),
        "workflow_completion": sum(r["workflow_correct"] for r in rows) / len(rows),
        "model_explanation_faithfulness": None,
        "model_evaluation_status": "not_run_requires_provider_key",
        "external_api_verification": "not_run_requires_provider_credentials",
        "historical_week2": {"rows": len(old_rows), "rows_below_0_5": len(low),
                             "all_low_rows_for_review": [{k: r[k] for k in ("configuration", "case_id", "question", "faithfulness", "answer")} for r in low]},
        "cases": rows,
        "limitations": ["Four fresh synthetic scenarios, not a production or statistical benchmark.",
                        "Deterministic evaluation cannot establish LLM faithfulness.",
                        "Historical low-score rows are retained for whole-output review; old judge contexts were not replayed."]}
    path = ROOT / "docs/evaluation.json"
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ("historical_week2", "cases")}, indent=2))
    if not all(r["calculation_correct"] and r["action_justified"] and r["workflow_correct"] for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
