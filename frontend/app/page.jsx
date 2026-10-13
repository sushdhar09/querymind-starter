'use client'
import { useState } from 'react'
import { Header } from './components/common/Header.jsx'
import { QueryInput } from './components/chat/QueryInput.jsx'
import { SqlPreview } from './components/sql/SqlPreview.jsx'
import { ResultTable } from './components/table/ResultTable.jsx'
import { AdminNav } from './components/admin/AdminNav.jsx'
import { apiClient } from './lib/apiClient.js'

export default function HomePage() {
  const [query, setQuery] = useState('Count orders by status')
  const [response, setResponse] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    setIsLoading(true)
    setError('')
    try {
      const data = await apiClient.executeQuery({ nl_query: query })
      setResponse(data)
    } catch (e) {
      setError('Backend not running on :8000. Run uvicorn app.main:app --reload in backend folder')
    }
    setIsLoading(false)
  }

  return (
    <div style={{ minHeight: '100vh', padding: 24 }}>
      <div style={{ maxWidth: 1000, margin: '0 auto' }}>
        <Header title="QueryMind AI" subtitle="Natural Language Analytics for E-commerce" />
        <QueryInput value={query} onChange={setQuery} onSubmit={handleSubmit} isLoading={isLoading} />
        {error && <div style={{ background: '#fee', padding: 12, borderRadius: 8, marginBottom: 12 }}>{error}</div>}
        {response && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <SqlPreview
              sql={response.generated_sql || response.error}
              queryType={response.query_type}
              latencyMs={response.execution?.latency_ms || 0}
              summary={response.result?.nl_summary}
              ambiguity={response.resolved_entities?.ambiguities?.[0]?.message}
            />
            <ResultTable data={response.result?.table} rowCount={response.execution?.row_count || 0} />
          </div>
        )}
        <AdminNav />
      </div>
    </div>
  )
}
