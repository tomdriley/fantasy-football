targetScope = 'resourceGroup'

@description('The approved fantasy-only parent created by main.bicep.')
@minLength(3)
@maxLength(60)
param appName string

resource parent 'Microsoft.Web/sites@2024-11-01' existing = {
  name: appName
}

resource stage 'Microsoft.Web/sites/slots@2024-11-01' existing = {
  parent: parent
  name: 'stage'
}

resource deployer 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: '${appName}-stage-deployer'
  location: 'eastus'
}

resource federation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2024-11-30' = {
  parent: deployer
  name: 'github-main'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:tomdriley@17971412/fantasy-football@1355269013:ref:refs/heads/main'
    audiences: ['api://AzureADTokenExchange']
  }
}

resource role 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(stage.id, 'foundation-stage-image-deployer-v1')
  properties: {
    roleName: '${appName}-stage-image-deployer'
    description: 'Read, update configuration, and restart only the assigned fantasy stage slot.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: [
          'Microsoft.Web/sites/slots/read'
          'Microsoft.Web/sites/slots/config/read'
          'Microsoft.Web/sites/slots/config/write'
          'Microsoft.Web/sites/slots/restart/action'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

resource stageAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(stage.id, deployer.id, role.id)
  scope: stage
  properties: {
    principalId: deployer.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: role.id
  }
}

output clientId string = deployer.properties.clientId
output tenantId string = tenant().tenantId
output roleAssignmentScope string = stage.id
