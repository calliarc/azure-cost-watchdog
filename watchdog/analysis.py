"""Pure cost analysis: aggregation, period-over-period change and anomaly detection.

Nothing in this module talks to Azure or the network, so it is fully unit-testable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

NO_RESOURCE_GROUP = "(no resource group)"
UNKNOWN_SERVICE = "(unknown service)"

DIM_TOTAL = "total"
DIM_SUBSCRIPTION = "subscription"
DIM_RESOURCE_GROUP = "resource_group"
DIM_SERVICE = "service"


@dataclass(frozen=True)
class CostRecord:
    """One row of daily cost: a single day, subscription, resource group and service."""

    usage_date: date
    cost: float
    subscription_id: str
    resource_group: str = ""
    service: str = ""
    currency: str = "USD"


@dataclass(frozen=True)
class Thresholds:
    """Anomaly rule: spend is anomalous when it differs from the rolling average by at
    least ``percent`` percent AND at least ``absolute`` currency units."""

    percent: float = 25.0
    absolute: float = 10.0
    rolling_window_days: int = 7
    alert_on_decrease: bool = False


@dataclass(frozen=True)
class Change:
    """Difference between a current value and a baseline."""

    current: float
    baseline: float

    @property
    def delta(self) -> float:
        return self.current - self.baseline

    @property
    def percent(self) -> float | None:
        """Percent change, or ``None`` when the baseline is zero."""
        if self.baseline == 0:
            return None if self.current != 0 else 0.0
        return (self.current - self.baseline) / abs(self.baseline) * 100.0


@dataclass(frozen=True)
class LineItem:
    """Cost for one group (the total, a subscription, resource group or service)."""

    dimension: str
    key: str
    name: str
    cost: float
    day_over_day: Change
    week_over_week: Change
    vs_rolling_average: Change
    is_anomaly: bool = False
    anomaly_reason: str | None = None


@dataclass
class CostReport:
    report_date: date
    currency: str
    thresholds: Thresholds
    total: LineItem
    by_subscription: list[LineItem] = field(default_factory=list)
    by_resource_group: list[LineItem] = field(default_factory=list)
    by_service: list[LineItem] = field(default_factory=list)
    mixed_currencies: bool = False
    record_count: int = 0

    @property
    def anomalies(self) -> list[LineItem]:
        """All anomalous line items, largest absolute deviation first."""
        items = [self.total, *self.by_subscription, *self.by_resource_group, *self.by_service]
        flagged = [i for i in items if i.is_anomaly]
        return sorted(flagged, key=lambda i: abs(i.vs_rolling_average.delta), reverse=True)

    @property
    def has_anomalies(self) -> bool:
        return bool(self.anomalies)


def required_start_date(report_date: date, thresholds: Thresholds) -> date:
    """First day of data needed for DoD, WoW (7 days) and the rolling average."""
    return report_date - timedelta(days=max(7, thresholds.rolling_window_days))


def detect_anomaly(change: Change, thresholds: Thresholds) -> str | None:
    """Return a human-readable reason if ``change`` breaches the thresholds, else ``None``."""
    delta = change.delta
    if delta < 0 and not thresholds.alert_on_decrease:
        return None
    if abs(delta) < thresholds.absolute or delta == 0:
        return None
    pct = change.percent
    window = thresholds.rolling_window_days
    if pct is None:
        return f"new spend (no cost in the previous {window} days)"
    if abs(pct) < thresholds.percent:
        return None
    direction = "above" if delta > 0 else "below"
    return f"{abs(pct):.0f}% {direction} the {window}-day average"


def _line_item(
    dimension: str,
    key: str,
    name: str,
    daily: Mapping[date, float],
    report_date: date,
    thresholds: Thresholds,
) -> LineItem:
    current = daily.get(report_date, 0.0)
    window = thresholds.rolling_window_days
    history = [daily.get(report_date - timedelta(days=i), 0.0) for i in range(1, window + 1)]
    rolling = sum(history) / window
    vs_avg = Change(current, rolling)
    reason = detect_anomaly(vs_avg, thresholds)
    return LineItem(
        dimension=dimension,
        key=key,
        name=name,
        cost=current,
        day_over_day=Change(current, daily.get(report_date - timedelta(days=1), 0.0)),
        week_over_week=Change(current, daily.get(report_date - timedelta(days=7), 0.0)),
        vs_rolling_average=vs_avg,
        is_anomaly=reason is not None,
        anomaly_reason=reason,
    )


def build_report(
    records: Iterable[CostRecord],
    report_date: date,
    thresholds: Thresholds | None = None,
    subscription_names: Mapping[str, str] | None = None,
) -> CostReport:
    """Aggregate raw daily cost records into a :class:`CostReport` for ``report_date``."""
    thresholds = thresholds or Thresholds()
    subscription_names = subscription_names or {}
    records = list(records)

    total: dict[date, float] = defaultdict(float)
    groups: dict[tuple[str, str], dict[date, float]] = defaultdict(lambda: defaultdict(float))
    names: dict[tuple[str, str], str] = {}
    currencies: dict[str, float] = defaultdict(float)
    subscriptions = {r.subscription_id.lower() for r in records}
    multi_sub = len(subscriptions) > 1

    def sub_label(sub_id: str) -> str:
        return subscription_names.get(sub_id) or subscription_names.get(sub_id.lower()) or sub_id

    for r in records:
        sub_key = r.subscription_id.lower()
        rg = r.resource_group.strip() or NO_RESOURCE_GROUP
        service = r.service.strip() or UNKNOWN_SERVICE
        rg_key = f"{sub_key}/{rg.lower()}"
        svc_key = service.lower()

        total[r.usage_date] += r.cost
        currencies[r.currency or "USD"] += abs(r.cost)
        for dim, key, name in (
            (DIM_SUBSCRIPTION, sub_key, sub_label(r.subscription_id)),
            (DIM_RESOURCE_GROUP, rg_key, f"{rg} [{sub_label(r.subscription_id)}]" if multi_sub else rg),
            (DIM_SERVICE, svc_key, service),
        ):
            groups[(dim, key)][r.usage_date] += r.cost
            names.setdefault((dim, key), name)

    def items(dimension: str) -> list[LineItem]:
        result = [
            _line_item(dim, key, names[(dim, key)], daily, report_date, thresholds)
            for (dim, key), daily in groups.items()
            if dim == dimension
        ]
        # Drop groups with no spend today and none in the comparison window.
        result = [
            i
            for i in result
            if i.cost or i.day_over_day.baseline or i.week_over_week.baseline or i.vs_rolling_average.baseline
        ]
        return sorted(result, key=lambda i: (-i.cost, i.name.lower()))

    currency = max(currencies, key=currencies.get) if currencies else "USD"
    return CostReport(
        report_date=report_date,
        currency=currency,
        thresholds=thresholds,
        total=_line_item(DIM_TOTAL, "total", "Total", total, report_date, thresholds),
        by_subscription=items(DIM_SUBSCRIPTION),
        by_resource_group=items(DIM_RESOURCE_GROUP),
        by_service=items(DIM_SERVICE),
        mixed_currencies=len(currencies) > 1,
        record_count=len(records),
    )
