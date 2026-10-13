export function Header({ title, subtitle }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <h1 style={{ fontSize: 28, fontWeight: 800, margin: 0 }}>{title}</h1>
      {subtitle && <p style={{ color: '#666', margin: '4px 0 0' }}>{subtitle}</p>}
    </div>
  )
}
