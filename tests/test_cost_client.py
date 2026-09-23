from datetime import date
from unittest.mock import MagicMock

import pytest
from conftest import SUB_A

from watchdog.cost_client import (
    ARM_SCOPE,
    CostManagementClient,
    CostQueryError,
    build_query_body,
    parse_query_response,
)

COLUMNS = [
    {"name": "Cost", "type": "Number"},
    {"name": "UsageDate", "type": "Number"},
    {"name": "ResourceGroupName", "type": "String"},
    {"name": "ServiceName", "type": "String"},
    {"name": "Currency", "type": "String"},
]


def page(rows, next_link=None):
    return {"properties": {"columns": COLUMNS, "rows": rows, "nextLink": next_link}}


def response(status, json_body=None, headers=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body or {}
    r.content = b"{}" if json_body is not None else b""
    r.headers = headers or {}
    r.text = text
    return r


def make_client(responses):
    credential = MagicMock()
    credential.get_token.return_value = MagicMock(token="fake-token")
    session = MagicMock()
    session.request.side_effect = responses
    sleeps = []
    client = CostManagementClient(credential=credential, session=session, sleep=sleeps.append)
    return client, session, credential, sleeps


def test_build_query_body():
    body = build_query_body(date(2026, 9, 15), date(2026, 9, 22), "AmortizedCost")
    assert body["type"] == "AmortizedCost"
    assert body["timeframe"] == "Custom"
    assert body["timePeriod"] == {"from": "2026-09-15T00:00:00Z", "to": "2026-09-22T23:59:59Z"}
    assert body["dataset"]["granularity"] == "Daily"
    assert [g["name"] for g in body["dataset"]["grouping"]] == ["ResourceGroupName", "ServiceName"]


def test_parse_query_response():
    payload = page([[12.5, 20260922, "rg-web", "App Service", "USD"], [None, "2026-09-21T00:00:00", None, "", "EUR"]])
    records, next_link = parse_query_response(payload, SUB_A)
    assert next_link is None
    assert records[0].usage_date == date(2026, 9, 22)
    assert records[0].cost == 12.5
    assert records[0].resource_group == "rg-web"
    assert records[0].subscription_id == SUB_A
    assert records[1].usage_date == date(2026, 9, 21)
    assert records[1].cost == 0.0
    assert records[1].resource_group == ""
    assert records[1].currency == "EUR"


def test_parse_empty_and_bad_columns():
    assert parse_query_response({"properties": {"columns": [], "rows": []}}, SUB_A) == ([], None)
    with pytest.raises(CostQueryError):
        parse_query_response({"properties": {"columns": [{"name": "Foo"}], "rows": [[1]]}}, SUB_A)


def test_query_daily_costs_paginates_and_authenticates():
    client, session, credential, _ = make_client(
        [
            response(200, page([[1.0, 20260921, "rg", "svc", "USD"]], next_link="https://management.azure.com/next")),
            response(200, page([[2.0, 20260922, "rg", "svc", "USD"]])),
        ]
    )
    records = client.query_daily_costs(SUB_A, date(2026, 9, 15), date(2026, 9, 22))
    assert [r.cost for r in records] == [1.0, 2.0]
    credential.get_token.assert_called_with(ARM_SCOPE)
    first = session.request.call_args_list[0]
    assert first.args[0] == "POST"
    assert f"/subscriptions/{SUB_A}/providers/Microsoft.CostManagement/query?api-version=" in first.args[1]
    assert first.kwargs["headers"]["Authorization"] == "Bearer fake-token"
    assert session.request.call_args_list[1].args[1] == "https://management.azure.com/next"


def test_retries_on_429_using_retry_header():
    client, session, _, sleeps = make_client(
        [
            response(429, headers={"x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after": "3"}),
            response(503),
            response(200, page([])),
        ]
    )
    assert client.query_daily_costs(SUB_A, date(2026, 9, 15), date(2026, 9, 22)) == []
    assert sleeps[0] == 3.0
    assert len(sleeps) == 2


def test_raises_on_client_error():
    client, _, _, _ = make_client([response(403, text="AuthorizationFailed")])
    with pytest.raises(CostQueryError, match="HTTP 403"):
        client.query_daily_costs(SUB_A, date(2026, 9, 15), date(2026, 9, 22))


def test_gives_up_after_max_retries():
    client, _, _, sleeps = make_client([response(429)] * 5)
    with pytest.raises(CostQueryError, match="HTTP 429"):
        client.query_daily_costs(SUB_A, date(2026, 9, 15), date(2026, 9, 22))
    assert len(sleeps) == 4


def test_subscription_name_with_fallback():
    client, _, _, _ = make_client([response(200, {"displayName": "Production"})])
    assert client.get_subscription_name(SUB_A) == "Production"
    client, _, _, _ = make_client([response(403, text="nope")])
    assert client.get_subscription_name(SUB_A) == SUB_A
