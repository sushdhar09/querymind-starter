'use client'
import { useState } from 'react'
import { AutoChart } from '../chart/AutoChart.jsx'
import { DataTable } from '../table/DataTable.jsx'
import { friendlyError, statusTone } from '../../lib/data.js'

function Ambiguities({ items, onChoose }) {
  return (
    <div className="ambiguity" role="group" aria-label="Clarification needed" style={{ 
      background: 'linear-gradient(135deg, var(--warn-bg) 0%, #fef9c3 100%)',
      border: '2px solid var(--warn)',
      borderRadius: '12px',
      padding: '16px'
    }}>
      <strong style={{ color: 'var(--warn)', fontSize: '16px' }}>I need one detail before running this.</strong>
      {items.map((a) => (
        <div key={a.term} style={{ marginTop: 12 }}>
          <div style={{ color: 'var(--text)', marginBottom: 8 }}>{a.message}</div>
          <div className="row" style={{ marginTop: 8 }}>
            {a.candidates.map((c) => (
              <button 
                key={c} 
                className="chip" 
                onClick={() => onChoose(a.term, c)}
                style={{ 
                  background: 'var(--card)',
                  border: '2px solid var(--brand-light)',
                  color: 'var(--brand)',
                  padding: '8px 16px',
                  fontWeight: '600',
                  transition: 'all 0.2s ease'
                }}
              >
                {c}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function Explanation({ response }) {
  const e = response.resolved_entities || {}
  return (
    <div className="notice" style={{ 
      marginTop: 16,
      background: 'linear-gradient(135deg, var(--info-bg) 0%, #e0e7ff 100%)',
      border: '2px solid var(--brand-light)',
      borderRadius: '12px',
      padding: '16px'
    }}>
      {response.explanation && <p style={{ margin: '0 0 12px', color: 'var(--brand-dark)', fontWeight: '600' }}>{response.explanation}</p>}
      <div className="mono" style={{ color: 'var(--brand)', lineHeight: '1.6' }}>
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
  if (loading) return (
    <div className="card muted" style={{ 
      textAlign: 'center',
      padding: '32px',
      background: 'linear-gradient(135deg, var(--info-bg) 0%, var(--card) 100%)',
      border: '2px solid var(--brand-light)'
    }}>
      <div style={{ fontSize: '18px', color: 'var(--brand)', fontWeight: '600' }}>Thinking...</div>
    </div>
  )
  if (error) return (
    <div className="notice bad" role="alert" style={{ 
      background: 'linear-gradient(135deg, var(--bad-bg) 0%, #fecaca 100%)',
      border: '2px solid var(--bad)',
      borderRadius: '12px',
      padding: '16px',
      fontWeight: '600'
    }}>
      {error}
    </div>
  )
  const ambiguities = response.resolved_entities?.ambiguities || []
  const copy = async () => { await navigator.clipboard.writeText(response.generated_sql); setCopied(true); setTimeout(() => setCopied(false), 1500) }

  if (response.status === 'needs_clarification') return (
    <div className="card" style={{ border: '2px solid var(--brand-light)', borderRadius: '16px' }}>
      <Ambiguities items={ambiguities} onChoose={onChoose} />
    </div>
  )
  if (response.status !== 'success') return (
    <div className={`notice ${statusTone(response.status)}`} role="alert" style={{ 
      borderRadius: '12px',
      padding: '16px',
      fontWeight: '600',
      border: '2px solid'
    }}>
      {friendlyError(response)}
    </div>
  )

  const { execution, result } = response
  return (
    <div className="card" style={{ 
      boxShadow: '0 4px 12px rgba(37, 99, 235, 0.1)',
      border: '2px solid var(--brand-light)',
      borderRadius: '16px'
    }}>
      {result.nl_summary && <p style={{ margin: '0 0 16px', fontWeight: '700', fontSize: '17px', color: 'var(--brand-dark)' }}>{result.nl_summary}</p>}
      <div className="row" style={{ marginBottom: 16, gap: '8px' }}>
        <span className="badge" style={{ background: 'var(--info-bg)', color: 'var(--brand-dark)', padding: '6px 12px', fontWeight: '600' }}>{response.query_type}</span>
        <span className="badge ok" style={{ background: 'var(--ok-bg)', color: 'var(--ok)', padding: '6px 12px', fontWeight: '600' }}>{execution.row_count}{execution.truncated ? '+' : ''} rows</span>
        <span className="badge" style={{ background: '#f1f5f9', color: 'var(--muted)', padding: '6px 12px', fontWeight: '600' }}>{response.total_latency_ms} ms</span>
        {response.self_healed && <span className="badge warn" style={{ background: 'var(--warn-bg)', color: 'var(--warn)', padding: '6px 12px', fontWeight: '600' }}>auto-corrected</span>}
        {execution.truncated && <span className="badge warn" style={{ background: 'var(--warn-bg)', color: 'var(--warn)', padding: '6px 12px', fontWeight: '600' }}>truncated</span>}
      </div>
      {ambiguities.length > 0 && (
        <div className="notice warn" style={{ 
          background: 'linear-gradient(135deg, var(--warn-bg) 0%, #fef9c3 100%)',
          border: '2px solid var(--warn)',
          borderRadius: '10px',
          padding: '12px',
          marginBottom: '16px'
        }}>
          Assumed: {ambiguities.map((a) => a.message.replace(/^'(.*?)' is ambiguous\. /, '$1: ')).join(' ')} Turn on "Ask me when unsure" to choose.
        </div>
      )}
      {execution.row_count > 0 ? <DataTable columns={execution.columns} rows={execution.rows} /> : <p className="muted" style={{ textAlign: 'center', padding: '24px', color: 'var(--muted)', fontSize: '15px' }}>No rows matched.</p>}
      <AutoChart columns={execution.columns} rows={execution.rows} />
      {explain && <Explanation response={response} />}
      <details style={{ marginTop: 16 }}>
        <summary className="muted" style={{ cursor: 'pointer', color: 'var(--brand)', fontWeight: '600', padding: '8px 0' }}>Generated SQL</summary>
        <pre className="sql" style={{ 
          background: 'linear-gradient(135deg, #1e3a8a 0%, #1e40af 100%)',
          border: '2px solid var(--brand-light)',
          borderRadius: '10px',
          padding: '16px',
          marginTop: '12px'
        }}>{response.generated_sql}</pre>
        <button 
          className="btn secondary small" 
          style={{ 
            marginTop: 12, 
            padding: '8px 20px',
            borderRadius: '8px',
            fontWeight: '600',
            border: '2px solid var(--brand-light)',
            color: 'var(--brand)',
            transition: 'all 0.2s ease'
          }} 
          onClick={copy}
        >
          {copied ? 'Copied!' : 'Copy SQL'}
        </button>
      </details>
    </div>
  )
}
