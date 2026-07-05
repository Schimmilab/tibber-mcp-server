import httpx
import pytest
import respx

from tibber_mcp import graphql
from tibber_mcp.graphql import TibberApiError


@respx.mock
async def test_query_returns_data():
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"viewer": {"homes": []}}})
    )
    data = await graphql.query("{ viewer { homes { id } } }")
    assert data == {"viewer": {"homes": []}}


async def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("TIBBER_API_TOKEN", raising=False)
    with pytest.raises(TibberApiError, match="TIBBER_API_TOKEN"):
        await graphql.query("{ viewer { name } }")


@respx.mock
async def test_invalid_token_401():
    respx.post(graphql.API_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(TibberApiError, match="401"):
        await graphql.query("{ viewer { name } }")


@respx.mock
async def test_rate_limit_429():
    respx.post(graphql.API_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(TibberApiError, match="Rate-Limit"):
        await graphql.query("{ viewer { name } }")


@respx.mock
async def test_graphql_errors_raised():
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(
            200, json={"errors": [{"message": "boom"}], "data": None}
        )
    )
    with pytest.raises(TibberApiError, match="boom"):
        await graphql.query("{ viewer { name } }")
