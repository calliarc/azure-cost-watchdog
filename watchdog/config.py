"""Configuration loaded from environment variables (Function App settings)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from .analysis import Thresholds

NOTIFY_ALWAYS = "always"
NOTIFY_ANOMALIES = "anomalies"
COST_TYPES = ("ActualCost", "AmortizedCost")

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    """Raised when the watchdog configuration is invalid."""


@dataclass(frozen=True)
class WatchdogConfig:
    subscription_ids: tuple[str, ...]
    slack_webhook_url: str | None = None
    teams_webhook_url: str | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    notify_mode: str = NOTIFY_ALWAYS
    top_n: int = 5
    report_offset_days: int = 1
    cost_type: str = "ActualCost"
    dry_run: bool = False

    @property
    def has_channels(self) -> bool:
        return bool(self.slack_webhook_url or self.teams_webhook_url)


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _float(env: Mapping[str, str], name: str, default: float, minimum: float = 0.0) -> float:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _int(env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _webhook(env: Mapping[str, str], name: str) -> str | None:
    url = _get(env, name)
    if url is not None and not url.lower().startswith("https://"):
        raise ConfigError(f"{name} must be an https:// URL")
    return url


def load_config(env: Mapping[str, str] | None = None) -> WatchdogConfig:
    """Build a :class:`WatchdogConfig` from environment variables."""
    env = os.environ if env is None else env

    subs_raw = _get(env, "COST_SUBSCRIPTION_IDS") or ""
    subscription_ids = tuple(dict.fromkeys(s.strip() for s in subs_raw.split(",") if s.strip()))
    if not subscription_ids:
        raise ConfigError("COST_SUBSCRIPTION_IDS must contain at least one subscription ID")

    notify_mode = (_get(env, "NOTIFY_MODE") or NOTIFY_ALWAYS).lower()
    if notify_mode not in (NOTIFY_ALWAYS, NOTIFY_ANOMALIES):
        raise ConfigError(f"NOTIFY_MODE must be '{NOTIFY_ALWAYS}' or '{NOTIFY_ANOMALIES}'")

    cost_type = _get(env, "COST_TYPE") or "ActualCost"
    matched = [c for c in COST_TYPES if c.lower() == cost_type.lower()]
    if not matched:
        raise ConfigError(f"COST_TYPE must be one of {', '.join(COST_TYPES)}")

    thresholds = Thresholds(
        percent=_float(env, "ANOMALY_THRESHOLD_PERCENT", 25.0),
        absolute=_float(env, "ANOMALY_THRESHOLD_ABSOLUTE", 10.0),
        rolling_window_days=_int(env, "ROLLING_WINDOW_DAYS", 7, 1, 60),
        alert_on_decrease=_bool(env, "ALERT_ON_DECREASE", False),
    )

    config = WatchdogConfig(
        subscription_ids=subscription_ids,
        slack_webhook_url=_webhook(env, "SLACK_WEBHOOK_URL"),
        teams_webhook_url=_webhook(env, "TEAMS_WEBHOOK_URL"),
        thresholds=thresholds,
        notify_mode=notify_mode,
        top_n=_int(env, "TOP_N", 5, 1, 25),
        report_offset_days=_int(env, "REPORT_OFFSET_DAYS", 1, 0, 30),
        cost_type=matched[0],
        dry_run=_bool(env, "DRY_RUN", False),
    )
    if not config.dry_run and not config.has_channels:
        raise ConfigError("Set SLACK_WEBHOOK_URL and/or TEAMS_WEBHOOK_URL, or enable DRY_RUN")
    return config
