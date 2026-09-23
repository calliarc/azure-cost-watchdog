from datetime import date, timedelta

import pytest
from conftest import REPORT_DATE, SUB_A, SUB_B, make_history

from watchdog.config import ConfigError, load_config

SLACK = "https://hooks.slack.example.invalid/services/T000/B000/XXXX"
TEAMS = "https://teams.example.invalid/workflows/abc"
TODAY = REPORT_DATE + timedelta(days=1)


def env(**overrides):
    base = {"COST_SUBSCRIPTION_IDS": SUB_A, "SLACK_WEBHOOK_URL": SLACK}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


class FakeCostSource:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def query_daily_costs(self, subscription_id, start, end, cost_type="ActualCost"):
        self.calls.append((subscription_id, start, end, cost_type))
        return [r for r in self.data.get(subscription_id, []) if start <= r.usage_date <= end]

    def get_subscription_name(self, subscription_id):
        return {SUB_A: "Prod", SUB_B: "Dev"}.get(subscription_id, subscription_id)


# ---------------------------------------------------------------- config


def test_defaults():
    cfg = load_config(env())
    assert cfg.subscription_ids == (SUB_A,)
    assert cfg.thresholds.percent == 25.0
    assert cfg.thresholds.absolute == 10.0
    assert cfg.thresholds.rolling_window_days == 7
    assert cfg.notify_mode == "always"
    assert cfg.cost_type == "ActualCost"
    assert not cfg.dry_run


def test_parses_all_settings():
    cfg = load_config(
        env(
            COST_SUBSCRIPTION_IDS=f" {SUB_A}, {SUB_B},{SUB_A} ",
            TEAMS_WEBHOOK_URL=TEAMS,
            ANOMALY_THRESHOLD_PERCENT="40",
            ANOMALY_THRESHOLD_ABSOLUTE="2.5",
            ROLLING_WINDOW_DAYS="14",
            ALERT_ON_DECREASE="yes",
            NOTIFY_MODE="Anomalies",
            TOP_N="3",
            REPORT_OFFSET_DAYS="2",
            COST_TYPE="amortizedcost",
        )
    )
    assert cfg.subscription_ids == (SUB_A, SUB_B)
    assert cfg.thresholds.percent == 40 and cfg.thresholds.absolute == 2.5
    assert cfg.thresholds.rolling_window_days == 14 and cfg.thresholds.alert_on_decrease
    assert cfg.notify_mode == "anomalies"
    assert cfg.top_n == 3 and cfg.report_offset_days == 2
    assert cfg.cost_type == "AmortizedCost"


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"COST_SUBSCRIPTION_IDS": " , "}, "COST_SUBSCRIPTION_IDS"),
        ({"SLACK_WEBHOOK_URL": None}, "SLACK_WEBHOOK_URL and/or TEAMS_WEBHOOK_URL"),
        ({"SLACK_WEBHOOK_URL": "http://insecure"}, "https"),
        ({"ANOMALY_THRESHOLD_PERCENT": "abc"}, "number"),
        ({"ANOMALY_THRESHOLD_ABSOLUTE": "-1"}, ">="),
        ({"ROLLING_WINDOW_DAYS": "0"}, "between"),
        ({"NOTIFY_MODE": "sometimes"}, "NOTIFY_MODE"),
        ({"COST_TYPE": "Usage"}, "COST_TYPE"),
        ({"DRY_RUN": "maybe"}, "true or false"),
    ],
)
def test_invalid_config(overrides, match):
    with pytest.raises(ConfigError, match=match):
        load_config(env(**overrides))


def test_dry_run_without_channels_is_valid():
    cfg = load_config(env(SLACK_WEBHOOK_URL=None, DRY_RUN="true"))
    assert cfg.dry_run and not cfg.has_channels


# ---------------------------------------------------------------- runner


def spike_data():
    return {SUB_A: make_history(today_override={("rg-web", "App Service"): 60.0})}


def test_run_posts_to_all_channels():
    from watchdog.runner import run_watchdog

    source = FakeCostSource(spike_data())
    posted = []
    cfg = load_config(env(TEAMS_WEBHOOK_URL=TEAMS))
    result = run_watchdog(cfg, source, poster=lambda url, p: posted.append((url, p)), today=TODAY)

    assert source.calls == [(SUB_A, REPORT_DATE - timedelta(days=7), REPORT_DATE, "ActualCost")]
    assert [u for u, _ in posted] == [SLACK, TEAMS]
    assert "blocks" in posted[0][1]
    assert posted[1][1]["type"] == "message"
    assert result.notified == ["slack", "teams"]
    summary = result.summary()
    assert summary["reportDate"] == "2026-09-22"
    assert summary["total"] == 65.0
    assert any(a["name"] == "rg-web" for a in summary["anomalies"])


def test_anomalies_only_mode_skips_quiet_days():
    from watchdog.runner import run_watchdog

    posted = []
    cfg = load_config(env(NOTIFY_MODE="anomalies"))
    quiet = FakeCostSource({SUB_A: make_history()})
    result = run_watchdog(cfg, quiet, poster=lambda u, p: posted.append(u), today=TODAY)
    assert posted == [] and "no anomalies" in result.skipped_reason

    run_watchdog(cfg, FakeCostSource(spike_data()), poster=lambda u, p: posted.append(u), today=TODAY)
    assert posted == [SLACK]


def test_dry_run_builds_payloads_without_posting():
    from watchdog.runner import run_watchdog

    cfg = load_config(env(SLACK_WEBHOOK_URL=None, DRY_RUN="true"))

    def fail(url, payload):
        raise AssertionError("should not post")

    result = run_watchdog(cfg, FakeCostSource(spike_data()), poster=fail, today=TODAY)
    assert set(result.payloads) == {"slack", "teams"}
    assert result.skipped_reason == "dry run"


def test_failure_in_one_channel_still_tries_the_other():
    from watchdog.runner import run_watchdog

    posted = []

    def poster(url, payload):
        if url == SLACK:
            raise RuntimeError("boom")
        posted.append(url)

    cfg = load_config(env(TEAMS_WEBHOOK_URL=TEAMS))
    with pytest.raises(RuntimeError, match="slack: boom"):
        run_watchdog(cfg, FakeCostSource(spike_data()), poster=poster, today=TODAY)
    assert posted == [TEAMS]


def test_multiple_subscriptions_and_window():
    from watchdog.runner import run_watchdog

    data = {SUB_A: make_history(days=20), SUB_B: make_history(days=20, subscription_id=SUB_B)}
    source = FakeCostSource(data)
    cfg = load_config(env(COST_SUBSCRIPTION_IDS=f"{SUB_A},{SUB_B}", ROLLING_WINDOW_DAYS="14", REPORT_OFFSET_DAYS="2"))
    result = run_watchdog(cfg, source, poster=lambda u, p: None, today=date(2026, 9, 24))
    assert [c[0] for c in source.calls] == [SUB_A, SUB_B]
    assert source.calls[0][1] == REPORT_DATE - timedelta(days=14)
    assert {s.name for s in result.report.by_subscription} == {"Prod", "Dev"}
    assert result.report.total.cost == 50.0


def test_function_app_registers_functions():
    pytest.importorskip("azure.functions")
    import function_app

    names = {f.get_function_name() for f in function_app.app.get_functions()}
    assert names == {"daily_cost_watchdog", "run_now"}
