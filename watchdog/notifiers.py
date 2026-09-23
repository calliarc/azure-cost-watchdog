"""Build Slack (Block Kit) and Microsoft Teams (Adaptive Card) messages and post them.

Payload builders are pure functions. :func:`post_webhook` takes an injectable
``session`` so HTTP can be mocked in tests.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from . import __version__
from .analysis import (
    DIM_RESOURCE_GROUP,
    DIM_SERVICE,
    DIM_SUBSCRIPTION,
    DIM_TOTAL,
    Change,
    CostReport,
    LineItem,
)

logger = logging.getLogger(__name__)

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "INR": "₹", "AUD": "A$", "CAD": "C$"}
_DIM_LABELS = {
    DIM_TOTAL: "Total",
    DIM_SUBSCRIPTION: "Subscription",
    DIM_RESOURCE_GROUP: "Resource group",
    DIM_SERVICE: "Service",
}
_SLACK_SECTION_LIMIT = 3000
_MAX_ANOMALIES = 10


class NotificationError(RuntimeError):
    """Raised when a webhook returns an error."""


class _Session(Protocol):
    def post(self, url: str, json: Any = ..., timeout: float = ...) -> Any: ...


# --------------------------------------------------------------------------- formatting


def format_money(amount: float, currency: str) -> str:
    symbol = _SYMBOLS.get(currency.upper())
    sign = "-" if amount < 0 else ""
    if symbol:
        return f"{sign}{symbol}{abs(amount):,.2f}"
    return f"{sign}{abs(amount):,.2f} {currency}"


def format_change(change: Change, currency: str) -> str:
    """E.g. ``+$12.30 (+15.2%)``, ``-$4.00 (-3.1%)`` or ``+$9.00 (new)``."""
    delta = change.delta
    money = format_money(abs(delta), currency)
    sign = "+" if delta > 0 else ("-" if delta < 0 else "±")
    pct = change.percent
    pct_text = "new" if pct is None else f"{pct:+.1f}%"
    return f"{sign}{money} ({pct_text})"


def _trend(change: Change) -> str:
    if change.delta > 0:
        return "▲"
    if change.delta < 0:
        return "▼"
    return "■"


def _title(report: CostReport) -> str:
    return f"Azure cost report for {report.report_date.isoformat()}"


def _threshold_text(report: CostReport) -> str:
    t = report.thresholds
    return (
        f"Anomaly rule: ≥{t.percent:g}% and ≥{format_money(t.absolute, report.currency)} "
        f"vs {t.rolling_window_days}-day average"
    )


def _footer(report: CostReport) -> str:
    text = f"{_threshold_text(report)} · azure-cost-watchdog v{__version__}"
    if report.mixed_currencies:
        text += " · warning: multiple billing currencies, totals are summed as-is"
    return text


def _item_line(item: LineItem, currency: str) -> str:
    return (
        f"{format_money(item.cost, currency)} · DoD {format_change(item.day_over_day, currency)}"
        f" · WoW {format_change(item.week_over_week, currency)}"
    )


def _anomaly_line(item: LineItem, currency: str) -> str:
    avg = item.vs_rolling_average
    return (
        f"{_DIM_LABELS[item.dimension]} {item.name}: {format_money(item.cost, currency)} "
        f"vs avg {format_money(avg.baseline, currency)} ({item.anomaly_reason})"
    )


# --------------------------------------------------------------------------- Slack


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str, limit: int = _SLACK_SECTION_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _slack_list_section(title: str, items: list[LineItem], currency: str, top_n: int) -> dict:
    lines = [f"*{title}*"]
    for item in items[:top_n]:
        lines.append(f"{_trend(item.day_over_day)} *{_slack_escape(item.name)}* — {_item_line(item, currency)}")
    if len(items) > top_n:
        lines.append(f"_…and {len(items) - top_n} more_")
    if len(items) == 0:
        lines.append("_No spend_")
    return {"type": "section", "text": {"type": "mrkdwn", "text": _truncate("\n".join(lines))}}


def build_slack_payload(report: CostReport, top_n: int = 5) -> dict:
    """Slack incoming-webhook payload using Block Kit."""
    cur = report.currency
    total = report.total
    anomalies = report.anomalies
    status = (
        f":rotating_light: {len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'}"
        if anomalies
        else ":white_check_mark: No anomalies"
    )

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": _title(report)[:150], "emoji": True}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Total spend*\n{format_money(total.cost, cur)}"},
                {"type": "mrkdwn", "text": f"*Status*\n{status}"},
                {"type": "mrkdwn", "text": f"*Day over day*\n{format_change(total.day_over_day, cur)}"},
                {"type": "mrkdwn", "text": f"*Week over week*\n{format_change(total.week_over_week, cur)}"},
                {
                    "type": "mrkdwn",
                    "text": f"*vs {report.thresholds.rolling_window_days}-day average*\n"
                    f"{format_change(total.vs_rolling_average, cur)}",
                },
            ],
        },
    ]
    if anomalies:
        lines = ["*:rotating_light: Anomalies*"]
        lines += [f"• {_slack_escape(_anomaly_line(a, cur))}" for a in anomalies[:_MAX_ANOMALIES]]
        if len(anomalies) > _MAX_ANOMALIES:
            lines.append(f"_…and {len(anomalies) - _MAX_ANOMALIES} more_")
        blocks += [
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": _truncate("\n".join(lines))}},
        ]

    blocks.append({"type": "divider"})
    if len(report.by_subscription) > 1:
        blocks.append(_slack_list_section("Top subscriptions", report.by_subscription, cur, top_n))
    blocks.append(_slack_list_section("Top resource groups", report.by_resource_group, cur, top_n))
    blocks.append(_slack_list_section("Top services", report.by_service, cur, top_n))
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _slack_escape(_footer(report))}]})

    fallback = (
        f"{_title(report)}: {format_money(total.cost, cur)} "
        f"(DoD {format_change(total.day_over_day, cur)}), "
        f"{len(anomalies)} anomalies"
    )
    return {"text": fallback, "blocks": blocks}


# --------------------------------------------------------------------------- Teams


def _teams_list(title: str, items: list[LineItem], currency: str, top_n: int) -> list[dict]:
    body: list[dict] = [{"type": "TextBlock", "text": title, "weight": "Bolder", "spacing": "Medium", "wrap": True}]
    if not items:
        body.append({"type": "TextBlock", "text": "No spend", "isSubtle": True, "wrap": True})
        return body
    facts = [{"title": f"{_trend(i.day_over_day)} {i.name}", "value": _item_line(i, currency)} for i in items[:top_n]]
    body.append({"type": "FactSet", "facts": facts})
    if len(items) > top_n:
        body.append({"type": "TextBlock", "text": f"…and {len(items) - top_n} more", "isSubtle": True, "wrap": True})
    return body


def build_teams_card(report: CostReport, top_n: int = 5) -> dict:
    """Adaptive Card (v1.4) describing the report."""
    cur = report.currency
    total = report.total
    anomalies = report.anomalies
    body: list[dict] = [
        {"type": "TextBlock", "text": _title(report), "size": "Large", "weight": "Bolder", "wrap": True},
        {
            "type": "FactSet",
            "facts": [
                {"title": "Total spend", "value": format_money(total.cost, cur)},
                {"title": "Day over day", "value": format_change(total.day_over_day, cur)},
                {"title": "Week over week", "value": format_change(total.week_over_week, cur)},
                {
                    "title": f"vs {report.thresholds.rolling_window_days}-day avg",
                    "value": format_change(total.vs_rolling_average, cur),
                },
            ],
        },
    ]
    if anomalies:
        items = [
            {
                "type": "TextBlock",
                "text": f"⚠ {len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'}",
                "weight": "Bolder",
                "color": "Attention",
                "wrap": True,
            }
        ]
        items += [
            {"type": "TextBlock", "text": f"- {_anomaly_line(a, cur)}", "wrap": True, "spacing": "Small"}
            for a in anomalies[:_MAX_ANOMALIES]
        ]
        if len(anomalies) > _MAX_ANOMALIES:
            items.append({"type": "TextBlock", "text": f"…and {len(anomalies) - _MAX_ANOMALIES} more", "wrap": True})
        body.append({"type": "Container", "style": "attention", "items": items, "spacing": "Medium"})
    else:
        body.append({"type": "TextBlock", "text": "✅ No anomalies", "color": "Good", "wrap": True})

    if len(report.by_subscription) > 1:
        body += _teams_list("Top subscriptions", report.by_subscription, cur, top_n)
    body += _teams_list("Top resource groups", report.by_resource_group, cur, top_n)
    body += _teams_list("Top services", report.by_service, cur, top_n)
    body.append({"type": "TextBlock", "text": _footer(report), "size": "Small", "isSubtle": True, "wrap": True})

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
    }


def build_teams_payload(report: CostReport, top_n: int = 5) -> dict:
    """Message envelope accepted by Teams Workflows ("Post to a channel when a webhook
    request is received") and legacy Office 365 connector incoming webhooks."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": build_teams_card(report, top_n),
            }
        ],
    }


# --------------------------------------------------------------------------- HTTP


def post_webhook(url: str, payload: dict, session: _Session | None = None, timeout: float = 15.0) -> None:
    """POST ``payload`` as JSON. Raises :class:`NotificationError` on non-2xx responses."""
    if session is None:
        import requests

        session = requests.Session()
    response = session.post(url, json=payload, timeout=timeout)
    status = getattr(response, "status_code", 0)
    if not 200 <= status < 300:
        body = (getattr(response, "text", "") or "")[:200]
        raise NotificationError(f"Webhook returned HTTP {status}: {body}")
