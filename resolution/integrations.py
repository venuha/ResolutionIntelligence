"""Optional provider adapters. Credentials never enter graph state or HTTP responses."""
import os
from urllib.parse import urlparse
import httpx


class ProviderError(RuntimeError):
    """A sanitized, retryable provider failure."""


def request(method, url, **kwargs):
    try:
        response = httpx.request(method, url, timeout=20, **kwargs)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise ProviderError("Provider request failed; check configuration or retry.") from None


class StripeSandbox:
    base = "https://api.stripe.com/v1"

    def headers(self, action_id=None):
        key = os.getenv("STRIPE_SECRET_KEY", "")
        if not key.startswith(("sk_test_", "rk_test_")):
            raise ProviderError("A Stripe test key is required. Live keys are rejected.")
        headers = {"Authorization": f"Bearer {key}"}
        if action_id:
            headers["Idempotency-Key"] = action_id
        return headers

    def charge(self, charge_id, case):
        if not charge_id or not charge_id.startswith("ch_"):
            raise ProviderError("Link a Stripe test charge to this case first.")
        result = request("GET", f"{self.base}/charges/{charge_id}", headers=self.headers())
        if (result.get("livemode") is not False or result.get("metadata", {}).get("case_id") != case["id"]
                or result.get("currency") != "usd" or result.get("amount") != case["amount_cents"]
                or not result.get("paid") or result.get("disputed")):
            raise ProviderError("Test charge does not match the case, amount, currency or payment requirements.")
        return {"charge_id": result["id"], "captured_cents": result["amount"],
                "submitted_cents": result["amount_refunded"], "provider": "stripe_sandbox"}

    def refund(self, charge_id, amount, action_id, case):
        # Re-read the charge immediately before execution. Stripe also enforces the refundable balance.
        current = self.charge(charge_id, case)
        if amount <= 0 or amount > current["captured_cents"] - current["submitted_cents"]:
            raise ProviderError("Refundable balance changed. Investigate again before approval.")
        result = request("POST", f"{self.base}/refunds", headers=self.headers(action_id), data={
            "charge": charge_id, "amount": amount, "metadata[case_id]": case["id"],
            "metadata[action_id]": action_id})
        if (result.get("amount") != amount or result.get("charge") != charge_id
                or result.get("currency") != "usd"):
            raise ProviderError("Unexpected refund response. Reconcile before retrying.")
        return {"reference": result["id"], "status": result["status"]}

    def status(self, reference):
        if not reference.startswith("re_"):
            raise ProviderError("Invalid refund reference")
        result = request("GET", f"{self.base}/refunds/{reference}", headers=self.headers())
        return {"reference": result["id"], "status": result["status"], "amount_cents": result["amount"],
                "case_id": result.get("metadata", {}).get("case_id")}

    def find_refund(self, charge_id, case, action_id):
        self.charge(charge_id, case)
        cursor = None
        for _ in range(10):
            params = {"charge": charge_id, "limit": 100}
            if cursor:
                params["starting_after"] = cursor
            page = request("GET", f"{self.base}/refunds", headers=self.headers(), params=params)
            rows = page.get("data", [])
            matches = [r for r in rows if r.get("metadata", {}).get("action_id") == action_id
                       and r.get("metadata", {}).get("case_id") == case["id"] and r.get("charge") == charge_id]
            if len(matches) > 1:
                raise ProviderError("Multiple matching provider refunds require manual reconciliation.")
            if matches:
                r = matches[0]
                return {"reference": r["id"], "status": r["status"], "amount_cents": r["amount"], "case_id": case["id"]}
            if not page.get("has_more"):
                return None
            if not rows:
                break
            cursor = rows[-1]["id"]
        raise ProviderError("Refund lookup pagination limit reached. The outcome remains uncertain.")


SEARCH_KEYS = {"keenable": "KEENABLE_API_KEY", "tavily": "TAVILY_API_KEY"}
GUIDANCE_DOMAINS = ("docs.stripe.com", "plaid.com")
GUIDANCE_QUERY = "refund status pending submitted bank posting refund reference tracing"


def search_provider():
    selected = os.getenv("SEARCH_PROVIDER", "auto").strip().lower()
    if selected == "auto":
        return next((name for name, key in SEARCH_KEYS.items() if os.getenv(key)), "local")
    return selected


