'use client'
import { useEffect, useState } from 'react'
import { apiClient } from '../../lib/apiClient.js'
import { Header } from '../../components/common/Header.jsx'

export default function Page() {
  const [data, setData] = useState(null)
  useEffect(() => {
    const fn = {
      'schema-manager': apiClient.getSchema,
      'examples-manager': apiClient.getExamples,
      'query-logs': apiClient.getLogs,
      'guardrails-config': apiClient.getGuardrails,
      'model-config': apiClient.getModelConfig,
    }['examples-manager']
    fn().then(setData).catch(() => setData({ error: 'Backend not running on :8000' }))
  }, [])
  return (
    <div style={{ padding: 24, maxWidth: 1000, margin: '0 auto' }}>
      <a href="/">← Back to Chat</a>
      <Header title="Examples Manager" subtitle="Curate few-shot question → SQL pairs" />
      <pre style={{ background: '#111', color: '#0f0', padding: 12, borderRadius: 8, overflow: 'auto', fontSize: 12 }}>{JSON.stringify(data, null, 2)}</pre>
    </div>
  )
}
