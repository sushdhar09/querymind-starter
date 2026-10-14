'use client'
import { useEffect, useState } from 'react'
import { Field } from '../../components/common/Field.jsx'
import { PageTitle } from '../../components/common/Header.jsx'
import { LoadState, Notice } from '../../components/common/Notice.jsx'
import { apiClient } from '../../lib/apiClient.js'
import { useAsync, useNotice } from '../../lib/useAsync.js'

const PROVIDERS = [
  ['mock', 'Mock (offline templates)'],
  ['openai', 'OpenAI'],
  ['anthropic', 'Anthropic'],
  ['ollama', 'Ollama (local)'],
]

export default function ModelConfigPage() {
  const { data, loading, error, reload } = useAsync(() => apiClient.getModelConfig())
  const [form, setForm] = useState(null)
  const [saving, setSaving] = useState(false)
  const [notice, showNotice, clearNotice] = useNotice()
  useEffect(() => { if (data) setForm({ ...data }) }, [data])
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))

  const save = async () => {
    setSaving(true)
    try {
      const { api_key_configured, ...body } = form
      await apiClient.saveModelConfig({ ...body, temperature: Number(body.temperature), max_tokens: Number(body.max_tokens) })
      showNotice('Model configuration saved. It applies to the next query.')
      reload()
    } catch (e) { showNotice(e.message, 'bad') }
    setSaving(false)
  }

  return (
    <>
      <PageTitle title="Model Config" subtitle="Choose the LLM provider and generation settings. API keys stay on the server and are never shown here." />
      <Notice notice={notice} onClose={clearNotice} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {form && (
        <div className="card">
          <Field label="Provider" hint={form.provider !== 'mock' && !data.api_key_configured ? 'No API key is configured on the server for the saved provider.' : undefined}>
            <select value={form.provider} onChange={set('provider')}>
              {PROVIDERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </Field>
          <Field label="Model" hint="For example gpt-4o-mini, claude-sonnet-4-20250514 or llama3. Ignored by the mock provider.">
            <input type="text" value={form.model} onChange={set('model')} />
          </Field>
          <Field label={`Temperature: ${form.temperature}`} hint="0 gives the most deterministic SQL.">
            <input type="range" min={0} max={2} step={0.1} value={form.temperature} onChange={set('temperature')} />
          </Field>
          <Field label="Max tokens" hint="Token budget for each model response (64 to 32000).">
            <input type="number" min={64} max={32000} value={form.max_tokens} onChange={set('max_tokens')} style={{ width: 160 }} />
          </Field>
          <Field label="SQL dialect">
            <select value={form.dialect} onChange={set('dialect')} style={{ width: 200 }}>
              <option value="sqlite">SQLite</option>
              <option value="postgresql">PostgreSQL</option>
              <option value="mysql">MySQL</option>
            </select>
          </Field>
          <label className="row" style={{ marginBottom: 12 }}>
            <input type="checkbox" checked={form.llm_summary} onChange={set('llm_summary')} />
            Write result summaries with the LLM (sends up to 20 result rows to the provider)
          </label>
          <button className="btn" onClick={save} disabled={saving}>{saving ? 'Saving...' : 'Save configuration'}</button>
        </div>
      )}
    </>
  )
}