def guidance_sources(result, text_fields):
    if not isinstance(result, dict) or not isinstance(result.get("results"), list):
        raise ProviderError("Web search returned an unexpected response; local procedure retained.")
    sources = []
    for item in result["results"]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        try:
            url = urlparse(item["url"])
            host = (url.hostname or "").lower()
        except ValueError:
            continue
        if (url.scheme != "https" or url.username or url.password
                or not any(host == d or host.endswith("." + d) for d in GUIDANCE_DOMAINS)):
            continue
        excerpt = next((item[k] for k in text_fields if isinstance(item.get(k), str) and item[k].strip()), "")
        if not excerpt:
            continue
        title = item.get("title")
        sources.append({"title": title[:300] if isinstance(title, str) and title else "Provider guidance",
                        "url": item["url"], "text": excerpt[:2000]})
    return sources


def research_policy():
    provider = search_provider()
    if provider == "local":
        return {"mode": "local_procedure", "sources": [], "note": "Using the versioned fictional telecom procedure."}
    if provider not in SEARCH_KEYS:
        raise ProviderError("SEARCH_PROVIDER must be auto, keenable, tavily or local.")
    key = os.getenv(SEARCH_KEYS[provider], "")
    if not key:
        raise ProviderError("The selected web search provider is not configured.")
    # Generic query only. No case IDs, names, account details or customer text sent to web search.
    sources = []
    if provider == "keenable":
        # Keenable's documented site filter accepts one domain per request.
        for domain in GUIDANCE_DOMAINS:
            result = request("POST", "https://api.keenable.ai/v1/search", headers={"X-API-Key": key}, json={
                "query": GUIDANCE_QUERY, "site": domain, "mode": "realtime",
                "max_results": 2, "snippet_max_length": 2000})
            sources.extend(guidance_sources(result, ("snippet", "description"))[:2])
    else:
        result = request("POST", "https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"}, json={
            "query": GUIDANCE_QUERY, "include_domains": list(GUIDANCE_DOMAINS),
            "include_answer": False, "max_results": 3})
        sources = guidance_sources(result, ("content",))[:3]
    sources = list({source["url"]: source for source in sources}.values())
    name = "Keenable" if provider == "keenable" else "Tavily"
    note = (f"{name}: {len(sources)} official-provider source(s) retrieved. Public guidance does not establish "
            "case events or telecom refund eligibility." if sources else
            f"{name}: no usable official-provider excerpts found; versioned demo procedure retained.")
    return {"mode": provider, "sources": sources, "note": note}


def plaid_candidates():
    env = [os.getenv(k) for k in ("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ACCESS_TOKEN")]
    if not all(env):
        raise ProviderError("Plaid sandbox credentials are not configured.")
    base = dict(zip(("client_id", "secret", "access_token"), env))
    rows, cursor = [], None
    for _ in range(10):
        result = request("POST", "https://sandbox.plaid.com/transactions/sync",
                         json={**base, **({"cursor": cursor} if cursor else {})})
        rows.extend({"transaction_id": x["transaction_id"], "name": x.get("name"),
                     "date": x["date"], "amount": x["amount"], "pending": x["pending"]}
                    for x in result.get("added", []))
        cursor = result["next_cursor"]
        if not result.get("has_more"):
            return {"environment": "sandbox", "candidates": rows[:100],
                    "note": "Unmatched sandbox transactions. These do not verify any case refund or mirror Stripe."}
    raise ProviderError("Plaid pagination limit reached. No case posting was inferred.")


def send_test_sms(body, action_id):
    if os.getenv("ENABLE_SMS") != "true":
        raise ProviderError("SMS is disabled. Customer portal updates are available.")
    sid, token, sender, recipient = (os.getenv(x) for x in (
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "TWILIO_TEST_RECIPIENT"))
    if not all((sid, token, sender, recipient)):
        raise ProviderError("Configure Twilio and a designated test recipient privately first.")
    result = request("POST", f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                     auth=(sid, token), data={"From": sender, "To": recipient, "Body": body})
    return {"reference": result["sid"], "status": result["status"]}


def configuration():
    provider = search_provider()
    return {
        "model": {"configured": bool(os.getenv("OPENAI_API_KEY")), "name": os.getenv("OPENAI_MODEL", "gpt-4.1-mini")},
        "search": {"provider": provider, "configured": bool(os.getenv(SEARCH_KEYS.get(provider, "")))},
        "keenable": {"configured": bool(os.getenv("KEENABLE_API_KEY"))},
        "tavily": {"configured": bool(os.getenv("TAVILY_API_KEY"))},
        "stripe": {"configured": os.getenv("STRIPE_SECRET_KEY", "").startswith(("sk_test_", "rk_test_"))},
        "plaid": {"configured": all(os.getenv(k) for k in ("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ACCESS_TOKEN"))},
        "twilio": {"configured": all(os.getenv(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "TWILIO_TEST_RECIPIENT")),
                   "enabled": os.getenv("ENABLE_SMS") == "true"},
    }
