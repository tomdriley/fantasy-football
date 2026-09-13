targetScope = 'resourceGroup'

var tags = { purpose: 'production', environment: 'production', retention: 'production', owner: 'tomdriley' }
resource owners 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'production-subscription-owners'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'ProdOwners'
    enabled: true
    armRoleReceivers: [{
      name: 'SubscriptionOwners'
      roleId: '8e3af657-a8ff-443c-a75c-2fe8c4bcb635'
      useCommonAlertSchema: true
    }]
  }
}
var rules = [
  { name: 'production-plan-cpu', type: 'Microsoft.Web/serverFarms', resource: 'ff-w3-pilot-plan', metric: 'CpuPercentage', threshold: 80, aggregation: 'Average' }
  { name: 'production-plan-memory', type: 'Microsoft.Web/serverFarms', resource: 'ff-w3-pilot-plan', metric: 'MemoryPercentage', threshold: 85, aggregation: 'Average' }
  { name: 'thomasriley-blog-w3-pilot-http5xx', type: 'Microsoft.Web/sites', resource: 'thomasriley-blog-w3-pilot', metric: 'Http5xx', threshold: 5, aggregation: 'Total' }
  { name: 'thomasriley-article-w3-pilot-http5xx', type: 'Microsoft.Web/sites', resource: 'thomasriley-article-w3-pilot', metric: 'Http5xx', threshold: 5, aggregation: 'Total' }
  { name: 'production-postgres-storage', type: 'Microsoft.DBforPostgreSQL/flexibleServers', resource: 'thomasriley-ff-w3-pg', metric: 'storage_percent', threshold: 80, aggregation: 'Average' }
]
resource alerts 'Microsoft.Insights/metricAlerts@2018-03-01' = [for rule in rules: {
  name: rule.name
  location: 'global'
  tags: tags
  properties: {
    description: 'Production capacity or availability requires operator investigation.'
    severity: 2
    enabled: true
    scopes: [resourceId(rule.type, rule.resource)]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    autoMitigate: true
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [{
        name: rule.name
        metricNamespace: rule.type
        metricName: rule.metric
        operator: 'GreaterThan'
        threshold: rule.threshold
        timeAggregation: rule.aggregation
        criterionType: 'StaticThresholdCriterion'
      }]
    }
    actions: [{ actionGroupId: owners.id }]
  }
}]
