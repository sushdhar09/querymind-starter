const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export const apiClient = {
  async executeQuery(request) {
    const res = await fetch(`${API_BASE_URL}/api/v1/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    })
    if (!res.ok) throw new Error(`API Error: ${res.status}`)
    return res.json()
  },
  async getSchema() {
    const res = await fetch(`${API_BASE_URL}/api/v1/admin/schema`)
    return res.json()
  },
  async getExamples() {
    const res = await fetch(`${API_BASE_URL}/api/v1/admin/examples`)
    return res.json()
  },
  async getLogs(limit = 100) {
    const res = await fetch(`${API_BASE_URL}/api/v1/admin/logs?limit=${limit}`)
    return res.json()
  },
  async getGuardrails() {
    const res = await fetch(`${API_BASE_URL}/api/v1/admin/guardrails`)
    return res.json()
  },
  async getModelConfig() {
    const res = await fetch(`${API_BASE_URL}/api/v1/admin/config`)
    return res.json()
  }
}
