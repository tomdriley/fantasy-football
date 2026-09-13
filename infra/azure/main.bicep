targetScope = 'resourceGroup'

@description('Approved fantasy-only WestUS3 pilot parent; production remains disabled.')
@allowed(['thomasriley-fantasy-w3-pilot'])
@minLength(3)
@maxLength(60)
param appName string

@description('Already published and anonymously pullable reviewed foundation digest: sha256:<64 lowercase hex>.')
@minLength(71)
@maxLength(71)
param imageDigest string

var appKind = 'app,linux'

resource sharedPlan 'Microsoft.Web/serverfarms@2024-11-01' existing = {
  name: 'ff-w3-pilot-plan'
  scope: resourceGroup('b9ee5d35-c096-4772-8a56-0529054b4dcf', 'ff-westus3-pilot')
}

resource parent 'Microsoft.Web/sites@2024-11-01' = {
  name: appName
  location: 'westus3'
  kind: appKind
  properties: {
    serverFarmId: sharedPlan.id
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
  kind: appKind
  properties: {
    serverFarmId: sharedPlan.id
    reserved: true
    enabled: true
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    clientAffinityEnabled: false
    siteConfig: {
      linuxFxVersion: 'DOCKER|ghcr.io/tomdriley/fantasy-football-hosting@${imageDigest}'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      healthCheckPath: '/healthz'
      ipSecurityRestrictionsDefaultAction: 'Allow'
      scmIpSecurityRestrictionsUseMain: false
      scmIpSecurityRestrictionsDefaultAction: 'Deny'
    }
  }
}

// Configure after creation to use Azure's real hostname, including newer unique DNS names.
// Preserve the separately provisioned read-only PostgreSQL connection settings.
resource stageSettings 'Microsoft.Web/sites/slots/config@2024-11-01' = {
  parent: stage
  name: 'appsettings'
  properties: union(list('${stage.id}/config/appsettings', '2024-11-01').properties, {
    FFOPT_HOSTING_ENVIRONMENT: 'stage'
    FFOPT_HOSTING_ALLOWED_HOSTS: stage.properties.defaultHostName
    WEBSITES_PORT: '8080'
    WEBSITES_ENABLE_APP_SERVICE_STORAGE: 'false'
    DOCKER_REGISTRY_SERVER_URL: 'https://ghcr.io'
  })
}

resource stickySettings 'Microsoft.Web/sites/config@2024-11-01' = {
  parent: parent
  name: 'slotConfigNames'
  properties: {
    appSettingNames: [
      'FFOPT_HOSTING_ENVIRONMENT'
      'FFOPT_HOSTING_ALLOWED_HOSTS'
      'WEBSITES_PORT'
      'WEBSITES_ENABLE_APP_SERVICE_STORAGE'
      'DOCKER_REGISTRY_SERVER_URL'
    ]
  }
}

resource parentFtp 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: parent
  name: 'ftp'
  properties: {
    allow: false
  }
}

resource parentScm 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: parent
  name: 'scm'
  properties: {
    allow: false
  }
}

resource stageFtp 'Microsoft.Web/sites/slots/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: stage
  name: 'ftp'
  properties: {
    allow: false
  }
}

resource stageScm 'Microsoft.Web/sites/slots/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: stage
  name: 'scm'
  properties: {
    allow: false
  }
}

output stageResourceId string = stage.id
output stageOrigin string = 'https://${stage.properties.defaultHostName}'
output referencedPlanId string = sharedPlan.id
