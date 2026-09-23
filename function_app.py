"""Azure Functions entry point (Python v2 programming model)."""

from __future__ import annotations

import json
import logging

import azure.functions as func

from watchdog.config import ConfigError, load_config
from watchdog.cost_client import CostManagementClient
from watchdog.runner import run_watchdog

app = func.FunctionApp()
logger = logging.getLogger("azure-cost-watchdog")

_client: CostManagementClient | None = None


def _cost_client() -> CostManagementClient:
    global _client
    if _client is None:
        _client = CostManagementClient()
    return _client


# The CRON expression comes from the WATCHDOG_SCHEDULE app setting
# (six-field NCRONTAB, UTC), e.g. "0 0 7 * * *" = every day at 07:00 UTC.
@app.timer_trigger(schedule="%WATCHDOG_SCHEDULE%", arg_name="timer", run_on_startup=False, use_monitor=True)
def daily_cost_watchdog(timer: func.TimerRequest) -> None:
    if timer.past_due:
        logger.warning("Timer is past due; running now")
    config = load_config()
    result = run_watchdog(config, _cost_client())
    logger.info("Watchdog run complete: %s", json.dumps(result.summary()))


@app.route(route="run", methods=["GET", "POST"], auth_level=func.AuthLevel.FUNCTION)
def run_now(req: func.HttpRequest) -> func.HttpResponse:
    """Manual trigger. ``?dryRun=true`` returns the report and payloads without posting."""
    dry_run = (req.params.get("dryRun") or "").lower() in ("1", "true", "yes")
    try:
        config = load_config()
    except ConfigError as exc:
        return func.HttpResponse(json.dumps({"error": str(exc)}), status_code=500, mimetype="application/json")
    result = run_watchdog(config, _cost_client(), dry_run=dry_run or None)
    body = result.summary()
    if dry_run:
        body["payloads"] = result.payloads
    return func.HttpResponse(json.dumps(body, default=str), mimetype="application/json")
