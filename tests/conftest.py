import pytest


@pytest.fixture(autouse=True)
def api_token(monkeypatch):
    monkeypatch.setenv("TIBBER_API_TOKEN", "test-token")


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    from tibber_mcp import graphql
    from tibber_mcp.cache import TTLCache

    monkeypatch.setattr(graphql, "_cache", TTLCache())
