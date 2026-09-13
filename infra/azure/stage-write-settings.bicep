targetScope = 'resourceGroup'

@description('Fresh private stage app-settings snapshot. Must retain the enrolled Google allowlist, secret reference, exact host and reader settings. Operator validates it before deployment.')
@secure()
param existingAppSettings object

@description('Existing isolated writer credential secret name, never its value.')
@minLength(1)
@maxLength(127)
param writerSecretName string = 'pg-write-probe-password'

resource parent 'Microsoft.Web/sites@2024-11-01' existing = {
  name: 'thomasriley-fantasy-w3-pilot'
}

resource stage 'Microsoft.Web/sites/slots@2024-11-01' existing = {
  parent: parent
  name: 'stage'
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: 'tr-ff-w3-kv-fcbc'
}

resource secret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: writerSecretName
}

resource writerSecretGrant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secret.id, stage.id, 'hosting-write-secret-v1')
  scope: secret
  properties: {
    principalId: stage.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

// No identity/authorization parameter and no authsettings resource: preserve enrollment exactly.
resource settings 'Microsoft.Web/sites/slots/config@2024-11-01' = {
  parent: stage
  name: 'appsettings'
  properties: union(existingAppSettings, {
    FFOPT_HOSTING_PHASE: 'authenticated-write'
    FFOPT_WRITE_DB_HOST: existingAppSettings.FFOPT_DB_HOST
    FFOPT_WRITE_DB_NAME: existingAppSettings.FFOPT_DB_NAME
    FFOPT_WRITE_DB_USER: 'ffopt_stage_probe_writer'
    FFOPT_WRITE_DB_PASSWORD: '@Microsoft.KeyVault(SecretUri=https://${vault.name}.vault.azure.net/secrets/${secret.name})'
    FFOPT_WRITE_DB_PORT: '5432'
    FFOPT_WRITE_DB_SSLMODE: 'verify-full'
  })
  dependsOn: [writerSecretGrant]
}
