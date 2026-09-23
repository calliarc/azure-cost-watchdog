# Azure Cost Watchdog

Azure Function that posts daily cost changes and anomalies to Slack or Microsoft Teams.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/calliarc/azure-cost-watchdog/actions/workflows/ci.yml/badge.svg)](https://github.com/calliarc/azure-cost-watchdog/actions/workflows/ci.yml)
![Status: v0.1.0](https://img.shields.io/badge/status-v0.1.0-brightgreen)

> **Status:** v0.1.0, the first working release. Feedback and issues are welcome.

## Features

- Daily summary of spend by subscription, resource group and service
- Day-over-day and week-over-week change detection
- Anomaly alerts when spend crosses a configurable threshold (percent and absolute, compared with a rolling average)
- Slack (Block Kit) and Microsoft Teams (Adaptive Card) notifications
- One-command deployment with managed identity (no stored secrets)

## Tech stack

- Azure Functions (Python 3.10+, v2 programming model, Flex Consumption plan)
- Azure Cost Management Query API via `azure-identity` (managed identity)
- Slack incoming webhooks / Teams Workflows webhooks
- Bicep for deployment

## How it works

1. A timer trigger runs on a configurable CRON schedule (default: 07:00 UTC daily).
2. The Function's managed identity queries the Cost Management Query API for each
   configured subscription. It fetches daily cost grouped by resource group and service.
3. For the report day (yesterday by default), the watchdog computes day-over-day change,
   week-over-week change (same weekday last week) and change against the N-day rolling
   average. It does this for the total and for every subscription, resource group and service.
4. A line item counts as an **anomaly** when it is at least `ANOMALY_THRESHOLD_PERCENT`
   percent **and** at least `ANOMALY_THRESHOLD_ABSOLUTE` currency units above its rolling
   average. Spend that appears where there was none before counts as "new spend".
5. The report is posted to Slack and/or Teams, either every day or only when an anomaly
   is found.

The analysis and message formatting live in pure modules (`watchdog/analysis.py`,
`watchdog/notifiers.py`), separate from the Azure client (`watchdog/cost_client.py`).

## Getting started

### Deploy to Azure

Requirements: Azure CLI, Azure Functions Core Tools v4, and Owner or User Access
Administrator on the subscription. You need that access because the template creates the
**Cost Management Reader** role assignment.

```bash
git clone https://github.com/calliarc/azure-cost-watchdog.git
cd azure-cost-watchdog
az login

export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...'   # and/or
export TEAMS_WEBHOOK_URL='https://...'                           # Teams Workflows webhook
./scripts/deploy.sh -g rg-cost-watchdog -l eastus
```

The script deploys `infra/main.bicep` and then publishes the code. The template creates a
Flex Consumption Function App with a system-assigned managed identity, a storage account
without shared keys, Application Insights, and the Cost Management Reader role at
subscription scope.

Check the deployment with a dry run. It returns the report and the message payloads
without posting anything:

```bash
curl "https://<function-app>.azurewebsites.net/api/run?dryRun=true&code=<function-key>"
```

See [docs/deployment.md](docs/deployment.md) for step-by-step deployment, monitoring
several subscriptions, webhook setup, Key Vault references and troubleshooting.

### Configuration

All settings are Function App settings (environment variables):

| Setting | Default | Description |
| --- | --- | --- |
| `COST_SUBSCRIPTION_IDS` | *(required)* | Comma-separated subscription IDs to monitor |
| `WATCHDOG_SCHEDULE` | `0 0 7 * * *` | Six-field NCRONTAB timer schedule (UTC) |
| `SLACK_WEBHOOK_URL` | – | Slack incoming webhook URL |
| `TEAMS_WEBHOOK_URL` | – | Teams Workflows (or connector) webhook URL |
| `NOTIFY_MODE` | `always` | `always` = daily summary, `anomalies` = post only when an anomaly is found |
| `ANOMALY_THRESHOLD_PERCENT` | `25` | Minimum % above the rolling average |
| `ANOMALY_THRESHOLD_ABSOLUTE` | `10` | Minimum amount (billing currency) above the rolling average |
| `ROLLING_WINDOW_DAYS` | `7` | Days in the rolling average baseline (1–60) |
| `ALERT_ON_DECREASE` | `false` | Also flag unusual drops in spend |
| `REPORT_OFFSET_DAYS` | `1` | Report on today minus N days (cost data can lag by up to 24h) |
| `TOP_N` | `5` | Resource groups and services listed per section |
| `COST_TYPE` | `ActualCost` | `ActualCost` or `AmortizedCost` |
| `DRY_RUN` | `false` | Build the report and log it, but don't post |

At least one webhook URL is required unless `DRY_RUN=true`.

### Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp local.settings.json.example local.settings.json   # set COST_SUBSCRIPTION_IDS
az login                                             # DefaultAzureCredential uses your login
func start                                           # needs Azurite for AzureWebJobsStorage
curl "http://localhost:7071/api/run?dryRun=true"
```

Your account needs Cost Management Reader (or Reader) on the subscription.

### Tests

```bash
pip install -r requirements-dev.txt
ruff check . && pytest
```

The tests use mocked cost data and mocked HTTP, so they run offline.

## Roadmap

- [x] Initial release
- [x] Documentation and examples
- [x] CI and automated tests
- [ ] Budget and forecast comparison
- [ ] Management group and billing account scopes
- [ ] Tag-based grouping

Have an idea? [Open an issue](https://github.com/calliarc/azure-cost-watchdog/issues).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 2026 CalliArc

---

Built and maintained by [CalliArc](https://www.calliarc.com/). Need help with Azure cloud services? [Talk to our team](https://www.calliarc.com/services/azure-cloud-migration/).
