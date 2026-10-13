import { ADMIN_PANELS } from '../../lib/constants.js'

export function AdminNav() {
  return (
    <div style={{ marginTop: 24, display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8, fontSize: 13 }}>
      {ADMIN_PANELS.map((panel) => (
        <a
          key={panel.id}
          href={panel.path}
          style={{ background: 'white', padding: 12, borderRadius: 8, textAlign: 'center', textDecoration: 'none', color: 'black', boxShadow: '0 1px 2px rgba(0,0,0,0.1)' }}
        >
          {panel.icon} {panel.label}
        </a>
      ))}
    </div>
  )
}
