'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

export function Header() {
  const path = usePathname()
  const link = (href, label, match) => (
    <Link href={href} className={(match ? path.startsWith(href) : path === href) ? 'active' : ''}>{label}</Link>
  )
  return (
    <div className="topbar">
      <div className="topbar-inner">
        <span className="brand">QueryMind AI</span>
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
      <h1 className="page-title">{title}</h1>
      {subtitle && <p className="subtitle">{subtitle}</p>}
    </>
  )
}
