import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog.analysis import CostRecord  # noqa: E402

SUB_A = "11111111-1111-1111-1111-111111111111"
SUB_B = "22222222-2222-2222-2222-222222222222"
REPORT_DATE = date(2026, 9, 22)


def make_history(
    report_date: date = REPORT_DATE,
    days: int = 8,
    subscription_id: str = SUB_A,
    rows: dict | None = None,
    today_override: dict | None = None,
) -> list[CostRecord]:
    """Flat daily history for each (rg, service) -> cost, with optional overrides on report_date."""
    rows = rows or {("rg-web", "App Service"): 20.0, ("rg-data", "Storage"): 5.0}
    today_override = today_override or {}
    records = []
    for offset in range(days, -1, -1):
        day = report_date - timedelta(days=offset)
        for (rg, svc), cost in rows.items():
            value = today_override.get((rg, svc), cost) if offset == 0 else cost
            records.append(CostRecord(day, value, subscription_id, rg, svc, "USD"))
    return records


@pytest.fixture
def history():
    return make_history()
