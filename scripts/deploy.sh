#!/usr/bin/env bash
# One-command deployment: infrastructure (Bicep) + function code.
#
# Usage:
#   SLACK_WEBHOOK_URL=... TEAMS_WEBHOOK_URL=... ./scripts/deploy.sh -g rg-cost-watchdog -l eastus [-n cost-watchdog]
#
# Requires: Azure CLI (logged in with Owner or User Access Administrator on the
# monitored subscriptions, needed for the role assignments) and Azure Functions Core Tools v4.
set -euo pipefail

RESOURCE_GROUP=""
LOCATION=""
APP_NAME="cost-watchdog"
EXTRA_PARAMS=()

usage() {
  echo "Usage: $0 -g <resource-group> -l <location> [-n <app-name>] [-p key=value ...]" >&2
  exit 1
}

while getopts "g:l:n:p:h" opt; do
  case "$opt" in
    g) RESOURCE_GROUP="$OPTARG" ;;
    l) LOCATION="$OPTARG" ;;
    n) APP_NAME="$OPTARG" ;;
    p) EXTRA_PARAMS+=("$OPTARG") ;;
    *) usage ;;
  esac
done
[[ -z "$RESOURCE_GROUP" || -z "$LOCATION" ]] && usage

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Creating resource group $RESOURCE_GROUP in $LOCATION"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "==> Deploying infrastructure"
FUNCTION_APP=$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --name "azure-cost-watchdog" \
  --template-file "$ROOT/infra/main.bicep" \
  --parameters appName="$APP_NAME" \
               slackWebhookUrl="${SLACK_WEBHOOK_URL:-}" \
               teamsWebhookUrl="${TEAMS_WEBHOOK_URL:-}" \
               ${EXTRA_PARAMS[@]+"${EXTRA_PARAMS[@]}"} \
  --query properties.outputs.functionAppName.value \
  --output tsv)

echo "==> Publishing code to $FUNCTION_APP"
cd "$ROOT"
# Role assignments can take a minute to propagate; retry the publish a few times.
for attempt in 1 2 3; do
  if func azure functionapp publish "$FUNCTION_APP" --python; then
    break
  fi
  [[ $attempt -eq 3 ]] && exit 1
  echo "Publish failed, retrying in 30s..."
  sleep 30
done

echo "==> Done. Function App: $FUNCTION_APP"
echo "    Dry run: curl \"https://$FUNCTION_APP.azurewebsites.net/api/run?dryRun=true&code=<function-key>\""
