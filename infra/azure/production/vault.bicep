targetScope = 'resourceGroup'

param operatorId string
param tenantId string

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'tr-ff-w3-kv-fcbc'
  location: 'westus3'
  tags: {
    purpose: 'production'
    environment: 'production'
    retention: 'production'
    owner: 'tomdriley'
  }
  properties: {
    tenantId: tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enabledForTemplateDeployment: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
    accessPolicies: []
  }
}

resource officer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, operatorId, 'secret-setup')
  scope: vault
  properties: {
    principalId: operatorId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
  }
}
