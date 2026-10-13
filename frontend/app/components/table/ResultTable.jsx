export function ResultTable({ data, rowCount }) {
  if (!data?.length) return null
  const columns = Object.keys(data[0] || {})
  return (
    <div style={{ background: 'white', padding: 16, borderRadius: 12, overflow: 'auto', boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
      <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col} style={{ textAlign: 'left', padding: 8, borderBottom: '2px solid #eee' }}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.slice(0, 20).map((row, i) => (
            <tr key={i}>
              {Object.values(row).map((val, j) => (
                <td key={j} style={{ padding: 8, borderBottom: '1px solid #eee' }}>{String(val).slice(0, 100)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ marginTop: 8, fontSize: 12, color: '#888' }}>{rowCount} rows total (showing 20)</div>
    </div>
  )
}
