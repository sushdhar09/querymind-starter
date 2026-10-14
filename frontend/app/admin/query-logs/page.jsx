'use client'
import { useState } from 'react'
import { PageTitle } from '../../components/common/Header.jsx'
import { LoadState, Notice } from '../../components/common/Notice.jsx'
import { apiClient } from '../../lib/apiClient.js'
import { statusTone } from '../../lib/data.js'
import { useAsync, useNotice } from '../../lib/useAsync.js'

const PAGE_SIZE = 25

export default function QueryLogsPage() {
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [applied, setApplied] = useState({ status: '', search: '' })
  const [page, setPage] = useState(0)
  const [notice, showNotice, clearNotice] = useNotice()
  const logs = useAsync(() => apiClient.getLogs({ ...applied, limit: PAGE_SIZE, offset: page * PAGE_SIZE }), [applied, page])
  const stats = useAsync(() => apiClient.getStats(), [])

  const apply = (e) => { e.preventDefault(); setPage(0); setApplied({ status, search }) }
  const refresh = () => { logs.reload(); stats.reload() }
  const total = logs.data?.total || 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const exportCsv = async () => {
    try {
      const blob = await apiClient.exportLogs(applied)
      const url = URL.createObjectURL(blob)
      const a = Object.assign(document.createElement('a'), { href: url, download: 'query_logs.csv' })
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) { showNotice(e.message, 'bad') }
  }

  const s = stats.data
  return (
    <>
      <PageTitle title="Query Logs" subtitle="Every question asked, the SQL that was generated and how it ended." />
      <Notice notice={notice} onClose={clearNotice} />
      {s && (
        <div className="stats">
          <div className="stat"><div className="muted">Total queries</div><div className="value">{s.total}</div></div>
          <div className="stat"><div className="muted">Success rate</div><div className="value">{(s.success_rate * 100).toFixed(0)}%</div></div>
          <div className="stat"><div className="muted">Avg latency</div><div className="value">{Math.round(s.avg_latency_ms)} ms</div></div>
          <div className="stat"><div className="muted">Blocked / errors</div><div className="value">{s.by_status.blocked || 0} / {s.by_status.error || 0}</div></div>
        </div>
      )}
      <div className="card">
        <form className="row" onSubmit={apply} style={{ marginBottom: 12 }}>
          <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 180 }} aria-label="Status filter">
            <option value="">All statuses</option>
            <option value="success">Success</option>
            <option value="error">Error</option>
            <option value="blocked">Blocked</option>
            <option value="needs_clarification">Needs clarification</option>
          </select>
          <input type="text" className="grow" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search question or SQL" aria-label="Search logs" />
          <button className="btn" type="submit">Filter</button>
          <button className="btn secondary" type="button" onClick={refresh}>Refresh</button>
          <button className="btn secondary" type="button" onClick={exportCsv}>Export CSV</button>
        </form>
        <LoadState loading={logs.loading && !logs.data} error={logs.error} onRetry={logs.reload} />
        {logs.data && (logs.data.items.length === 0 ? <p className="muted">No log entries match.</p> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Time</th><th>Question</th><th>SQL</th><th>Status</th><th>Latency</th><th>Rows</th></tr></thead>
              <tbody>
                {logs.data.items.map((l) => (
                  <tr key={l.id}>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{l.created_at}</td>
                    <td>{l.nl_query}</td>
                    <td className="mono" style={{ maxWidth: 320 }} title={l.generated_sql}>{l.generated_sql.slice(0, 90)}{l.generated_sql.length > 90 ? '...' : ''}</td>
                    <td><span className={`badge ${statusTone(l.status)}`}>{l.status}</span>{l.error && <div className="muted" title={l.error}>{l.failed_stage}: {l.error.slice(0, 60)}</div>}</td>
                    <td>{l.latency_ms} ms</td>
                    <td>{l.row_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        <div className="pager">
          <span className="muted">{total} entries, page {page + 1} of {pages}</span>
          <button className="btn secondary small" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <button className="btn secondary small" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      </div>
    </>
  )
}
