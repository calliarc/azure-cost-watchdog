"""Azure Cost Management Query API client (REST, authenticated with azure-identity).

Uses ``DefaultAzureCredential``, which resolves to the Function App's managed identity
in Azure and to your ``az login`` / VS Code identity locally.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from .analysis import CostRecord

logger = logging.getLogger(__name__)

ARM_ENDPOINT = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
QUERY_API_VERSION = "2023-11-01"
SUBSCRIPTION_API_VERSION = "2022-12-01"
_RETRY_HEADERS = (
    "x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after",
    "x-ms-ratelimit-microsoft.costmanagement-entity-retry-after",
    "x-ms-ratelimit-microsoft.costmanagement-tenant-retry-after",
    "x-ms-ratelimit-microsoft.costmanagement-client-retry-after",
    "Retry-After",
)


class CostQueryError(RuntimeError):
    """Raised when the Cost Management API returns an unrecoverable error."""


def build_query_body(start: date, end: date, cost_type: str = "ActualCost") -> dict:
    """Daily cost for [start, end] grouped by resource group and service."""
    return {
        "type": cost_type,
        "timeframe": "Custom",
        "timePeriod": {
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{end.isoformat()}T23:59:59Z",
        },
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ResourceGroupName"},
                {"type": "Dimension", "name": "ServiceName"},
            ],
        },
    }


def _parse_usage_date(value: Any) -> date:
    if isinstance(value, (int, float)):
        return datetime.strptime(str(int(value)), "%Y%m%d").date()
    text = str(value)
    if text.isdigit() and len(text) == 8:
        return datetime.strptime(text, "%Y%m%d").date()
    return datetime.fromisoformat(text.replace("Z", "+00:00")[:19]).date()


def parse_query_response(payload: dict, subscription_id: str) -> tuple[list[CostRecord], str | None]:
    """Turn a Query API response into records. Returns ``(records, next_link)``."""
    props = payload.get("properties") or {}
    columns = [c.get("name", "") for c in props.get("columns") or []]
    index = {name.lower(): i for i, name in enumerate(columns)}

    cost_idx = next((index[k] for k in ("cost", "precost", "costusd", "pretaxcost") if k in index), None)
    date_idx = index.get("usagedate")
    if cost_idx is None or date_idx is None:
        if not props.get("rows"):
            return [], props.get("nextLink")
        raise CostQueryError(f"Unexpected Cost Management columns: {columns}")
    rg_idx = index.get("resourcegroupname")
    svc_idx = index.get("servicename")
    cur_idx = index.get("currency")

    records: list[CostRecord] = []
    for row in props.get("rows") or []:
        records.append(
            CostRecord(
                usage_date=_parse_usage_date(row[date_idx]),
                cost=float(row[cost_idx] or 0.0),
                subscription_id=subscription_id,
                resource_group=str(row[rg_idx] or "") if rg_idx is not None else "",
                service=str(row[svc_idx] or "") if svc_idx is not None else "",
                currency=str(row[cur_idx] or "USD") if cur_idx is not None else "USD",
            )
        )
    return records, props.get("nextLink") or None


def _retry_after(response: Any, attempt: int) -> float:
    headers = getattr(response, "headers", {}) or {}
    for name in _RETRY_HEADERS:
        value = headers.get(name)
        if value:
            try:
                return min(float(value), 60.0)
            except ValueError:
                continue
    return min(2.0**attempt * 5, 60.0)


class CostManagementClient:
    """Minimal client for the Cost Management Query API."""

    def __init__(
        self,
        credential: Any = None,
        session: Any = None,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60.0,
    ) -> None:
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        if session is None:
            import requests

            session = requests.Session()
        self._credential = credential
        self._session = session
        self._max_retries = max_retries
        self._sleep = sleep
        self._timeout = timeout

    def _headers(self) -> dict:
        token = self._credential.get_token(ARM_SCOPE).token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _request(self, method: str, url: str, body: dict | None = None) -> dict:
        for attempt in range(self._max_retries + 1):
            response = self._session.request(method, url, headers=self._headers(), json=body, timeout=self._timeout)
            status = response.status_code
            if status == 429 or status >= 500:
                if attempt < self._max_retries:
                    delay = _retry_after(response, attempt)
                    logger.warning("Cost Management API returned %s, retrying in %.0fs", status, delay)
                    self._sleep(delay)
                    continue
            if status >= 400:
                raise CostQueryError(f"{method} {url.split('?')[0]} failed with HTTP {status}: {response.text[:500]}")
            return response.json() if response.content else {}
        raise CostQueryError("unreachable")  # pragma: no cover

    def query_daily_costs(
        self, subscription_id: str, start: date, end: date, cost_type: str = "ActualCost"
    ) -> list[CostRecord]:
        """Daily cost per resource group and service for one subscription."""
        url: str | None = (
            f"{ARM_ENDPOINT}/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query"
            f"?api-version={QUERY_API_VERSION}"
        )
        body = build_query_body(start, end, cost_type)
        records: list[CostRecord] = []
        pages = 0
        while url and pages < 100:
            payload = self._request("POST", url, body)
            page, url = parse_query_response(payload, subscription_id)
            records.extend(page)
            pages += 1
        logger.info("Fetched %d cost rows for subscription %s", len(records), subscription_id)
        return records

    def get_subscription_name(self, subscription_id: str) -> str:
        """Display name of a subscription, falling back to its ID."""
        url = f"{ARM_ENDPOINT}/subscriptions/{subscription_id}?api-version={SUBSCRIPTION_API_VERSION}"
        try:
            return self._request("GET", url).get("displayName") or subscription_id
        except Exception:  # noqa: BLE001 - the name is cosmetic
            logger.warning("Could not read display name for subscription %s", subscription_id)
            return subscription_id
