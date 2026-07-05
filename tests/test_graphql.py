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
async def test_network_error_raises():
    respx.post(graphql.API_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(TibberApiError, match="nicht erreichbar"):
        await graphql.query("{ viewer { name } }")


@respx.mock
async def test_server_error_500():
    respx.post(graphql.API_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(TibberApiError, match="500"):
        await graphql.query("{ viewer { name } }")


@respx.mock
async def test_non_json_200_body():
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(200, text="<html>gateway</html>")
    )
    with pytest.raises(TibberApiError, match="JSON"):
        await graphql.query("{ viewer { name } }")


@respx.mock
async def test_missing_data_field():
    respx.post(graphql.API_URL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(TibberApiError, match="data"):
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


HOMES_RESPONSE = {
    "data": {
        "viewer": {
            "homes": [
                {
                    "id": "h1",
                    "appNickname": "Zuhause",
                    "address": {
                        "address1": "Musterweg 1",
                        "postalCode": "70173",
                        "city": "Stuttgart",
                        "country": "DE",
                    },
                    "features": {"realTimeConsumptionEnabled": True},
                    "meteringPointData": {
                        "consumptionEan": "DE0001",
                        "gridCompany": "Netze BW",
                        "estimatedAnnualConsumption": 3500,
                    },
                    "currentSubscription": {"status": "running"},
                }
            ]
        }
    }
}


@respx.mock
async def test_get_homes_uses_cache():
    route = respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(200, json=HOMES_RESPONSE)
    )
    homes = await graphql.get_homes()
    assert homes[0]["id"] == "h1"
    await graphql.get_homes()  # zweiter Aufruf aus dem Cache
    assert route.call_count == 1


@respx.mock
async def test_get_price_info():
    price_response = {
        "data": {
            "viewer": {
                "home": {
                    "currentSubscription": {
                        "priceInfo": {
                            "current": {
                                "total": 0.28,
                                "startsAt": "2026-07-05T13:00:00.000+02:00",
                                "level": "NORMAL",
                            },
                            "today": [
                                {
                                    "total": 0.28,
                                    "startsAt": "2026-07-05T13:00:00.000+02:00",
                                    "level": "NORMAL",
                                }
                            ],
                            "tomorrow": [],
                        }
                    }
                }
            }
        }
    }
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(200, json=price_response)
    )
    info = await graphql.get_price_info("h1")
    assert info["current"]["total"] == 0.28
    assert info["tomorrow"] == []


@respx.mock
async def test_get_price_info_without_subscription():
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"viewer": {"home": {"currentSubscription": None}}}}
        )
    )
    with pytest.raises(TibberApiError, match="Vertrag"):
        await graphql.get_price_info("h1")


@respx.mock
async def test_get_consumption():
    consumption_response = {
        "data": {
            "viewer": {
                "home": {
                    "consumption": {
                        "nodes": [
                            {
                                "from": "2026-07-04T00:00:00.000+02:00",
                                "to": "2026-07-05T00:00:00.000+02:00",
                                "consumption": 9.5,
                                "cost": 2.7,
                                "unitPrice": 0.284,
                            }
                        ]
                    }
                }
            }
        }
    }
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(200, json=consumption_response)
    )
    nodes = await graphql.get_consumption("h1", "DAILY", 1)
    assert nodes[0]["consumption"] == 9.5


@respx.mock
async def test_get_consumption_empty_history():
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"viewer": {"home": {"consumption": None}}}}
        )
    )
    nodes = await graphql.get_consumption("h1", "DAILY", 1)
    assert nodes == []
