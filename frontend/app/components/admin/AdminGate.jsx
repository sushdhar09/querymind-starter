'use client'
import { useCallback, useEffect, useState } from 'react'
import { adminKey, apiClient } from '../../lib/apiClient.js'

export function AdminGate({ children }) {
  const [state, setState] = useState('checking')
  const [input, setInput] = useState('')
  const [error, setError] = useState('')

  const verify = useCallback(async (key) => {
    setState('checking')
    try {
      await apiClient.verifyKey(key)
      adminKey.set(key)
      setError('')
      setState('ok')
    } catch (e) {
      adminKey.clear()
      setError(e.status === 401 ? 'That admin key was not accepted.' : e.status === 503 ? 'The server has no ADMIN_API_KEY configured.' : e.message)
      setState('locked')
    }
  }, [])

  useEffect(() => {
    const stored = adminKey.get()
    if (stored) verify(stored)
    else setState('locked')
    const onAuthFailed = () => { adminKey.clear(); setError('Your session expired. Enter the admin key again.'); setState('locked') }
    window.addEventListener('qm-auth-failed', onAuthFailed)
    return () => window.removeEventListener('qm-auth-failed', onAuthFailed)
  }, [verify])

  if (state === 'checking') return <div className="container muted">Checking access...</div>
  if (state === 'locked') {
    return (
      <div className="container">
        <form className="card gate" onSubmit={(e) => { e.preventDefault(); verify(input.trim()) }}>
          <h2>Admin access</h2>
          <p className="muted">Enter the admin API key configured on the server (ADMIN_API_KEY). It is kept for this browser tab only.</p>
          {error && <div className="notice bad" role="alert">{error}</div>}
          <label className="field">Admin key
            <input type="password" value={input} onChange={(e) => setInput(e.target.value)} autoFocus autoComplete="off" />
          </label>
          <button className="btn" type="submit" disabled={!input.trim()}>Unlock</button>
        </form>
      </div>
    )
  }
  return (
    <div className="container">
      <div className="row" style={{ justifyContent: 'flex-end', marginBottom: 8 }}>
        <button className="btn secondary small" onClick={() => { adminKey.clear(); setInput(''); setState('locked') }}>Sign out</button>
      </div>
      {children}
    </div>
  )
}
