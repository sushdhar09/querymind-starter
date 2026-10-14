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
    <div className="card">
      <div className="row">
        <input
          type="text" className="grow" value={value} aria-label="Your question" maxLength={1000}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Ask a question about your data, e.g. Top 5 customers by total amount spent"
          onKeyDown={(e) => { if (e.key === 'Enter' && !isLoading) onSubmit() }}
        />
        {supported && (
          <button className={`btn secondary${listening ? ' danger' : ''}`} onClick={toggleMic} title="Speak your question" aria-pressed={listening}>
            {listening ? 'Stop' : 'Mic'}
          </button>
        )}
        <button className="btn" onClick={() => onSubmit()} disabled={isLoading || !value.trim()}>{isLoading ? 'Thinking...' : 'Ask'}</button>
      </div>
    </div>
  )
}
