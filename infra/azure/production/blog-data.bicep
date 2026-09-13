targetScope = 'resourceGroup'

var tags = { purpose: 'production', environment: 'production', retention: 'production', owner: 'tomdriley' }
resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: 'ff-w3-pilot-vnet'
}
resource plan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: 'ff-w3-pilot-plan'
  location: 'westus3'
  kind: 'linux'
  tags: tags
  sku: { name: 'P0v3', tier: 'PremiumV3', capacity: 1 }
  properties: { reserved: true }
}
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'tr-blog-w3-fcbc'
  location: 'westus3'
  kind: 'MongoDB'
  tags: tags
  properties: {
    databaseAccountOfferType: 'Standard'
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: false
    apiProperties: { serverVersion: '4.0' }
    capabilities: [for name in ['EnableMongo', 'DisableRateLimitingResponses', 'EnableServerless', 'EnableMongoRoleBasedAccessControl']: { name: name }]
    locations: [{ locationName: 'westus3', failoverPriority: 0, isZoneRedundant: false }]
    backupPolicy: {
      type: 'Periodic'
      periodicModeProperties: {
        backupIntervalInMinutes: 240
        backupRetentionIntervalInHours: 24
        backupStorageRedundancy: 'Geo'
      }
    }
  }
}
// Data and indexes are restored separately; do not replace live collection index definitions.
resource database 'Microsoft.DocumentDB/databaseAccounts/mongodbDatabases@2024-05-15' = {
  parent: cosmos
  name: 'blog-site'
  properties: { resource: { id: 'blog-site' }, options: {} }
}
resource dns 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.mongo.cosmos.azure.com'
  location: 'global'
  tags: tags
}
resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dns
  name: 'blog-canary-link'
  location: 'global'
  tags: tags
  properties: { registrationEnabled: false, virtualNetwork: { id: vnet.id } }
}
resource endpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'blog-canary-mongo-pe'
  location: 'westus3'
  tags: tags
  properties: {
    subnet: { id: '${vnet.id}/subnets/private-endpoints' }
    privateLinkServiceConnections: [{
      name: 'mongo'
      properties: { privateLinkServiceId: cosmos.id, groupIds: ['MongoDB'] }
    }]
  }
}
resource zoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: endpoint
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: 'mongo', properties: { privateDnsZoneId: dns.id } }] }
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'trblogw3fcbc'
  location: 'westus3'
  kind: 'StorageV2'
  tags: tags
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: true
    allowSharedKeyAccess: true
    publicNetworkAccess: 'Enabled'
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: true
    deleteRetentionPolicy: { enabled: true, days: 30 }
    containerDeleteRetentionPolicy: { enabled: true, days: 30 }
  }
}
resource backups 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'migration-backups'
  properties: { publicAccess: 'None' }
}
resource synthetic 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'synthetic'
  properties: { publicAccess: 'Blob' }
}
resource images 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'img'
  properties: { publicAccess: 'Blob' }
}
