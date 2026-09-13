targetScope = 'resourceGroup'

@secure()
param adminPassword string

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'ff-w3-pilot-vnet'
  location: 'westus3'
  tags: { purpose: 'production', environment: 'production', retention: 'production', owner: 'tomdriley' }
  properties: {
    addressSpace: { addressPrefixes: ['10.241.0.0/16'] }
    subnets: [
      {
        name: 'app-integration'
        properties: {
          addressPrefix: '10.241.0.0/26'
          delegations: [{ name: 'app', properties: { serviceName: 'Microsoft.Web/serverFarms' } }]
        }
      }
      {
        name: 'postgres'
        properties: {
          addressPrefix: '10.241.1.0/27'
          delegations: [{ name: 'postgres', properties: { serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers' } }]
        }
      }
      {
        name: 'admin-jobs'
        properties: {
          addressPrefix: '10.241.2.0/24'
          delegations: [{ name: 'jobs', properties: { serviceName: 'Microsoft.ContainerInstance/containerGroups' } }]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.241.3.0/27'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource dns 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'ff-w3-pilot.postgres.database.azure.com'
  location: 'global'
  tags: { purpose: 'production', environment: 'production', retention: 'production', owner: 'tomdriley' }
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dns
  name: 'pilot-vnet'
  location: 'global'
  tags: { purpose: 'production', environment: 'production', retention: 'production', owner: 'tomdriley' }
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: 'thomasriley-ff-w3-pg'
  location: 'westus3'
  tags: { purpose: 'production', environment: 'production', retention: 'production', owner: 'tomdriley' }
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '17'
    administratorLogin: 'ffoptadmin'
    administratorLoginPassword: adminPassword
    storage: { storageSizeGB: 32, autoGrow: 'Disabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: {
      publicNetworkAccess: 'Disabled'
      delegatedSubnetResourceId: '${vnet.id}/subnets/postgres'
      privateDnsZoneArmResourceId: dns.id
    }
  }
  dependsOn: [link]
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: server
  name: 'hosting_stage'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

output serverId string = server.id
output fqdn string = server.properties.fullyQualifiedDomainName
