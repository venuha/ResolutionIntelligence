import pytest


@pytest.fixture(autouse=True)
def isolated_search_configuration(monkeypatch):
    # Adding a real local key must never make the test suite consume search credits.
    for key in ("SEARCH_PROVIDER", "KEENABLE_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
