const isNumber = (v) => typeof v === 'number' && Number.isFinite(v)

export function chartSpec(columns, rows, maxPoints = 50) {
  if (!columns || columns.length !== 2 || !rows || rows.length < 2 || rows.length > maxPoints) return null
  const numericIdx = [0, 1].filter((i) => rows.every((r) => r[i] === null || isNumber(r[i])) && rows.some((r) => isNumber(r[i])))
  if (numericIdx.length === 0) return null
  const valueIdx = numericIdx.length === 2 ? 1 : numericIdx[0]
  const labelIdx = 1 - valueIdx
  const points = rows.map((r) => ({ label: String(r[labelIdx]), value: r[valueIdx] ?? 0 }))
  const temporal = points.every((p) => /^\d{4}(-\d{2}(-\d{2})?)?$/.test(p.label))
  return { kind: temporal ? 'line' : 'bar', labelKey: columns[labelIdx], valueKey: columns[valueIdx], points }
}

export function sortRows(rows, key, dir = 'asc') {
  if (!key) return rows
  const sign = dir === 'desc' ? -1 : 1
  return [...rows].sort((a, b) => {
    const x = a[key], y = b[key]
    if (x === y) return 0
    if (x === null || x === undefined) return 1
    if (y === null || y === undefined) return -1
    if (isNumber(x) && isNumber(y)) return (x - y) * sign
    return String(x).localeCompare(String(y), undefined, { numeric: true }) * sign
  })
}

export function nextSort(current, key) {
  if (current.key !== key) return { key, dir: 'asc' }
  return current.dir === 'asc' ? { key, dir: 'desc' } : { key: null, dir: 'asc' }
}

export function parseList(text) {
  return [...new Set(text.split(',').map((s) => s.trim()).filter(Boolean))]
}

export function statusTone(status) {
  return { success: 'ok', blocked: 'warn', needs_clarification: 'warn', error: 'bad' }[status] || ''
}

export function friendlyError(resp) {
  if (!resp) return ''
  const stage = resp.failed_stage ? ` (stopped at ${resp.failed_stage})` : ''
  if (resp.status === 'blocked') return `This request was blocked${stage}: ${resp.error}`
  if (resp.status === 'error') return `Sorry, I could not answer that${stage}: ${resp.error}`
  return resp.error || ''
}

export function formatCell(value) {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return String(value)
}
