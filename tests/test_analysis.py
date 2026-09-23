from datetime import date, timedelta

import pytest
from conftest import REPORT_DATE, SUB_A, SUB_B, make_history

from watchdog.analysis import (
    NO_RESOURCE_GROUP,
    Change,
    CostRecord,
    Thresholds,
    build_report,
    detect_anomaly,
    required_start_date,
)


def test_change_percent():
    assert Change(120, 100).percent == pytest.approx(20.0)
    assert Change(80, 100).percent == pytest.approx(-20.0)
    assert Change(5, 0).percent is None
    assert Change(0, 0).percent == 0.0
    assert Change(120, 100).delta == 20


def test_required_start_date_covers_week_and_window():
    assert required_start_date(REPORT_DATE, Thresholds(rolling_window_days=3)) == REPORT_DATE - timedelta(days=7)
    assert required_start_date(REPORT_DATE, Thresholds(rolling_window_days=14)) == REPORT_DATE - timedelta(days=14)


def test_flat_spend_has_no_anomalies(history):
    report = build_report(history, REPORT_DATE)
    assert report.total.cost == pytest.approx(25.0)
    assert report.total.day_over_day.percent == pytest.approx(0.0)
    assert report.total.week_over_week.percent == pytest.approx(0.0)
    assert report.total.vs_rolling_average.baseline == pytest.approx(25.0)
    assert not report.has_anomalies
    assert [i.name for i in report.by_resource_group] == ["rg-web", "rg-data"]
    assert [i.name for i in report.by_service] == ["App Service", "Storage"]
    assert report.currency == "USD"


def test_spike_flags_total_rg_and_service():
    records = make_history(today_override={("rg-web", "App Service"): 60.0})
    report = build_report(records, REPORT_DATE, Thresholds(percent=25, absolute=10))
    names = {(a.dimension, a.name) for a in report.anomalies}
    assert ("total", "Total") in names
    assert ("resource_group", "rg-web") in names
    assert ("service", "App Service") in names
    assert ("resource_group", "rg-data") not in names
    web = next(i for i in report.by_resource_group if i.name == "rg-web")
    assert web.day_over_day.delta == pytest.approx(40.0)
    assert web.vs_rolling_average.percent == pytest.approx(200.0)
    assert "200% above the 7-day average" in web.anomaly_reason


def test_absolute_threshold_suppresses_small_percentage_spikes():
    # rg-data doubles (+100%) but only by $5, under the $10 absolute threshold.
    records = make_history(today_override={("rg-data", "Storage"): 10.0})
    report = build_report(records, REPORT_DATE, Thresholds(percent=25, absolute=10))
    assert not report.has_anomalies


def test_percent_threshold_suppresses_large_base_small_change():
    records = make_history(rows={("rg-big", "VMs"): 1000.0}, today_override={("rg-big", "VMs"): 1100.0})
    report = build_report(records, REPORT_DATE, Thresholds(percent=25, absolute=10))
    assert not report.has_anomalies
    report = build_report(records, REPORT_DATE, Thresholds(percent=5, absolute=10))
    assert report.has_anomalies


def test_new_spend_is_anomalous():
    records = make_history()
    records.append(CostRecord(REPORT_DATE, 50.0, SUB_A, "rg-new", "Azure OpenAI", "USD"))
    report = build_report(records, REPORT_DATE)
    new = next(a for a in report.anomalies if a.name == "rg-new")
    assert new.vs_rolling_average.percent is None
    assert "new spend" in new.anomaly_reason


def test_decrease_only_flagged_when_enabled():
    records = make_history(today_override={("rg-web", "App Service"): 0.0})
    assert not build_report(records, REPORT_DATE).has_anomalies
    report = build_report(records, REPORT_DATE, Thresholds(alert_on_decrease=True))
    web = next(a for a in report.anomalies if a.name == "rg-web")
    assert "below" in web.anomaly_reason


def test_rolling_average_uses_window_and_zero_fills_missing_days():
    records = [CostRecord(REPORT_DATE - timedelta(days=1), 70.0, SUB_A, "rg", "svc")]
    records.append(CostRecord(REPORT_DATE, 10.0, SUB_A, "rg", "svc"))
    report = build_report(records, REPORT_DATE, Thresholds(rolling_window_days=7))
    assert report.total.vs_rolling_average.baseline == pytest.approx(10.0)
    assert report.total.day_over_day.baseline == pytest.approx(70.0)
    assert report.total.week_over_week.baseline == 0.0


def test_week_over_week_uses_same_weekday():
    records = make_history(rows={("rg", "svc"): 10.0})
    records = [r for r in records if r.usage_date != REPORT_DATE - timedelta(days=7)]
    records.append(CostRecord(REPORT_DATE - timedelta(days=7), 5.0, SUB_A, "rg", "svc"))
    report = build_report(records, REPORT_DATE)
    assert report.total.week_over_week.percent == pytest.approx(100.0)


def test_multiple_subscriptions_and_blank_names():
    records = make_history(subscription_id=SUB_A) + make_history(subscription_id=SUB_B, rows={("rg-web", ""): 3.0})
    records.append(CostRecord(REPORT_DATE, 1.0, SUB_B, "", "Bandwidth"))
    report = build_report(records, REPORT_DATE, subscription_names={SUB_A: "Prod", SUB_B: "Dev"})
    assert {s.name for s in report.by_subscription} == {"Prod", "Dev"}
    rg_names = {i.name for i in report.by_resource_group}
    assert "rg-web [Prod]" in rg_names and "rg-web [Dev]" in rg_names
    assert f"{NO_RESOURCE_GROUP} [Dev]" in rg_names
    assert "(unknown service)" in {i.name for i in report.by_service}


def test_resource_group_names_are_case_insensitive():
    records = [
        CostRecord(REPORT_DATE, 5.0, SUB_A, "RG-Web", "svc"),
        CostRecord(REPORT_DATE, 5.0, SUB_A, "rg-web", "svc"),
    ]
    report = build_report(records, REPORT_DATE)
    assert len(report.by_resource_group) == 1
    assert report.by_resource_group[0].cost == 10.0


def test_empty_records():
    report = build_report([], date(2026, 1, 1))
    assert report.total.cost == 0
    assert report.by_service == []
    assert not report.has_anomalies


def test_mixed_currency_flag():
    records = [
        CostRecord(REPORT_DATE, 5.0, SUB_A, "a", "s", "USD"),
        CostRecord(REPORT_DATE, 50.0, SUB_B, "b", "s", "EUR"),
    ]
    report = build_report(records, REPORT_DATE)
    assert report.mixed_currencies
    assert report.currency == "EUR"


def test_detect_anomaly_requires_both_thresholds():
    t = Thresholds(percent=50, absolute=100)
    assert detect_anomaly(Change(300, 100), t) is not None
    assert detect_anomaly(Change(160, 100), t) is None  # +60 < 100 absolute
    assert detect_anomaly(Change(1400, 1000), t) is None  # +40% < 50%
    assert detect_anomaly(Change(0, 0), Thresholds(absolute=0)) is None
