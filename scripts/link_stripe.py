"""Link an existing test charge, or explicitly create a fictional test-card charge."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from resolution.evidence import Corpus
from resolution.integrations import StripeSandbox, request, ProviderError
from resolution.store import ROOT, Store


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("charge_id", nargs="?")
    parser.add_argument("--create-test-charge", action="store_true", help="Create a Stripe sandbox charge using tok_visa; no real card or money")
    args = parser.parse_args()
    corpus, stripe = Corpus(), StripeSandbox()
    if args.case_id not in corpus.cases:
        raise SystemExit("Unknown fictional case")
    case = corpus.cases[args.case_id]
    store = Store()
    if store.actions(args.case_id):
        raise SystemExit("This case already has action history. Use a fresh demonstration workspace to avoid mixing processor histories.")
    if args.create_test_charge:
        if args.charge_id:
            raise SystemExit("Choose an existing charge or create one, not both")
        result = request("POST", stripe.base + "/charges", headers=stripe.headers("rr-demo-charge-"+args.case_id),
                         data={"amount": case["amount_cents"], "currency": "usd", "source": "tok_visa",
                               "metadata[case_id]": case["id"], "description": "Fictional refund project test payment"})
        charge_id = result["id"]
    else:
        charge_id = args.charge_id
    stripe.charge(charge_id, case)
    with store.connect() as c:
        c.execute("INSERT INTO links VALUES (?,?) ON CONFLICT(case_id) DO UPDATE SET charge_id=excluded.charge_id", (case["id"], charge_id))
    print("Validated and linked a Stripe test charge to", case["id"])


if __name__ == "__main__":
    try:
        main()
    except ProviderError as e:
        raise SystemExit(str(e))
