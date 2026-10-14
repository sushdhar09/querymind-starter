'use client'
import { useMemo, useState } from 'react'
import { formatCell, nextSort, sortRows } from '../../lib/data.js'

export function DataTable({ columns, rows, pageSize = 20 }) {
  const [sort, setSort] = useState({ key: null, dir: 'asc' })
  const [page, setPage] = useState(0)
  const records = useMemo(() => rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]]))), [columns, rows])
  const sorted = useMemo(() => sortRows(records, sort.key, sort.dir), [records, sort])
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize))
  const visible = sorted.slice(page * pageSize, page * pageSize + pageSize)
  const arrow = (c) => (sort.key === c ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '')
  return (
    <div>
      <div className="table-wrap" style={{ maxHeight: 420 }}>
        <table className="data">
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c} className="sortable" onClick={() => { setSort(nextSort(sort, c)); setPage(0) }} aria-sort={sort.key === c ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  {c}{arrow(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((r, i) => (
              <tr key={i}>{columns.map((c) => <td key={c}>{formatCell(r[c])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="pager">
          <span className="muted">Page {page + 1} of {pages}</span>
          <button className="btn secondary small" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <button className="btn secondary small" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </div>
  )
}
