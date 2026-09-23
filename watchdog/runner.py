"""Orchestration: fetch costs, analyse them, and notify. Dependencies are injected."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from .analysis import CostRecord, CostReport, build_report, required_start_date
from .config import NOTIFY_ALWAYS, WatchdogConfig
from .notifiers import build_slack_payload, build_teams_payload, post_webhook

logger = logging.getLogger(__name__)


class CostSource(Protocol):
    def query_daily_costs(
        self, subscription_id: str, start: date, end: date, cost_type: str = "ActualCost"
    ) -> list[CostRecord]: ...

    def get_subscription_name(self, subscription_id: str) -> str: ...


Poster = Callable[[str, dict], None]


@dataclass
class RunResult:
    report: CostReport
    payloads: dict[str, dict] = field(default_factory=dict)
    notified: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def summary(self) -> dict:
        r = self.report
        return {
            "reportDate": r.report_date.isoformat(),
            "currency": r.currency,
            "total": round(r.total.cost, 2),
            "dayOverDayPercent": r.total.day_over_day.percent,
            "weekOverWeekPercent": r.total.week_over_week.percent,
            "anomalies": [
                {"dimension": a.dimension, "name": a.name, "cost": round(a.cost, 2), "reason": a.anomaly_reason}
                for a in r.anomalies
            ],
            "notified": self.notified,
            "skippedReason": self.skipped_reason,
        }


def run_watchdog(
    config: WatchdogConfig,
    cost_source: CostSource,
    poster: Poster = post_webhook,
    today: date | None = None,
    dry_run: bool | None = None,
) -> RunResult:
    """Run one watchdog cycle. Raises if any notification channel fails."""
    dry_run = config.dry_run if dry_run is None else dry_run
    today = today or datetime.now(timezone.utc).date()
    report_date = today - timedelta(days=config.report_offset_days)
    start = required_start_date(report_date, config.thresholds)

    records: list[CostRecord] = []
    names: dict[str, str] = {}
    for sub in config.subscription_ids:
        records.extend(cost_source.query_daily_costs(sub, start, report_date, config.cost_type))
        names[sub] = cost_source.get_subscription_name(sub)

    report = build_report(records, report_date, config.thresholds, names)
    result = RunResult(report=report)
    if config.slack_webhook_url or dry_run:
        result.payloads["slack"] = build_slack_payload(report, config.top_n)
    if config.teams_webhook_url or dry_run:
        result.payloads["teams"] = build_teams_payload(report, config.top_n)

    logger.info(
        "Report %s: total %.2f %s, %d anomalies",
        report_date,
        report.total.cost,
        report.currency,
        len(report.anomalies),
    )

    if config.notify_mode != NOTIFY_ALWAYS and not report.has_anomalies:
        result.skipped_reason = "no anomalies (NOTIFY_MODE=anomalies)"
        return result
    if dry_run:
        result.skipped_reason = "dry run"
        return result

    errors: list[str] = []
    for channel, url in (("slack", config.slack_webhook_url), ("teams", config.teams_webhook_url)):
        if not url:
            continue
        try:
            poster(url, result.payloads[channel])
            result.notified.append(channel)
        except Exception as exc:  # noqa: BLE001 - try every channel before failing
            logger.error("Failed to notify %s: %s", channel, exc)
            errors.append(f"{channel}: {exc}")
    if errors:
        raise RuntimeError("Notification failed: " + "; ".join(errors))
    return result
