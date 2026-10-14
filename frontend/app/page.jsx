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
        <PageTitle title="Ask your data" subtitle="Type a question in plain English. QueryMind writes the SQL, checks it is safe and runs it read-only." />
        <QueryInput value={query} onChange={setQuery} onSubmit={() => ask()} isLoading={busy} />
        <div className="row" style={{ marginBottom: 16 }}>
          {SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => ask(s)} disabled={busy}>{s}</button>)}
          <span className="spacer" />
          <label className="row"><input type="checkbox" checked={clarify} onChange={(e) => setClarify(e.target.checked)} /> Ask me when unsure</label>
          <label className="row"><input type="checkbox" checked={explain} onChange={(e) => setExplain(e.target.checked)} /> Explain</label>
        </div>
        {turns.length === 0 && <p className="muted">Try one of the suggestions above.</p>}
        {turns.map((t) => (
          <div key={t.id} className="turn">
            <div className="question">{t.question}</div>
            <AnswerCard turn={t} explain={explain} onChoose={choose(t)} />
          </div>
        ))}
      </div>
    </>
  )
}
