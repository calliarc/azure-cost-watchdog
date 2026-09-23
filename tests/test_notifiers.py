import json
from unittest.mock import MagicMock

import pytest
from conftest import REPORT_DATE, SUB_A, SUB_B, make_history

from watchdog.analysis import Change, CostRecord, build_report
from watchdog.notifiers import (
    NotificationError,
    build_slack_payload,
    build_teams_payload,
    format_change,
    format_money,
    post_webhook,
)


def spike_report():
    records = make_history(today_override={("rg-web", "App Service"): 60.0})
    return build_report(records, REPORT_DATE)


def test_format_money():
    assert format_money(1234.5, "USD") == "$1,234.50"
    assert format_money(-3, "EUR") == "-€3.00"
    assert format_money(7, "CHF") == "7.00 CHF"


def test_format_change():
    assert format_change(Change(120, 100), "USD") == "+$20.00 (+20.0%)"
    assert format_change(Change(80, 100), "USD") == "-$20.00 (-20.0%)"
    assert format_change(Change(9, 0), "USD") == "+$9.00 (new)"
    assert format_change(Change(0, 0), "USD") == "±$0.00 (+0.0%)"


def test_slack_payload_structure():
    payload = build_slack_payload(spike_report(), top_n=1)
    assert "2026-09-22" in payload["text"]
    blocks = payload["blocks"]
    assert blocks[0]["type"] == "header"
    assert len(blocks[0]["text"]["text"]) <= 150
    text = json.dumps(payload)
    assert "Anomalies" in text
    assert "rg-web" in text
    assert "and 1 more" in text  # top_n=1 truncates resource groups
    for block in blocks:
        if block["type"] == "section" and "text" in block:
            assert len(block["text"]["text"]) <= 3000
        for f in block.get("fields", []):
            assert len(f["text"]) <= 2000
    assert len(blocks[1]["fields"]) <= 10


def test_slack_no_anomalies_and_escaping():
    records = make_history(rows={("rg-<script>&", "svc"): 10.0})
    payload = build_slack_payload(build_report(records, REPORT_DATE))
    text = json.dumps(payload)
    assert "No anomalies" in text
    assert "rg-&lt;script&gt;&amp;" in text
    assert "<script>" not in text


def test_slack_shows_subscriptions_only_when_multiple():
    single = json.dumps(build_slack_payload(spike_report()))
    assert "Top subscriptions" not in single
    records = make_history(subscription_id=SUB_A) + make_history(subscription_id=SUB_B)
    multi = json.dumps(build_slack_payload(build_report(records, REPORT_DATE)))
    assert "Top subscriptions" in multi


def test_teams_payload_is_adaptive_card_message():
    payload = build_teams_payload(spike_report())
    assert payload["type"] == "message"
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    containers = [b for b in card["body"] if b["type"] == "Container"]
    assert containers and containers[0]["style"] == "attention"
    assert "rg-web" in json.dumps(card)


def test_teams_no_anomalies():
    card = build_teams_payload(build_report(make_history(), REPORT_DATE))["attachments"][0]["content"]
    assert any(b.get("text") == "✅ No anomalies" for b in card["body"])


def test_empty_report_renders():
    report = build_report([], REPORT_DATE)
    assert "No spend" in json.dumps(build_slack_payload(report))
    assert "No spend" in json.dumps(build_teams_payload(report))


def test_mixed_currency_warning():
    records = [CostRecord(REPORT_DATE, 1, SUB_A, "a", "s", "USD"), CostRecord(REPORT_DATE, 1, SUB_B, "b", "s", "EUR")]
    assert "multiple billing currencies" in json.dumps(build_slack_payload(build_report(records, REPORT_DATE)))


def test_post_webhook_success():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200, text="ok")
    post_webhook("https://example.invalid/hook", {"a": 1}, session=session)
    session.post.assert_called_once_with("https://example.invalid/hook", json={"a": 1}, timeout=15.0)


def test_post_webhook_accepts_202():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=202, text="")
    post_webhook("https://example.invalid/hook", {}, session=session)


def test_post_webhook_error():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=400, text="invalid_payload")
    with pytest.raises(NotificationError, match="HTTP 400: invalid_payload"):
        post_webhook("https://example.invalid/hook", {}, session=session)
