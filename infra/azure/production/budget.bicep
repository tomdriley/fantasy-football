targetScope = 'subscription'

@description('Billing currency, not necessarily USD. Notification only, never a spending cap.')
param amount int = 150
param startDate string = '2026-09-01T00:00:00Z'
param endDate string = '2031-09-01T00:00:00Z'

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = {
  name: 'production-monthly-150'
  properties: {
    category: 'Cost'
    amount: amount
    timeGrain: 'Monthly'
    timePeriod: { startDate: startDate, endDate: endDate }
    notifications: {
      actual80: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 80, thresholdType: 'Actual', contactRoles: ['Owner'], contactEmails: [] }
      actual100: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 100, thresholdType: 'Actual', contactRoles: ['Owner'], contactEmails: [] }
      forecast100: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 100, thresholdType: 'Forecasted', contactRoles: ['Owner'], contactEmails: [] }
    }
  }
}
