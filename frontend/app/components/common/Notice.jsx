export function Notice({ notice, onClose }) {
  if (!notice) return null
  return (
    <div className={`notice ${notice.tone || ''}`} role={notice.tone === 'bad' ? 'alert' : 'status'}>
      <div className="row">
        <span className="grow">{notice.text}</span>
        {onClose && <button className="btn secondary small" onClick={onClose}>Dismiss</button>}
      </div>
    </div>
  )
}

export function LoadState({ loading, error, onRetry }) {
  if (loading) return <p className="muted">Loading...</p>
  if (!error) return null
  return (
    <div className="notice bad" role="alert">
      <div className="row"><span className="grow">{error}</span>
        {onRetry && <button className="btn secondary small" onClick={onRetry}>Retry</button>}</div>
    </div>
  )
}
