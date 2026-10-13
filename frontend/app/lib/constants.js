export const APP_NAME = 'QueryMind AI'
export const API_BASE_URL = 'http://localhost:8000'

export const ADMIN_PANELS = [
  { id: 'schema', label: 'Schema Manager', icon: '📊', path: '/admin/schema-manager' },
  { id: 'examples', label: 'Examples Manager', icon: '📚', path: '/admin/examples-manager' },
  { id: 'logs', label: 'Query Logs', icon: '📝', path: '/admin/query-logs' },
  { id: 'guardrails', label: 'Guardrails Config', icon: '🛡️', path: '/admin/guardrails-config' },
  { id: 'config', label: 'Model Config', icon: '⚙️', path: '/admin/model-config' },
]
