'use client'
import { useState } from 'react'
import { AutoChart } from '../chart/AutoChart.jsx'
import { DataTable } from '../table/DataTable.jsx'
import { friendlyError, statusTone } from '../../lib/data.js'

function Ambiguities({ items, onChoose }) {
  return (
    <div className="ambiguity" role="group" aria-label="Clarification needed">
      <strong>I need one detail before running this.</strong>
      {items.map((a) => (
        <div key={a.term} style={{ marginTop: 8 }}>
          <div>{a.message}</div>
          <div className="row" style={{ marginTop: 6 }}>
            {a.candidates.map((c) => <button key={c} className="chip" onClick={() => onChoose(a.term, c)}>{c}</button>)}
          </div>
        </div>
      ))}
    </div>
  )
}

function Explanation({ response }) {
  const e = response.resolved_entities || {}
  return (
    <div className="notice" style={{ marginTop: 12 }}>
      {response.explanation && <p style={{ margin: '0 0 6px' }}>{response.explanation}</p>}
      <div className="mono">
        tables: {e.resolved_tables?.join(', ') || '-'}<br />
        columns: {e.resolved_columns?.join(', ') || '-'}<br />
        filters: {e.value_hits?.join('; ') || '-'}
      </div>
    </div>
  )
}

export function AnswerCard({ turn, explain, onChoose }) {
  const [copied, setCopied] = useState(false)
  const { response, error, loading } = turn
  if (loading) return <div className="card muted">Thinking...</div>
  if (error) return <div className="notice bad" role="alert">{error}</div>
  const ambiguities = response.resolved_entities?.ambiguities || []
  const copy = async () => { await navigator.clipboard.writeText(response.generated_sql); setCopied(true); setTimeout(() => setCopied(false), 1500) }

  if (response.status === 'needs_clarification') return <div className="card"><Ambiguities items={ambiguities} onChoose={onChoose} /></div>
  if (response.status !== 'success') return <div className={`notice ${statusTone(response.status)}`} role="alert">{friendlyError(response)}</div>

  const { execution, result } = response
  return (
    <div className="card">
      {result.nl_summary && <p style={{ margin: '0 0 12px', fontWeight: 600, fontSize: 15 }}>{result.nl_summary}</p>}
      <div className="row" style={{ marginBottom: 12 }}>
        <span className="badge">{response.query_type}</span>
        <span className="badge ok">{execution.row_count}{execution.truncated ? '+' : ''} rows</span>
        <span className="badge">{response.total_latency_ms} ms</span>
        {response.self_healed && <span className="badge warn">auto-corrected</span>}
        {execution.truncated && <span className="badge warn">truncated</span>}
      </div>
      {ambiguities.length > 0 && (
        <div className="notice warn">Assumed: {ambiguities.map((a) => a.message.replace(/^'(.*?)' is ambiguous\. /, '$1: ')).join(' ')} Turn on "Ask me when unsure" to choose.</div>
      )}
      {execution.row_count > 0 ? <DataTable columns={execution.columns} rows={execution.rows} /> : <p className="muted">No rows matched.</p>}
      <AutoChart columns={execution.columns} rows={execution.rows} />
      {explain && <Explanation response={response} />}
      <details style={{ marginTop: 12 }}>
        <summary className="muted" style={{ cursor: 'pointer' }}>Generated SQL</summary>
        <pre className="sql">{response.generated_sql}</pre>
        <button className="btn secondary small" style={{ marginTop: 8 }} onClick={copy}>{copied ? 'Copied' : 'Copy SQL'}</button>
      </details>
    </div>
  )
}
