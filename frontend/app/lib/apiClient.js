export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const API = `${API_BASE_URL}/api/v1`
const KEY_STORAGE = 'qm_admin_key'

export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

export const adminKey = {
  get: () => (typeof window === 'undefined' ? '' : window.sessionStorage.getItem(KEY_STORAGE) || ''),
  set: (key) => window.sessionStorage.setItem(KEY_STORAGE, key),
  clear: () => window.sessionStorage.removeItem(KEY_STORAGE),
}

export function describeError(detail, fallback) {
  if (!detail) return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
  if (detail.message) return [detail.message, ...(detail.errors || [])].join(': ')
  return fallback
}

async function request(path, { method = 'GET', body, admin = false, raw = false, key } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (admin) headers['X-API-Key'] = key ?? adminKey.get()
  let res
  try {
    res = await fetch(`${API}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  } catch {
    throw new ApiError(0, `Cannot reach the backend at ${API_BASE_URL}. Is it running?`)
  }
  if (!res.ok) {
    let detail
    try { detail = (await res.json()).detail } catch { /* non-JSON error body */ }
    if (res.status === 401 && admin && typeof window !== 'undefined') window.dispatchEvent(new Event('qm-auth-failed'))
    const fallback = res.status === 429 ? 'Too many requests, please wait a moment.' : `Request failed (${res.status})`
    throw new ApiError(res.status, describeError(detail, fallback), detail)
  }
  if (raw) return res
  if (res.status === 204) return null
  return res.json()
}

const qs = (params) => {
  const p = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p.set(k, v) })
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const apiClient = {
  executeQuery: (body) => request('/query', { method: 'POST', body }),
  verifyKey: (key) => request('/admin/stats', { admin: true, key }),
  getStats: () => request('/admin/stats', { admin: true }),
  getSchema: () => request('/admin/schema', { admin: true }),
  updateColumn: (table, column, meta) => request(`/admin/schema/${table}/${column}`, { method: 'PUT', body: meta, admin: true }),
  getExamples: (params = {}) => request(`/admin/examples${qs({ limit: 500, ...params })}`, { admin: true }),
  searchExamples: (q, params = {}) => request(`/admin/examples/search${qs({ q, ...params })}`, { admin: true }),
  createExample: (ex) => request('/admin/examples', { method: 'POST', body: ex, admin: true }),
  updateExample: (id, ex) => request(`/admin/examples/${id}`, { method: 'PUT', body: ex, admin: true }),
  deleteExample: (id) => request(`/admin/examples/${id}`, { method: 'DELETE', admin: true }),
  async getLogs(params = {}) {
    const res = await request(`/admin/logs${qs(params)}`, { admin: true, raw: true })
    return { items: await res.json(), total: Number(res.headers.get('X-Total-Count') || 0) }
  },
  async exportLogs(params = {}) {
    const res = await request(`/admin/logs/export${qs(params)}`, { admin: true, raw: true })
    return res.blob()
  },
  getGuardrails: () => request('/admin/guardrails', { admin: true }),
  saveGuardrails: (g) => request('/admin/guardrails', { method: 'PUT', body: g, admin: true }),
  getModelConfig: () => request('/admin/config', { admin: true }),
  saveModelConfig: (c) => request('/admin/config', { method: 'PUT', body: c, admin: true }),
}
