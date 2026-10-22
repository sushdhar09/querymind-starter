'use client'
import { useEffect, useRef, useState } from 'react'

export function QueryInput({ value, onChange, onSubmit, isLoading }) {
  const [listening, setListening] = useState(false)
  const [supported, setSupported] = useState(false)
  const recognition = useRef(null)

  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) return
    setSupported(true)
    const rec = new SR()
    rec.lang = 'en-US'
    rec.interimResults = false
    rec.onresult = (e) => onChange(Array.from(e.results).map((r) => r[0].transcript).join(' '))
    rec.onend = () => setListening(false)
    rec.onerror = () => setListening(false)
    recognition.current = rec
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const toggleMic = () => {
    if (listening) recognition.current.stop()
    else { setListening(true); recognition.current.start() }
  }

  return (
    <div className="card" style={{ boxShadow: '0 4px 12px rgba(37, 99, 235, 0.15)', border: '2px solid var(--brand-light)', borderRadius: '16px' }}>
      <div className="row" style={{ gap: '12px' }}>
        <input
          type="text" 
          className="grow" 
          value={value} 
          aria-label="Your question" 
          maxLength={1000}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Ask a question about your data, e.g. Top 5 customers by total amount spent"
          onKeyDown={(e) => { if (e.key === 'Enter' && !isLoading) onSubmit() }}
          style={{ 
            padding: '12px 16px', 
            fontSize: '15px', 
            border: '2px solid var(--border)', 
            borderRadius: '10px',
            transition: 'all 0.2s ease'
          }}
        />
        {supported && (
          <button 
            className={`btn secondary${listening ? ' danger' : ''}`} 
            onClick={toggleMic} 
            title="Speak your question" 
            aria-pressed={listening}
            style={{ 
              padding: '10px 20px', 
              borderRadius: '10px',
              fontWeight: '600',
              transition: 'all 0.2s ease'
            }}
          >
            {listening ? 'Stop' : 'Mic'}
          </button>
        )}
        <button 
          className="btn" 
          onClick={() => onSubmit()} 
          disabled={isLoading || !value.trim()}
          style={{ 
            padding: '10px 24px', 
            borderRadius: '10px',
            fontWeight: '600',
            background: 'var(--brand)',
            boxShadow: '0 2px 8px rgba(37, 99, 235, 0.3)',
            transition: 'all 0.2s ease'
          }}
        >
          {isLoading ? 'Thinking...' : 'Ask'}
        </button>
      </div>
    </div>
  )
}
