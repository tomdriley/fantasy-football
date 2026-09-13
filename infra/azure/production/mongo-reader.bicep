targetScope = 'resourceGroup'

@description('Existing production_reader password, not an account key. Preserve it on redeployment.')
@secure()
param readerPassword string

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: 'tr-blog-w3-fcbc'
}
resource role 'Microsoft.DocumentDB/databaseAccounts/mongodbRoleDefinitions@2024-05-15' = {
  parent: cosmos
  name: 'blog-site.ArticleFindOnly'
  properties: {
    roleName: 'ArticleFindOnly'
    type: 'CustomRole'
    databaseName: 'blog-site'
    privileges: [{ resource: { db: 'blog-site', collection: 'articles2' }, actions: ['find'] }]
    roles: []
  }
}
resource reader 'Microsoft.DocumentDB/databaseAccounts/mongodbUserDefinitions@2024-05-15' = {
  parent: cosmos
  name: 'blog-site.production_reader'
  properties: {
    userName: 'production_reader'
    password: readerPassword
    databaseName: 'blog-site'
    mechanisms: 'SCRAM-SHA-256'
    roles: [{ db: 'blog-site', role: 'ArticleFindOnly' }]
  }
  dependsOn: [role]
}
