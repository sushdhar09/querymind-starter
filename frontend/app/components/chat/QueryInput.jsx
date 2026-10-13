export function QueryInput({ value, onChange, onSubmit, isLoading }) {
  return (
    <div style={{ background: 'white', borderRadius: 12, padding: 16, display: 'flex', gap: 8, boxShadow: '0 1px 3px rgba(0,0,0,0.1)', marginBottom: 16 }}>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="e.g. Top 10 products by revenue last quarter"
        style={{ flex: 1, border: '1px solid #ddd', borderRadius: 8, padding: '10px 12px' }}
        onKeyDown={(e) => { if (e.key === 'Enter') onSubmit() }}
      />
      <button
        onClick={onSubmit}
        disabled={isLoading}
        style={{ background: 'black', color: 'white', padding: '10px 20px', borderRadius: 8, cursor: 'pointer' }}
      >
        {isLoading ? 'Thinking...' : 'Ask'}
      </button>
    </div>
  )
}
