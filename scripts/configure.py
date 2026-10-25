"""Interactive private local configuration. Never paste provider keys into chat."""
import argparse
import getpass
import os
from pathlib import Path
from dotenv import set_key

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "model": [("OPENAI_API_KEY", True), ("OPENAI_MODEL", False)],
    "keenable": [("KEENABLE_API_KEY", True)],
    "tavily": [("TAVILY_API_KEY", True)],
    "stripe": [("STRIPE_SECRET_KEY", True)],
    "plaid": [("PLAID_CLIENT_ID", True), ("PLAID_SECRET", True), ("PLAID_ACCESS_TOKEN", True)],
    "twilio": [("TWILIO_ACCOUNT_SID", True), ("TWILIO_AUTH_TOKEN", True), ("TWILIO_FROM_NUMBER", False), ("TWILIO_TEST_RECIPIENT", False), ("ENABLE_SMS", False)],
    "bank": [("BANK_REVIEW_THRESHOLD_CENTS", False)],
}


def main():
    parser = argparse.ArgumentParser(description="Save optional provider settings privately in this project's .env")
    parser.add_argument("provider", nargs="?", choices=GROUPS)
    args = parser.parse_args()
    group = args.provider or input("Provider (model / keenable / tavily / stripe / plaid / twilio / bank): ").strip()
    if group not in GROUPS:
        raise SystemExit("Unknown provider")
    print("Leave any field blank to keep its current setting. Secret inputs are hidden.")
    if group == "twilio":
        print("ENABLE_SMS=true enables an explicit app button that can send billable SMS to the designated test recipient.")
    path = ROOT / ".env"
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    path.chmod(0o600)
    for key, secret in GROUPS[group]:
        value = (getpass.getpass if secret else input)(key + ": ").strip()
        if not value:
            continue
        if key == "STRIPE_SECRET_KEY" and not value.startswith(("sk_test_", "rk_test_")):
            print("Rejected: only Stripe test keys are supported.")
            continue
        if key == "BANK_REVIEW_THRESHOLD_CENTS" and (not value.isdigit() or int(value) < 1):
            print("Rejected: enter a positive integer in cents.")
            continue
        if key == "ENABLE_SMS" and value not in ("true", "false"):
            print("Rejected: enter true or false.")
            continue
        set_key(path, key, value)
        if group in ("keenable", "tavily"):
            set_key(path, "SEARCH_PROVIDER", group)
        path.chmod(0o600)
    print("Saved privately. Restart the local server to apply the settings.")


if __name__ == "__main__":
    main()
