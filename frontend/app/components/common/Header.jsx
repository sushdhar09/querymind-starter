'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

export function Header() {
  const path = usePathname()
  const link = (href, label, match) => (
    <Link href={href} className={(match ? path.startsWith(href) : path === href) ? 'active' : ''}>{label}</Link>
  )
  return (
    <div className="topbar" style={{ borderBottom: '2px solid var(--brand)', background: 'linear-gradient(135deg, var(--card) 0%, var(--info-bg) 100%)' }}>
      <div className="topbar-inner">
        <span className="brand" style={{ color: 'var(--brand)', fontSize: '20px' }}>AskData AI</span>
        <nav className="nav">
          {link('/', 'Chat')}
          {link('/admin/schema-manager', 'Schema', true)}
          {link('/admin/examples-manager', 'Examples', true)}
          {link('/admin/query-logs', 'Logs', true)}
          {link('/admin/guardrails-config', 'Guardrails', true)}
          {link('/admin/model-config', 'Model', true)}
        </nav>
      </div>
    </div>
  )
}

export function PageTitle({ title, subtitle }) {
  return (
    <>
      <h1 className="page-title" style={{ color: 'var(--brand-dark)', fontSize: '28px' }}>{title}</h1>
      {subtitle && <p className="subtitle" style={{ fontSize: '16px' }}>{subtitle}</p>}
    </>
  )
}
