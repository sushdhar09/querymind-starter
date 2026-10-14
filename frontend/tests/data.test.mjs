import assert from 'node:assert/strict'
import { test } from 'node:test'
import { chartSpec, formatCell, friendlyError, nextSort, parseList, sortRows, statusTone } from '../app/lib/data.js'

test('chartSpec picks a bar chart for label plus number', () => {
  const spec = chartSpec(['status', 'n'], [['pending', 5], ['done', 9]])
  assert.equal(spec.kind, 'bar')
  assert.equal(spec.labelKey, 'status')
  assert.equal(spec.valueKey, 'n')
  assert.deepEqual(spec.points, [{ label: 'pending', value: 5 }, { label: 'done', value: 9 }])
})

test('chartSpec works when the number column comes first', () => {
  const spec = chartSpec(['total', 'city'], [[3, 'Delhi'], [4, 'Pune']])
  assert.equal(spec.labelKey, 'city')
  assert.equal(spec.valueKey, 'total')
})

test('chartSpec uses a line chart for month labels', () => {
  assert.equal(chartSpec(['month', 'orders'], [['2026-01', 4], ['2026-02', 7]]).kind, 'line')
})

test('chartSpec declines when a chart would be misleading', () => {
  assert.equal(chartSpec(['a', 'b', 'c'], [[1, 2, 3], [4, 5, 6]]), null)
  assert.equal(chartSpec(['name', 'city'], [['a', 'x'], ['b', 'y']]), null)
  assert.equal(chartSpec(['k', 'v'], [['a', 1]]), null)
  assert.equal(chartSpec(['k', 'v'], Array.from({ length: 51 }, (_, i) => [`k${i}`, i])), null)
  assert.equal(chartSpec(['k', 'v'], [['a', 1], ['b', 'text']]), null)
})

test('sortRows sorts numbers numerically and text naturally', () => {
  const rows = [{ n: 10, s: 'item10' }, { n: 2, s: 'item2' }, { n: null, s: 'item1' }]
  assert.deepEqual(sortRows(rows, 'n', 'asc').map((r) => r.n), [2, 10, null])
  assert.deepEqual(sortRows(rows, 'n', 'desc').map((r) => r.n), [10, 2, null])
  assert.deepEqual(sortRows(rows, 's', 'asc').map((r) => r.s), ['item1', 'item2', 'item10'])
  assert.equal(sortRows(rows, null), rows)
})

test('sortRows does not mutate its input', () => {
  const rows = [{ n: 2 }, { n: 1 }]
  sortRows(rows, 'n')
  assert.deepEqual(rows, [{ n: 2 }, { n: 1 }])
})

test('nextSort cycles asc, desc, off and resets on a new column', () => {
  let s = { key: null, dir: 'asc' }
  s = nextSort(s, 'a'); assert.deepEqual(s, { key: 'a', dir: 'asc' })
  s = nextSort(s, 'a'); assert.deepEqual(s, { key: 'a', dir: 'desc' })
  s = nextSort(s, 'a'); assert.deepEqual(s, { key: null, dir: 'asc' })
  s = nextSort({ key: 'a', dir: 'desc' }, 'b'); assert.deepEqual(s, { key: 'b', dir: 'asc' })
})

test('parseList trims, drops blanks and de-duplicates', () => {
  assert.deepEqual(parseList(' a, b ,, a ,c'), ['a', 'b', 'c'])
  assert.deepEqual(parseList(''), [])
})

test('friendlyError and statusTone', () => {
  assert.match(friendlyError({ status: 'blocked', failed_stage: 'validator', error: 'DROP blocked' }), /blocked.*validator.*DROP blocked/)
  assert.match(friendlyError({ status: 'error', failed_stage: 'generator', error: 'no sql' }), /could not answer.*generator.*no sql/)
  assert.equal(friendlyError(null), '')
  assert.equal(statusTone('success'), 'ok')
  assert.equal(statusTone('blocked'), 'warn')
  assert.equal(statusTone('error'), 'bad')
  assert.equal(statusTone('weird'), '')
})

test('formatCell', () => {
  assert.equal(formatCell(null), '-')
  assert.equal(formatCell(5000), '5000')
  assert.equal(formatCell(1234.5678), (1234.57).toLocaleString(undefined, { maximumFractionDigits: 2 }))
  assert.equal(formatCell('x'), 'x')
})
