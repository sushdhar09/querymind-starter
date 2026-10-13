export function SqlPreview({ sql, queryType, latencyMs, summary, ambiguity }) {
  return (
    <div style={{ background: 'white', padding: 16, borderRadius: 12, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
      <div style={{ fontSize: 12, color: '#888' }}>Generated SQL ({queryType}) - {latencyMs}ms</div>
      <pre style={{ background: '#111', color: '#7CFC00', padding: 12, borderRadius: 8, overflow: 'auto', marginTop: 8, fontSize: 13 }}>{sql}</pre>
      {summary && <p style={{ marginTop: 12, fontWeight: 600 }}>💡 {summary}</p>}
      {ambiguity && <div style={{ marginTop: 8, background: '#fff3cd', padding: 8, borderRadius: 6 }}>⚠️ Ambiguity: {ambiguity}</div>}
    </div>
  )
}
