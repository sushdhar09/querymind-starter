import { chartSpec, formatCell } from '../../lib/data.js'

const W = 640, H = 220, PAD = { l: 48, r: 12, t: 12, b: 56 }

export function AutoChart({ columns, rows }) {
  const spec = chartSpec(columns, rows)
  if (!spec) return null
  const { points, kind } = spec
  const max = Math.max(...points.map((p) => p.value), 0)
  const min = Math.min(...points.map((p) => p.value), 0)
  const span = max - min || 1
  const innerW = W - PAD.l - PAD.r, innerH = H - PAD.t - PAD.b
  const x = (i) => PAD.l + (kind === 'line' ? (i / (points.length - 1)) * innerW : (i + 0.5) * (innerW / points.length))
  const y = (v) => PAD.t + innerH - ((v - min) / span) * innerH
  const step = Math.ceil(points.length / 10)
  const barW = Math.max(4, innerW / points.length - 6)
  return (
    <div style={{ marginTop: 16 }}>
      <div className="muted" style={{ marginBottom: 4 }}>{spec.valueKey} by {spec.labelKey}</div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${kind} chart of ${spec.valueKey} by ${spec.labelKey}`}>
        <line x1={PAD.l} y1={y(0)} x2={W - PAD.r} y2={y(0)} stroke="#d1d5db" />
        <text x={PAD.l - 6} y={PAD.t + 4} textAnchor="end">{formatCell(max)}</text>
        <text x={PAD.l - 6} y={H - PAD.b} textAnchor="end">{formatCell(min)}</text>
        {kind === 'bar' && points.map((p, i) => (
          <rect key={i} x={x(i) - barW / 2} y={Math.min(y(p.value), y(0))} width={barW} height={Math.abs(y(p.value) - y(0))} fill="#4f46e5" rx="2">
            <title>{`${p.label}: ${formatCell(p.value)}`}</title>
          </rect>
        ))}
        {kind === 'line' && (
          <>
            <polyline fill="none" stroke="#4f46e5" strokeWidth="2" points={points.map((p, i) => `${x(i)},${y(p.value)}`).join(' ')} />
            {points.map((p, i) => <circle key={i} cx={x(i)} cy={y(p.value)} r="3" fill="#4f46e5"><title>{`${p.label}: ${formatCell(p.value)}`}</title></circle>)}
          </>
        )}
        {points.map((p, i) => i % step === 0 && (
          <text key={i} x={x(i)} y={H - PAD.b + 14} textAnchor="end" transform={`rotate(-35 ${x(i)} ${H - PAD.b + 14})`}>{p.label.slice(0, 14)}</text>
        ))}
      </svg>
    </div>
  )
}
