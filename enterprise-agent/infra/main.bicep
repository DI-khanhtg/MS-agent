// Deployment template only. Connections/resources are provisioned separately.
param location string = resourceGroup().location
param name string = 'enterprise-agent'
@allowed(['staging', 'production'])
param environment string = 'staging'
param image string
param tenantId string
param backendClientId string
param frontendClientId string
@secure()
param geminiKey string
@secure()
param clientSecret string
@secure()
param databaseUrl string
// Existing Container Apps environment with an Azure Files mount named agent-data.
param containerEnvironmentId string
param allowedHosts string

resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: '${name}-${environment}'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: containerEnvironmentId
    configuration: {
      activeRevisionsMode: 'Multiple'
      ingress: { external: true, targetPort: 8000, transport: 'auto' }
      secrets: [
        { name: 'gemini-key', value: geminiKey }
        { name: 'client-secret', value: clientSecret }
        { name: 'database-url', value: databaseUrl }
      ]
    }
    template: {
      containers: [{
        name: 'agent'
        image: image
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: [
          { name: 'APP_ENV', value: environment }
          { name: 'RUNTIME_MODE', value: 'gemini' }
          { name: 'AUTH_ENABLED', value: 'true' }
          { name: 'STORAGE_BACKEND', value: 'database' }
          { name: 'ENABLED_TOOL_GROUPS', value: 'workiq_read,artifact_generation' }
          { name: 'AZURE_TENANT_ID', value: tenantId }
          { name: 'AUTH_ALLOWED_TENANT_IDS', value: tenantId }
          { name: 'AZURE_CLIENT_ID', value: backendClientId }
          { name: 'ENTRA_API_AUDIENCE', value: backendClientId }
          { name: 'ENTRA_ALLOWED_CLIENT_IDS', value: frontendClientId }
          { name: 'ALLOWED_HOSTS', value: allowedHosts }
          { name: 'GEMINI_API_KEY', secretRef: 'gemini-key' }
          { name: 'AZURE_CLIENT_SECRET', secretRef: 'client-secret' }
          { name: 'DATABASE_URL', secretRef: 'database-url' }
        ]
        volumeMounts: [{ volumeName: 'data', mountPath: '/app/data' }]
        probes: [
          { type: 'Liveness', httpGet: { path: '/health', port: 8000 }, initialDelaySeconds: 15 }
          { type: 'Readiness', httpGet: { path: '/health', port: 8000 }, initialDelaySeconds: 15 }
        ]
      }]
      volumes: [{ name: 'data', storageType: 'AzureFile', storageName: 'agent-data' }]
      // Persistent SDK sessions and local rate limits currently require a single instance.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}
output fqdn string = app.properties.configuration.ingress.fqdn
