'use client'
import { useState } from 'react'
import { PageTitle } from '../../components/common/Header.jsx'
import { LoadState, Notice } from '../../components/common/Notice.jsx'
import { apiClient } from '../../lib/apiClient.js'
import { parseList } from '../../lib/data.js'
import { useAsync, useNotice } from '../../lib/useAsync.js'

function ColumnRow({ table, column, onSaved, onError }) {
  const [description, setDescription] = useState(column.description)
  const [synonyms, setSynonyms] = useState(column.synonyms.join(', '))
  const [sensitive, setSensitive] = useState(column.is_sensitive)
  const [saving, setSaving] = useState(false)
  const dirty = description !== column.description || synonyms !== column.synonyms.join(', ') || sensitive !== column.is_sensitive

  const save = async () => {
    setSaving(true)
    try {
      await apiClient.updateColumn(table, column.name, { description, synonyms: parseList(synonyms), is_sensitive: sensitive })
      onSaved(`Saved ${table}.${column.name}`)
    } catch (e) { onError(e.message) }
    setSaving(false)
  }

  return (
    <tr>
      <td className="mono">{column.name}{column.primary_key && <span className="badge" style={{ marginLeft: 6 }}>PK</span>}</td>
      <td className="muted">{column.type}</td>
      <td><textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} aria-label={`Description of ${column.name}`} /></td>
      <td><input type="text" value={synonyms} onChange={(e) => setSynonyms(e.target.value)} placeholder="comma separated" aria-label={`Synonyms of ${column.name}`} /></td>
      <td style={{ textAlign: 'center' }}><input type="checkbox" checked={sensitive} onChange={(e) => setSensitive(e.target.checked)} aria-label={`${column.name} is sensitive`} /></td>
      <td><button className="btn small" disabled={!dirty || saving} onClick={save}>{saving ? 'Saving...' : 'Save'}</button></td>
    </tr>
  )
}

export default function SchemaManagerPage() {
  const { data, loading, error, reload } = useAsync(() => apiClient.getSchema())
  const [selected, setSelected] = useState(null)
  const [notice, showNotice, clearNotice] = useNotice()
  const table = data?.find((t) => t.table_name === selected) || data?.[0]

  return (
    <>
      <PageTitle title="Schema Manager" subtitle="Describe columns in business language and mark sensitive columns. Sensitive columns are hidden from the LLM and blocked in queries." />
      <Notice notice={notice} onClose={clearNotice} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {table && (
        <div className="card">
          <div className="row" style={{ marginBottom: 12 }}>
            <label htmlFor="table-select" className="muted">Table</label>
            <select id="table-select" style={{ width: 240 }} value={table.table_name} onChange={(e) => setSelected(e.target.value)}>
              {data.map((t) => <option key={t.table_name} value={t.table_name}>{t.table_name}</option>)}
            </select>
            <span className="muted">{table.columns.length} columns, {table.columns.filter((c) => c.is_sensitive).length} sensitive</span>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Column</th><th>Type</th><th style={{ width: '34%' }}>Description</th><th style={{ width: '22%' }}>Synonyms</th><th>Sensitive</th><th /></tr></thead>
              <tbody>
                {table.columns.map((c) => (
                  <ColumnRow key={`${table.table_name}.${c.name}.${c.description}.${c.synonyms.join()}.${c.is_sensitive}`}
                    table={table.table_name} column={c}
                    onSaved={(m) => { showNotice(m); reload() }} onError={(m) => showNotice(m, 'bad')} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
