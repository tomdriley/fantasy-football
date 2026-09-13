targetScope = 'resourceGroup'

@description('Operator-approved Google Web application OAuth client ID. No secret values belong in this template.')
@minLength(1)
param googleClientId string

resource parent 'Microsoft.Web/sites@2024-11-01' existing = {
  name: 'thomasriley-fantasy-w3-pilot'
}

resource stage 'Microsoft.Web/sites/slots@2024-11-01' existing = {
  parent: parent
  name: 'stage'
}

// Deliberately separate from the base app and settings: never disable auth on rollback.
resource auth 'Microsoft.Web/sites/slots/config@2024-11-01' = {
  parent: stage
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: true
      runtimeVersion: '~1'
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
      redirectToProvider: 'google'
      excludedPaths: [
        '/healthz'
        '/readyz'
        '/fantasy-football'
        '/fantasy-football/'
        '/fantasy-football/api/status'
      ]
    }
    identityProviders: {
      azureActiveDirectory: { enabled: false }
      facebook: { enabled: false }
      gitHub: { enabled: false }
      twitter: { enabled: false }
      legacyMicrosoftAccount: { enabled: false }
      apple: { enabled: false }
      google: {
        enabled: true
        registration: {
          clientId: googleClientId
          clientSecretSettingName: 'GOOGLE_PROVIDER_AUTHENTICATION_SECRET'
        }
        login: {
          scopes: ['openid', 'profile', 'email']
        }
        validation: {
          allowedAudiences: [googleClientId]
        }
      }
    }
    httpSettings: {
      requireHttps: true
      forwardProxy: { convention: 'NoProxy' }
      routes: { apiPrefix: '/.auth' }
    }
    login: {
      allowedExternalRedirectUrls: []
      tokenStore: { enabled: false }
      nonce: { validateNonce: true }
      cookieExpiration: {
        convention: 'FixedTime'
        timeToExpiration: '01:00:00'
      }
    }
  }
}
