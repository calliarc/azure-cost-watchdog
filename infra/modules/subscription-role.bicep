// Assigns a built-in role to a service principal at subscription scope.
targetScope = 'subscription'

@description('Object ID of the managed identity.')
param principalId string

@description('Built-in role definition GUID, e.g. 72fafb9e-0641-4937-9268-a91bfd8191a3 (Cost Management Reader).')
param roleDefinitionId string

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, principalId, roleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output roleAssignmentId string = assignment.id
