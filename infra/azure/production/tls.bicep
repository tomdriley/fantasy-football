targetScope = 'resourceGroup'

// Existing verified hostname bindings and public DNS must precede certificate issuance.
var domains = [
  { certificate: 'thomasriley-ca-managed', hostname: 'thomasriley.ca', app: 'thomasriley-blog-w3-pilot' }
  { certificate: 'www-thomasriley-ca-managed', hostname: 'www.thomasriley.ca', app: 'thomasriley-blog-w3-pilot' }
  { certificate: 'article-service-managed', hostname: 'article-service.thomasriley.ca', app: 'thomasriley-article-w3-pilot' }
]
resource plan 'Microsoft.Web/serverfarms@2024-11-01' existing = { name: 'ff-w3-pilot-plan' }
resource certificates 'Microsoft.Web/certificates@2024-11-01' = [for domain in domains: {
  name: domain.certificate
  location: 'westus3'
  tags: { purpose: 'production', environment: 'production', retention: 'production', owner: 'tomdriley' }
  properties: { canonicalName: domain.hostname, serverFarmId: plan.id }
}]
resource bindings 'Microsoft.Web/sites/hostNameBindings@2024-11-01' = [for (domain, i) in domains: {
  name: '${domain.app}/${domain.hostname}'
  properties: {
    siteName: domain.app
    hostNameType: 'Verified'
    sslState: 'SniEnabled'
    thumbprint: certificates[i].properties.thumbprint
  }
}]
