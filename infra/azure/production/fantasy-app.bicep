targetScope = 'resourceGroup'

@minLength(71)
@maxLength(71)
param imageDigest string

resource plan 'Microsoft.Web/serverfarms@2024-11-01' existing = {
  name: 'ff-w3-pilot-plan'
}
resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: 'ff-w3-pilot-vnet'
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: 'tr-ff-w3-kv-fcbc'
}
resource secret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'pg-reader-password'
}

resource parent 'Microsoft.Web/sites@2024-11-01' = {
  name: 'thomasriley-fantasy-w3-pilot'
  location: 'westus3'
  kind: 'app,linux'
  tags: { purpose: 'production', environment: 'production', retention: 'production', owner: 'tomdriley' }
  properties: {
    serverFarmId: plan.id
    reserved: true
    enabled: false
    httpsOnly: true
    publicNetworkAccess: 'Disabled'
    siteConfig: {
      alwaysOn: false
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ipSecurityRestrictionsDefaultAction: 'Deny'
      scmIpSecurityRestrictionsUseMain: true
    }
  }
}
resource stage 'Microsoft.Web/sites/slots@2024-11-01' = {
  parent: parent
  name: 'stage'
  location: 'westus3'
  kind: 'app,linux'
  tags: { purpose: 'production', environment: 'stage', retention: 'production', owner: 'tomdriley' }
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    reserved: true
    enabled: true
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    virtualNetworkSubnetId: '${vnet.id}/subnets/app-integration'
    siteConfig: {
      linuxFxVersion: 'DOCKER|ghcr.io/tomdriley/fantasy-football-hosting@${imageDigest}'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      healthCheckPath: '/healthz'
      scmIpSecurityRestrictionsDefaultAction: 'Deny'
      scmIpSecurityRestrictionsUseMain: false
    }
  }
}
resource readerGrant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secret.id, stage.id, 'reader-secret')
  scope: secret
  properties: {
    principalId: stage.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}
resource settings 'Microsoft.Web/sites/slots/config@2024-11-01' = {
  parent: stage
  name: 'appsettings'
  properties: {
    FFOPT_HOSTING_ENVIRONMENT: 'stage'
    FFOPT_HOSTING_PHASE: 'database-readonly'
    FFOPT_HOSTING_ALLOWED_HOSTS: stage.properties.defaultHostName
    FFOPT_DB_HOST: 'thomasriley-ff-w3-pg.postgres.database.azure.com'
    FFOPT_DB_NAME: 'hosting_stage'
    FFOPT_DB_USER: 'ffopt_stage_reader'
    FFOPT_DB_PASSWORD: '@Microsoft.KeyVault(SecretUri=https://${vault.name}.vault.azure.net/secrets/pg-reader-password)'
    FFOPT_DB_SSLMODE: 'verify-full'
    WEBSITES_PORT: '8080'
    WEBSITES_ENABLE_APP_SERVICE_STORAGE: 'false'
  }
  dependsOn: [readerGrant]
}
resource ftp 'Microsoft.Web/sites/slots/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: stage
  name: 'ftp'
  properties: { allow: false }
}
resource scm 'Microsoft.Web/sites/slots/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: stage
  name: 'scm'
  properties: { allow: false }
}

output stageOrigin string = 'https://${stage.properties.defaultHostName}'
