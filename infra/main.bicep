// Azure Cost Watchdog: Function App (Flex Consumption) + storage + Application Insights,
// with a system-assigned managed identity granted "Cost Management Reader" on each
// monitored subscription. No storage keys or Azure credentials are stored.
//
// Deploy (resource group scope):
//   az deployment group create -g <rg> -f infra/main.bicep \
//     -p slackWebhookUrl=$SLACK_WEBHOOK_URL teamsWebhookUrl=$TEAMS_WEBHOOK_URL

targetScope = 'resourceGroup'

@description('Base name used for all resources.')
@minLength(3)
@maxLength(20)
param appName string = 'cost-watchdog'

@description('Region. Must support the Flex Consumption plan.')
param location string = resourceGroup().location

@description('Subscriptions to monitor. The Function identity gets Cost Management Reader on each one. Defaults to the deployment subscription.')
param costSubscriptionIds array = [
  subscription().subscriptionId
]

@description('Six-field NCRONTAB schedule, evaluated in UTC.')
param schedule string = '0 0 7 * * *'

@secure()
@description('Slack incoming webhook URL (optional).')
param slackWebhookUrl string = ''

@secure()
@description('Microsoft Teams Workflows / incoming webhook URL (optional).')
param teamsWebhookUrl string = ''

@description('"always" posts a daily summary; "anomalies" posts only when an anomaly is detected.')
@allowed([
  'always'
  'anomalies'
])
param notifyMode string = 'always'

@description('Minimum percent change vs the rolling average to count as an anomaly.')
param anomalyThresholdPercent string = '25'

@description('Minimum absolute change (billing currency) vs the rolling average to count as an anomaly.')
param anomalyThresholdAbsolute string = '10'

@description('Days in the rolling average baseline.')
param rollingWindowDays string = '7'

@allowed([
  'ActualCost'
  'AmortizedCost'
])
param costType string = 'ActualCost'

@allowed([
  '3.10'
  '3.11'
  '3.12'
])
param pythonVersion string = '3.11'

param tags object = {
  app: 'azure-cost-watchdog'
}

var suffix = uniqueString(resourceGroup().id, appName)
var functionAppName = toLower('${appName}-${suffix}')
var storageAccountName = toLower('st${take(replace(appName, '-', ''), 9)}${take(suffix, 13)}')
var deploymentContainerName = 'app-package-${take(suffix, 8)}'

// Built-in role definition IDs
var roles = {
  costManagementReader: '72fafb9e-0641-4937-9268-a91bfd8191a3'
  storageBlobDataOwner: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
  storageQueueDataContributor: '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
  storageTableDataContributor: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
  monitoringMetricsPublisher: '3913510d-42f4-4e42-8a64-420c390055eb'
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${appName}-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${appName}-${suffix}'
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    defaultToOAuthAuthentication: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: deploymentContainerName
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'plan-${appName}-${suffix}'
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storage.name
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'APPLICATIONINSIGHTS_AUTHENTICATION_STRING'
          value: 'Authorization=AAD'
        }
        {
          name: 'WATCHDOG_SCHEDULE'
          value: schedule
        }
        {
          name: 'COST_SUBSCRIPTION_IDS'
          value: join(costSubscriptionIds, ',')
        }
        {
          name: 'SLACK_WEBHOOK_URL'
          value: slackWebhookUrl
        }
        {
          name: 'TEAMS_WEBHOOK_URL'
          value: teamsWebhookUrl
        }
        {
          name: 'NOTIFY_MODE'
          value: notifyMode
        }
        {
          name: 'ANOMALY_THRESHOLD_PERCENT'
          value: anomalyThresholdPercent
        }
        {
          name: 'ANOMALY_THRESHOLD_ABSOLUTE'
          value: anomalyThresholdAbsolute
        }
        {
          name: 'ROLLING_WINDOW_DAYS'
          value: rollingWindowDays
        }
        {
          name: 'COST_TYPE'
          value: costType
        }
        {
          // Deploy without webhooks to verify with /api/run?dryRun=true first.
          name: 'DRY_RUN'
          value: empty(slackWebhookUrl) && empty(teamsWebhookUrl) ? 'true' : 'false'
        }
      ]
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaling: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'python'
        version: pythonVersion
      }
    }
  }
  dependsOn: [
    deploymentContainer
  ]
}

// Data-plane roles so the Functions host can use storage without account keys.
var storageRoles = [
  roles.storageBlobDataOwner
  roles.storageQueueDataContributor
  roles.storageTableDataContributor
]

resource storageRoleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for roleId in storageRoles: {
    name: guid(storage.id, functionApp.id, roleId)
    scope: storage
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
      principalId: functionApp.identity.principalId
      principalType: 'ServicePrincipal'
    }
  }
]

resource appInsightsRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, functionApp.id, roles.monitoringMetricsPublisher)
  scope: appInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.monitoringMetricsPublisher)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Cost Management Reader at subscription scope, for every monitored subscription.
module costReader 'modules/subscription-role.bicep' = [
  for (subId, i) in costSubscriptionIds: {
    name: 'cost-reader-${i}-${take(suffix, 6)}'
    scope: subscription(subId)
    params: {
      principalId: functionApp.identity.principalId
      roleDefinitionId: roles.costManagementReader
    }
  }
]

output functionAppName string = functionApp.name
output functionAppHostName string = functionApp.properties.defaultHostName
output principalId string = functionApp.identity.principalId
output storageAccountName string = storage.name
output appInsightsName string = appInsights.name
