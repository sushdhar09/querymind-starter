'use client'
import { useRef, useState } from 'react'
import { Header, PageTitle } from './components/common/Header.jsx'
import { QueryInput } from './components/chat/QueryInput.jsx'
import { AnswerCard } from './components/chat/AnswerCard.jsx'
import { apiClient } from './lib/apiClient.js'

const SUGGESTIONS = [
  'Count orders by status',
  'Top 5 customers by total amount spent',
  'Total revenue per product category',
  'Orders in last 30 days',
  'Show revenue by month',
]

export default function ChatPage() {
  const [query, setQuery] = useState('')
  const [turns, setTurns] = useState([])
  const [clarify, setClarify] = useState(true)
  const [explain, setExplain] = useState(false)
  const nextId = useRef(1)
  const busy = turns.some((t) => t.loading)

  const ask = async (question, choices = {}, replaceId = null) => {
    const q = (question ?? query).trim()
    if (!q) return
    const id = replaceId ?? nextId.current++
    const pending = { id, question: q, choices, loading: true }
    setTurns((prev) => (replaceId ? prev.map((t) => (t.id === id ? pending : t)) : [pending, ...prev]))
    if (!replaceId) setQuery('')
    try {
      const response = await apiClient.executeQuery({ nl_query: q, clarify_ambiguities: clarify, column_choices: choices })
      setTurns((prev) => prev.map((t) => (t.id === id ? { ...pending, loading: false, response } : t)))
    } catch (e) {
      setTurns((prev) => prev.map((t) => (t.id === id ? { ...pending, loading: false, error: e.message } : t)))
    }
  }

  const choose = (turn) => (term, column) => ask(turn.question, { ...turn.choices, [term]: column }, turn.id)

  return (
    <>
      <Header />
      <div className="container">
        <div style={{ marginBottom: '32px' }}>
          <PageTitle title="Ask your data" subtitle="Type a question in plain English. QueryMind writes the SQL, checks it is safe and runs it read-only." />
        </div>
        <div style={{ marginBottom: '24px' }}>
          <QueryInput value={query} onChange={setQuery} onSubmit={() => ask()} isLoading={busy} />
        </div>
        <div className="row" style={{ marginBottom: 24, gap: '12px' }}>
          {SUGGESTIONS.map((s) => (
            <button 
              key={s} 
              className="chip" 
              onClick={() => ask(s)} 
              disabled={busy}
              style={{ 
                background: 'linear-gradient(135deg, var(--card) 0%, var(--info-bg) 100%)',
                border: '2px solid var(--brand-light)',
                color: 'var(--brand)',
                padding: '10px 20px',
                fontWeight: '600',
                borderRadius: '999px',
                boxShadow: '0 2px 8px rgba(37, 99, 235, 0.15)',
                transition: 'all 0.2s ease'
              }}
            >
              {s}
            </button>
          ))}
          <span className="spacer" />
          <label className="row" style={{ color: 'var(--muted)', fontWeight: '500', gap: '8px' }}>
            <input 
              type="checkbox" 
              checked={clarify} 
              onChange={(e) => setClarify(e.target.checked)}
              style={{ accentColor: 'var(--brand)' }}
            /> 
            Ask me when unsure
          </label>
          <label className="row" style={{ color: 'var(--muted)', fontWeight: '500', gap: '8px' }}>
            <input 
              type="checkbox" 
              checked={explain} 
              onChange={(e) => setExplain(e.target.checked)}
              style={{ accentColor: 'var(--brand)' }}
            /> 
            Explain
          </label>
        </div>
        {turns.length === 0 && (
          <div style={{ 
            textAlign: 'center', 
            padding: '48px 24px',
            background: 'linear-gradient(135deg, var(--info-bg) 0%, var(--card) 100%)',
            borderRadius: '16px',
            border: '2px dashed var(--brand-light)'
          }}>
            <p style={{ color: 'var(--brand)', fontSize: '18px', fontWeight: '500' }}>Try one of the suggestions above to get started.</p>
          </div>
        )}
        {turns.map((t) => (
          <div key={t.id} className="turn" style={{ marginBottom: '24px' }}>
            <div className="question" style={{ 
              fontWeight: '700', 
              marginBottom: '12px', 
              fontSize: '18px',
              color: 'var(--brand-dark)',
              padding: '12px 16px',
              background: 'linear-gradient(135deg, var(--info-bg) 0%, var(--card) 100%)',
              borderRadius: '12px',
              border: '2px solid var(--brand-light)'
            }}>
              {t.question}
            </div>
            <AnswerCard turn={t} explain={explain} onChoose={choose(t)} />
          </div>
        ))}
      </div>
    </>
  )
}
