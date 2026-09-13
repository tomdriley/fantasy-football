targetScope = 'resourceGroup'

@description('Private provider/subject allowlist; empty denies all sample access. No emails or tokens.')
@secure()
param authorization object = {}

@description('Existing operator-provisioned Google OAuth secret name, never its value.')
@minLength(1)
@maxLength(127)
param googleSecretName string = 'google-auth-stage-client-secret'

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

@description('Operator-provisioned Google secret; this template neither reads nor creates its value.')
resource secret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: googleSecretName
}

resource googleSecretGrant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secret.id, stage.id, 'google-auth-secret-v1')
  scope: secret
  properties: {
    principalId: stage.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

// Requires separately approved operator privileges; the routine deployer cannot list settings.
resource settings 'Microsoft.Web/sites/slots/config@2024-11-01' = {
  parent: stage
  name: 'appsettings'
  properties: union(list('${stage.id}/config/appsettings', '2024-11-01').properties, {
    FFOPT_HOSTING_PHASE: 'authentication-only'
    FFOPT_AUTH_ALLOWED_IDENTITIES: string(authorization.?identities ?? [])
    GOOGLE_PROVIDER_AUTHENTICATION_SECRET: '@Microsoft.KeyVault(SecretUri=https://${vault.name}.vault.azure.net/secrets/${secret.name})'
  })
  dependsOn: [googleSecretGrant]
}
