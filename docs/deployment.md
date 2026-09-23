# Deploying Azure Cost Watchdog

This guide covers deploying to Azure with the included Bicep template, setting up the
Slack and Teams webhooks, and checking that it works.

## What gets deployed

`infra/main.bicep` (resource group scope) creates:

| Resource | Purpose |
| --- | --- |
| Function App on the **Flex Consumption** plan (Linux, Python 3.11 by default) | Runs the timer and the manual `run` endpoint |
| System-assigned managed identity | Used to call the Cost Management API and storage. No stored Azure credentials |
| Storage account (shared keys disabled) | Functions host storage and the deployment package container |
| Log Analytics workspace + Application Insights | Logs and telemetry (Entra ID auth) |
| Role assignment **Cost Management Reader** (`72fafb9e-0641-4937-9268-a91bfd8191a3`) at **subscription scope** | Lets the identity read cost data for each subscription in `costSubscriptionIds` |
| Storage Blob Data Owner, Queue Data Contributor and Table Data Contributor on the storage account | Identity-based `AzureWebJobsStorage` |
| Monitoring Metrics Publisher on Application Insights | Entra ID authenticated telemetry |

## Prerequisites

- Azure CLI 2.60 or later, signed in with `az login`
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
- **Owner** or **User Access Administrator** on every monitored subscription. The template creates role assignments, so Contributor is not enough.
- A region that supports Flex Consumption. Check with `az functionapp list-flexconsumption-locations -o table`.

## 1. Create the webhooks

**Slack:** create a Slack app, enable *Incoming Webhooks*, add a webhook to your channel
and copy the `https://hooks.slack.com/services/...` URL.

**Microsoft Teams:** in the channel, open **Workflows** and choose the template
*Post to a channel when a webhook request is received*. Copy the generated URL. The
watchdog sends an Adaptive Card inside a `{"type": "message", "attachments": [...]}`
envelope. Workflows accepts that format, and so do legacy Office 365 connector webhooks.

You can set up one channel or both. Treat webhook URLs as secrets.

## 2. Deploy

### One command

```bash
export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...'   # optional
export TEAMS_WEBHOOK_URL='https://...'                           # optional
./scripts/deploy.sh -g rg-cost-watchdog -l eastus
```

Pass extra Bicep parameters with `-p`, for example
`-p notifyMode=anomalies -p schedule='0 30 6 * * *'`.

### Step by step

```bash
az group create -n rg-cost-watchdog -l eastus

az deployment group create -g rg-cost-watchdog -f infra/main.bicep \
  -p slackWebhookUrl="$SLACK_WEBHOOK_URL" teamsWebhookUrl="$TEAMS_WEBHOOK_URL"

APP=$(az deployment group show -g rg-cost-watchdog -n main \
  --query properties.outputs.functionAppName.value -o tsv)

func azure functionapp publish "$APP" --python
```

### Monitoring several subscriptions

```bash
az deployment group create -g rg-cost-watchdog -f infra/main.bicep \
  -p costSubscriptionIds='["<sub-id-1>","<sub-id-2>"]' slackWebhookUrl="$SLACK_WEBHOOK_URL"
```

The template creates a Cost Management Reader assignment in each listed subscription.
Your account needs permission to create role assignments in each one.

## 3. Verify

1. Wait a few minutes after the first deployment. Role assignments can take up to about
   10 minutes to propagate.
2. Get a function key:
   `az functionapp keys list -g rg-cost-watchdog -n "$APP" --query functionKeys.default -o tsv`
3. Do a dry run. This builds the report and returns the Slack and Teams payloads without
   posting anything:
   `curl "https://$APP.azurewebsites.net/api/run?dryRun=true&code=<key>"`
4. Post for real: `curl -X POST "https://$APP.azurewebsites.net/api/run?code=<key>"`

If you deploy without any webhook URL, the template sets `DRY_RUN=true`. The timer then
only logs the report. Add a webhook URL and set `DRY_RUN=false` to start posting.

## Parameters

| Bicep parameter | App setting | Default |
| --- | --- | --- |
| `appName` | – | `cost-watchdog` |
| `costSubscriptionIds` | `COST_SUBSCRIPTION_IDS` | deployment subscription |
| `schedule` | `WATCHDOG_SCHEDULE` | `0 0 7 * * *` (07:00 UTC daily) |
| `slackWebhookUrl` | `SLACK_WEBHOOK_URL` | empty |
| `teamsWebhookUrl` | `TEAMS_WEBHOOK_URL` | empty |
| `notifyMode` | `NOTIFY_MODE` | `always` |
| `anomalyThresholdPercent` | `ANOMALY_THRESHOLD_PERCENT` | `25` |
| `anomalyThresholdAbsolute` | `ANOMALY_THRESHOLD_ABSOLUTE` | `10` |
| `rollingWindowDays` | `ROLLING_WINDOW_DAYS` | `7` |
| `costType` | `COST_TYPE` | `ActualCost` |
| `pythonVersion` | – | `3.11` |

You can also change settings after deployment with
`az functionapp config appsettings set -g <rg> -n <app> --settings KEY=value`.

## Keeping webhook URLs in Key Vault (optional)

The template stores webhook URLs as Function App settings; they are passed as `@secure()`
parameters and are not written to deployment history. If you prefer Key Vault, store
the URL as a secret, grant the Function identity **Key Vault Secrets User**, and set the
app setting to a reference:

```
SLACK_WEBHOOK_URL=@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/slack-webhook/)
```

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `HTTP 401/403` from Cost Management | The role assignment hasn't propagated yet, or the subscription isn't in `costSubscriptionIds` |
| `HTTP 429` in logs | Cost Management throttling. The client retries using the API's retry-after headers |
| Yesterday's totals look low | Cost data can lag by 8 to 24 hours. Set `REPORT_OFFSET_DAYS=2` to report on the day before yesterday |
| Function never fires | Check that `WATCHDOG_SCHEDULE` is a valid six-field NCRONTAB expression. Schedules are in UTC |
| Slack `invalid_payload` / Teams `400` | Check the webhook URL type. Teams needs a Workflows or connector webhook URL |

## Removing

```bash
az group delete -n rg-cost-watchdog
```

Subscription-level role assignments live outside the resource group and are not removed
with it. Find them with
`az role assignment list --assignee <principalId> --all` and delete them with
`az role assignment delete`.
