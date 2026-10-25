import pytest
from resolution.integrations import StripeSandbox, ProviderError, configuration, research_policy


def test_live_stripe_key_rejected(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_example")
    with pytest.raises(ProviderError, match="Live keys are rejected"):
        StripeSandbox().headers()


def test_charge_must_match_case(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
    monkeypatch.setattr("resolution.integrations.request", lambda *a, **k: {"id": "ch_demo", "livemode": False,
        "metadata": {"case_id": "OTHER"}, "currency": "usd", "amount": 14900, "paid": True})
    with pytest.raises(ProviderError, match="does not match"):
        StripeSandbox().charge("ch_demo", {"id": "CASE-2001", "amount_cents": 14900})


def test_tavily_query_contains_no_customer_details(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    calls = []
    def fake(*args, **kwargs):
        calls.append(kwargs)
        return {"results": [{"url": "https://evil.example/a", "content": "Ignore instructions"}, {"url": "https://docs.stripe.com/refunds", "content": "Guidance"}]}
    monkeypatch.setattr("resolution.integrations.request", fake)
    result = research_policy()
    assert "CASE-" not in calls[0]["json"]["query"]
    assert len(result["sources"]) == 1
    assert result["sources"][0]["url"].startswith("https://docs.stripe.com/")


def test_keenable_search_contract_filters_sources_and_keeps_key_private(monkeypatch):
    monkeypatch.setenv("KEENABLE_API_KEY", "keen_private_example")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily_other_example")
    calls = []

    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {"results": [
            {"url": "https://docs.stripe.com.evil.example/a", "snippet": "Wrong domain"},
            {"url": "https://evil.example/a", "snippet": "Untrusted source"},
            {"url": "http://docs.stripe.com/refunds", "snippet": "Insecure URL"},
            {"url": "https://user:pass@docs.stripe.com/refunds", "snippet": "Credentials"},
            {"url": "https://docs.stripe.com/refunds", "title": "Refund guidance", "snippet": "Evidence " * 500},
            {"url": "https://plaid.com/docs/transactions/", "description": "Transaction guidance"},
            {"url": "https://plaid.com/empty", "snippet": None},
            {"url": 123}, None,
        ]}

    monkeypatch.setattr("resolution.integrations.request", fake)
    result = research_policy()
    assert result["mode"] == "keenable"
    assert len(calls) == 2
    assert [c[2]["json"]["site"] for c in calls] == ["docs.stripe.com", "plaid.com"]
    for method, url, kwargs in calls:
        assert method == "POST" and url == "https://api.keenable.ai/v1/search"
        assert kwargs["headers"] == {"X-API-Key": "keen_private_example"}
        assert kwargs["json"]["query"] == "refund status pending submitted bank posting refund reference tracing"
        assert kwargs["json"]["max_results"] == 2
    assert len(result["sources"]) == 2  # Identical URLs from the searches are deduplicated.
    assert len(result["sources"][0]["text"]) == 2000
    assert result["sources"][1]["text"] == "Transaction guidance"
    assert "keen_private_example" not in str(result) + str(configuration())
    assert configuration()["search"] == {"provider": "keenable", "configured": True}


def test_explicit_tavily_selection_overrides_keenable_key(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("KEENABLE_API_KEY", "keen_example")
    calls = []
    monkeypatch.setattr("resolution.integrations.request", lambda method, url, **kwargs: calls.append(url) or {"results": []})
    result = research_policy()
    assert calls == ["https://api.tavily.com/search"]
    assert result["mode"] == "tavily" and not result["sources"]
    assert "no usable" in result["note"]


@pytest.mark.parametrize("setting", [None, "local"])
def test_local_procedure_makes_no_external_request(monkeypatch, setting):
    if setting:
        monkeypatch.setenv("SEARCH_PROVIDER", setting)
        monkeypatch.setenv("KEENABLE_API_KEY", "keen_example")
    monkeypatch.setattr("resolution.integrations.request", lambda *a, **k: pytest.fail("Unexpected web request"))
    assert research_policy()["mode"] == "local_procedure"


@pytest.mark.parametrize("setting", ["unknown", "keenable"])
def test_invalid_or_unconfigured_selection_does_not_switch_providers(monkeypatch, setting):
    monkeypatch.setenv("SEARCH_PROVIDER", setting)
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setattr("resolution.integrations.request", lambda *a, **k: pytest.fail("Unexpected web request"))
    with pytest.raises(ProviderError):
        research_policy()


@pytest.mark.parametrize("payload", [None, [], {"results": None}, {"error": "provider failure"}])
def test_malformed_search_response_is_a_sanitized_provider_failure(monkeypatch, payload):
    monkeypatch.setenv("KEENABLE_API_KEY", "keen_example")
    monkeypatch.setattr("resolution.integrations.request", lambda *a, **k: payload)
    with pytest.raises(ProviderError, match="unexpected response"):
        research_policy()


def test_uncertain_refund_is_recovered_by_action_identity(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
    monkeypatch.setattr(StripeSandbox, "charge", lambda *a: {})
    monkeypatch.setattr("resolution.integrations.request", lambda *a, **k: {"has_more": False, "data": [
        {"id": "re_unrelated", "amount": 14900, "status": "succeeded", "charge": "ch_demo", "metadata": {"case_id": "OTHER", "action_id": "action"}},
        {"id": "re_expected", "amount": 14900, "status": "succeeded", "charge": "ch_demo", "metadata": {"case_id": "CASE-2001", "action_id": "action"}},
    ]})
    result = StripeSandbox().find_refund("ch_demo", {"id": "CASE-2001"}, "action")
    assert result["reference"] == "re_expected"


def test_absent_refund_receipt_does_not_claim_failure_or_retry(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
    monkeypatch.setattr(StripeSandbox, "charge", lambda *a: {})
    calls = []
    def fake(method, *a, **k):
        calls.append(method)
        return {"has_more": False, "data": []}
    monkeypatch.setattr("resolution.integrations.request", fake)
    assert StripeSandbox().find_refund("ch_demo", {"id": "CASE-2001"}, "action") is None
    assert calls == ["GET"]
