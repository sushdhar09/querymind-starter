'use client'
import { useEffect, useState } from 'react'
import { Field } from '../../components/common/Field.jsx'
import { PageTitle } from '../../components/common/Header.jsx'
import { LoadState, Notice } from '../../components/common/Notice.jsx'
import { apiClient } from '../../lib/apiClient.js'
import { parseList } from '../../lib/data.js'
import { useAsync, useNotice } from '../../lib/useAsync.js'

const OPERATIONS = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'TRUNCATE', 'ALTER', 'CREATE']

export default function GuardrailsPage() {
  const { data, loading, error, reload } = useAsync(() => apiClient.getGuardrails())
  const [blocked, setBlocked] = useState([])
  const [maxRows, setMaxRows] = useState(100)
  const [prefixes, setPrefixes] = useState('')
  const [tables, setTables] = useState('')
  const [saving, setSaving] = useState(false)
  const [notice, showNotice, clearNotice] = useNotice()

  useEffect(() => {
    if (!data) return
    setBlocked(data.blocked_operations)
    setMaxRows(data.max_rows)
    setPrefixes(data.allowed_table_prefixes.join(', '))
    setTables(data.blocked_tables.join(', '))
  }, [data])

  const toggle = (op) => setBlocked((b) => (b.includes(op) ? b.filter((o) => o !== op) : [...b, op]))
  const unblocked = OPERATIONS.filter((o) => !blocked.includes(o))

  const save = async () => {
    setSaving(true)
    try {
      await apiClient.saveGuardrails({ blocked_operations: blocked, max_rows: Number(maxRows), allowed_table_prefixes: parseList(prefixes), blocked_tables: parseList(tables) })
      showNotice('Guardrails saved. They apply to the next query.')
      reload()
    } catch (e) { showNotice(e.message, 'bad') }
    setSaving(false)
  }

  return (
    <>
      <PageTitle title="Guardrails" subtitle="Control which SQL operations are blocked, how many rows a query may return and which tables it may touch." />
      <Notice notice={notice} onClose={clearNotice} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data && (
        <div className="card">
          <h3>Blocked operations</h3>
          <div className="row" style={{ marginBottom: 8 }}>
            {OPERATIONS.map((op) => (
              <label key={op} className="row"><input type="checkbox" checked={blocked.includes(op)} onChange={() => toggle(op)} /> {op}</label>
            ))}
          </div>
          {unblocked.length > 0 && (
            <div className="notice warn">
              {unblocked.join(', ')} will be allowed through validation. Queries still run on a read-only connection, so data cannot actually change.
            </div>
          )}
          <Field label="Maximum rows per query" hint="A LIMIT is added or lowered to this value (1 to 10000).">
            <input type="number" min={1} max={10000} value={maxRows} onChange={(e) => setMaxRows(e.target.value)} style={{ width: 160 }} />
          </Field>
          <Field label="Allowed table prefixes" hint="Comma separated. Leave empty to allow every table. Example: order, payment">
            <input type="text" value={prefixes} onChange={(e) => setPrefixes(e.target.value)} />
          </Field>
          <Field label="Blocked tables" hint="Internal tables (query_logs, few_shot_examples, schema_descriptions) are always blocked.">
            <input type="text" value={tables} onChange={(e) => setTables(e.target.value)} />
          </Field>
          <button className="btn" onClick={save} disabled={saving}>{saving ? 'Saving...' : 'Save guardrails'}</button>
        </div>
      )}
    </>
  )
}
