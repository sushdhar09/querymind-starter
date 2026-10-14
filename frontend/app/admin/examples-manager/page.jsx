'use client'
import { useState } from 'react'
import { Field } from '../../components/common/Field.jsx'
import { PageTitle } from '../../components/common/Header.jsx'
import { LoadState, Notice } from '../../components/common/Notice.jsx'
import { apiClient } from '../../lib/apiClient.js'
import { useAsync, useNotice } from '../../lib/useAsync.js'

const TYPES = ['SELECT_SIMPLE', 'SELECT_AGGREGATE', 'SELECT_JOIN', 'SELECT_TEMPORAL']
const EMPTY = { question: '', sql: '', query_type: 'SELECT_SIMPLE' }

function ExampleForm({ initial, editing, onSubmit, onCancel }) {
  const [form, setForm] = useState(initial)
  const [saving, setSaving] = useState(false)
  const [problems, setProblems] = useState([])
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setProblems([])
    try {
      await onSubmit(form)
      if (!editing) setForm(EMPTY)
    } catch (err) {
      setProblems(err.detail?.errors?.length ? err.detail.errors : [err.message])
    }
    setSaving(false)
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>{editing ? `Edit example #${editing}` : 'Add an example'}</h3>
      {problems.length > 0 && <div className="notice bad" role="alert">{problems.map((p) => <div key={p}>{p}</div>)}</div>}
      <Field label="Question"><input type="text" value={form.question} onChange={set('question')} required maxLength={1000} /></Field>
      <Field label="SQL" hint="Must be a single SELECT that passes validation against the current schema."><textarea rows={4} value={form.sql} onChange={set('sql')} required /></Field>
      <Field label="Query type">
        <select value={form.query_type} onChange={set('query_type')} style={{ width: 240 }}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select>
      </Field>
      <div className="row">
        <button className="btn" type="submit" disabled={saving}>{saving ? 'Saving...' : editing ? 'Save changes' : 'Add example'}</button>
        {editing && <button className="btn secondary" type="button" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  )
}

export default function ExamplesManagerPage() {
  const [type, setType] = useState('')
  const [filter, setFilter] = useState('')
  const [editing, setEditing] = useState(null)
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState(null)
  const [notice, showNotice, clearNotice] = useNotice()
  const list = useAsync(() => apiClient.getExamples({ query_type: type, search: filter }), [type, filter])

  const runSearch = async (e) => {
    e.preventDefault()
    if (!query.trim()) return setMatches(null)
    try { setMatches(await apiClient.searchExamples(query, { top_k: 5 })) } catch (err) { showNotice(err.message, 'bad') }
  }
  const create = async (form) => { await apiClient.createExample(form); showNotice('Example added.'); list.reload() }
  const update = async (form) => { await apiClient.updateExample(editing.id, form); showNotice('Example updated.'); setEditing(null); list.reload() }
  const remove = async (ex) => {
    if (!window.confirm(`Delete this example?\n\n${ex.question}`)) return
    try { await apiClient.deleteExample(ex.id); showNotice('Example deleted.'); list.reload() } catch (err) { showNotice(err.message, 'bad') }
  }

  return (
    <>
      <PageTitle title="Examples Manager" subtitle="Curated question and SQL pairs. The closest ones are shown to the model as few-shot examples." />
      <Notice notice={notice} onClose={clearNotice} />
      {editing
        ? <ExampleForm key={editing.id} initial={{ question: editing.question, sql: editing.sql, query_type: editing.query_type }} editing={editing.id} onSubmit={update} onCancel={() => setEditing(null)} />
        : <ExampleForm key="new" initial={EMPTY} onSubmit={create} />}

      <div className="card">
        <h3>Semantic search</h3>
        <form className="row" onSubmit={runSearch}>
          <input type="text" className="grow" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Describe a question to see which examples the model would get" aria-label="Semantic search" />
          <button className="btn" type="submit">Search</button>
          {matches && <button className="btn secondary" type="button" onClick={() => { setMatches(null); setQuery('') }}>Clear</button>}
        </form>
        {matches && (
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="data">
              <thead><tr><th>Similarity</th><th>Question</th><th>SQL</th></tr></thead>
              <tbody>{matches.map((m) => (
                <tr key={m.question}><td>{m.score?.toFixed(2)}</td><td>{m.question}</td><td className="mono">{m.sql}</td></tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>All examples {list.data && <span className="muted">({list.data.length})</span>}</h3>
          <span className="spacer" />
          <select value={type} onChange={(e) => setType(e.target.value)} style={{ width: 200 }} aria-label="Filter by type">
            <option value="">All types</option>{TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
          <input type="text" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by text" style={{ width: 220 }} aria-label="Filter by text" />
        </div>
        <LoadState loading={list.loading && !list.data} error={list.error} onRetry={list.reload} />
        {list.data && (list.data.length === 0 ? <p className="muted">No examples match.</p> : (
          <div className="table-wrap" style={{ maxHeight: 520 }}>
            <table className="data">
              <thead><tr><th>Question</th><th>SQL</th><th>Type</th><th /></tr></thead>
              <tbody>{list.data.map((ex) => (
                <tr key={ex.id}>
                  <td>{ex.question}</td>
                  <td className="mono" style={{ maxWidth: 420 }}>{ex.sql}</td>
                  <td><span className="badge">{ex.query_type.replace('SELECT_', '')}</span></td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn secondary small" onClick={() => { setEditing(ex); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>Edit</button>{' '}
                    <button className="btn danger small" onClick={() => remove(ex)}>Delete</button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ))}
      </div>
    </>
  )
}
