targetScope = 'resourceGroup'

@description('Existing secret-scoped assignment UUID from Azure; preserve it to avoid RoleAssignmentExists. Use a fresh UUID only for reconstruction.')
param readerRoleAssignmentName string

@description('Reviewed immutable container references, including @sha256:, never mutable tags.')
param blogImage string = 'ghcr.io/tomdriley/thomasriley-ca@sha256:fbecb3ba7a21fdc3f42c5d04d886a1917ef0a35d338ad2e42c9441c4c80833a4'
param articleImage string = 'ghcr.io/tomdriley/thomasriley-ca@sha256:e28899c43a741bc8ed06056b979c3fd45368417929428a106c6df4fbbd94982b'
param blogStageImage string = 'ghcr.io/tomdriley/thomasriley-ca@sha256:7e4fa8d436082f06bbc7a11412b0b8563e0c470ea1f6fe78394f13b1af2f3d3f'
param articleStageImage string = 'ghcr.io/tomdriley/thomasriley-ca@sha256:2db49690adb853885af370854a4a1ff2832a16424a36d94938e83580bd33a140'

var tags = { purpose: 'production', environment: 'production', retention: 'production', owner: 'tomdriley' }
var commonSettings = [
  { name: 'WEBSITES_PORT', value: '8080' }
  { name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE', value: 'false' }
]
var apps = [
  { name: 'thomasriley-article-w3-pilot', image: articleImage, stageImage: articleStageImage }
  { name: 'thomasriley-blog-w3-pilot', image: blogImage, stageImage: blogStageImage }
]
resource plan 'Microsoft.Web/serverfarms@2024-11-01' existing = { name: 'ff-w3-pilot-plan' }
resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = { name: 'ff-w3-pilot-vnet' }
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: 'tr-ff-w3-kv-fcbc' }
resource secret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'blog-production-mongo-reader'
}
resource sites 'Microsoft.Web/sites@2024-11-01' = [for (app, i) in apps: {
  name: app.name
  location: 'westus3'
  kind: 'app,linux,container'
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    reserved: true
    enabled: true
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    virtualNetworkSubnetId: '${vnet.id}/subnets/app-integration'
    siteConfig: {
      linuxFxVersion: 'DOCKER|${app.image}'
      alwaysOn: true
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ftpsState: 'Disabled'
      scmIpSecurityRestrictionsDefaultAction: 'Deny'
      scmIpSecurityRestrictionsUseMain: false
      appSettings: concat(commonSettings, i == 0 ? [
        { name: 'MONGO_DATABASE', value: 'blog-site' }
        { name: 'MONGO_ARTICLES_COLLECTION', value: 'articles2' }
      ] : [{ name: 'ARTICLE_SERVICE_URI', value: 'https://thomasriley-article-w3-pilot.azurewebsites.net' }])
      connectionStrings: i == 0 ? [{
        name: 'AZURE_TOMRILEY_BLOG_DB'
        type: 'Custom'
        connectionString: '@Microsoft.KeyVault(SecretUri=https://${vault.name}.vault.azure.net/secrets/${secret.name})'
      }] : []
    }
  }
}]
resource slots 'Microsoft.Web/sites/slots@2024-11-01' = [for (app, i) in apps: {
  parent: sites[i]
  name: 'stage'
  location: 'westus3'
  kind: 'app,linux,container'
  tags: union(tags, { environment: 'stage' })
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    reserved: true
    enabled: true
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    virtualNetworkSubnetId: '${vnet.id}/subnets/app-integration'
    siteConfig: {
      linuxFxVersion: 'DOCKER|${app.stageImage}'
      alwaysOn: true
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ftpsState: 'Disabled'
      scmIpSecurityRestrictionsDefaultAction: 'Deny'
      scmIpSecurityRestrictionsUseMain: false
      appSettings: concat(commonSettings, i == 0
        ? [{ name: 'ARTICLE_DATA_MODE', value: 'synthetic' }]
        : [{ name: 'ARTICLE_SERVICE_URI', value: 'https://thomasriley-article-w3-pilot-stage.azurewebsites.net' }])
      connectionStrings: []
    }
  }
}]
resource readerGrant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: readerRoleAssignmentName
  scope: secret
  properties: {
    principalId: sites[0].identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}
resource ftp 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = [for (app, i) in apps: {
  parent: sites[i]
  name: 'ftp'
  properties: { allow: false }
}]
resource scm 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = [for (app, i) in apps: {
  parent: sites[i]
  name: 'scm'
  properties: { allow: false }
}]
resource stageFtp 'Microsoft.Web/sites/slots/basicPublishingCredentialsPolicies@2024-11-01' = [for (app, i) in apps: {
  parent: slots[i]
  name: 'ftp'
  properties: { allow: false }
}]
resource stageScm 'Microsoft.Web/sites/slots/basicPublishingCredentialsPolicies@2024-11-01' = [for (app, i) in apps: {
  parent: slots[i]
  name: 'scm'
  properties: { allow: false }
}]
