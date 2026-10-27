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
  'Products with low stock',
  'Average rating per product',
]

const FEATURES = [
  {
    icon: '💬',
    title: 'Natural Language',
    description: 'Ask in plain English, no SQL needed'
  },
  {
    icon: '🔒',
    title: 'Safe & Read-Only',
    description: 'Only SELECT operations, data protected'
  },
  {
    icon: '⚡',
    title: 'Instant Results',
    description: 'Fast SQL generation with caching'
  },
  {
    icon: '📊',
    title: 'Visualizations',
    description: 'Clean tables with auto charts'
  },
  {
    icon: '🎯',
    title: 'Smart Schema',
    description: 'Auto maps queries to right tables'
  },
  {
    icon: '🔄',
    title: 'Self-Healing',
    description: 'Auto-fixes failed queries'
  }
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
        {/* Hero Section */}
        {turns.length === 0 && (
          <div className="hero-section">
            <div className="hero-content">
              <h1 className="hero-title">
                <span className="hero-gradient">AskData</span>
              </h1>
              <p className="hero-subtitle">
                Transform natural language into SQL queries instantly. Ask your data questions in plain English and get accurate, safe results.
              </p>
              <div className="hero-badges">
                <span className="hero-badge">🚀 Fast</span>
                <span className="hero-badge">🔒 Secure</span>
                <span className="hero-badge">🎯 Accurate</span>
              </div>
            </div>
          </div>
        )}

        {/* Features Section - Only show when no queries */}
        {turns.length === 0 && (
          <div className="features-section">
            <h2 className="section-title">What AskData Can Do</h2>
            <div className="features-grid">
              {FEATURES.map((feature, index) => (
                <div key={index} className="feature-card">
                  <h3 className="feature-title">
                    <span className="feature-icon">{feature.icon}</span>
                    {feature.title}
                  </h3>
                  <p className="feature-description">{feature.description}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Query Section */}
        <div className="query-section">
          <div style={{ marginBottom: '24px' }}>
            <PageTitle title="Ask your data" subtitle="Type a question in plain English. QueryMind writes the SQL, checks it is safe and runs it read-only." />
          </div>
          <div style={{ marginBottom: '20px' }}>
            <QueryInput value={query} onChange={setQuery} onSubmit={() => ask()} isLoading={busy} />
          </div>
          <div className="row" style={{ marginBottom: 20, gap: '12px' }}>
            {SUGGESTIONS.map((s) => (
              <button 
                key={s} 
                className="chip suggestion-chip" 
                onClick={() => ask(s)} 
                disabled={busy}
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
            <div className="empty-state">
              <p>Try one of the suggestions above to get started, or type your own question.</p>
            </div>
          )}
        </div>

        {/* Query Results */}
        {turns.map((t) => (
          <div key={t.id} className="turn" style={{ marginBottom: '24px' }}>
            <div className="question">
              {t.question}
            </div>
            <AnswerCard turn={t} explain={explain} onChoose={choose(t)} />
          </div>
        ))}
      </div>
    </>
  )
}
