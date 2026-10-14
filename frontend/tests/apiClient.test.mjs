import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { ApiError, apiClient, describeError } from '../app/lib/apiClient.js'

const realFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = realFetch })

function mockFetch(responder) {
  const calls = []
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return responder(url, init) }
  return calls
}
const json = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })

test('executeQuery posts JSON without any admin key', async () => {
  const calls = mockFetch(() => json({ status: 'success' }))
  const out = await apiClient.executeQuery({ nl_query: 'hi', clarify_ambiguities: true })
  assert.equal(out.status, 'success')
  assert.match(calls[0].url, /\/api\/v1\/query$/)
  assert.equal(calls[0].init.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].init.body), { nl_query: 'hi', clarify_ambiguities: true })
  assert.equal(calls[0].init.headers['X-API-Key'], undefined)
})

test('admin calls send the key they are given', async () => {
  const calls = mockFetch(() => json({ total: 1 }))
  await apiClient.verifyKey('secret')
  assert.equal(calls[0].init.headers['X-API-Key'], 'secret')
  assert.match(calls[0].url, /\/admin\/stats$/)
})

test('getLogs returns items and the total from the header, with filters in the query string', async () => {
  const calls = mockFetch(() => json([{ id: 1 }], 200, { 'X-Total-Count': '42' }))
  const out = await apiClient.getLogs({ status: 'blocked', search: '', limit: 25, offset: 50 })
  assert.deepEqual(out, { items: [{ id: 1 }], total: 42 })
  const url = new URL(calls[0].url)
  assert.equal(url.searchParams.get('status'), 'blocked')
  assert.equal(url.searchParams.get('limit'), '25')
  assert.equal(url.searchParams.get('offset'), '50')
  assert.equal(url.searchParams.has('search'), false)
})

test('updateColumn and deleteExample use the right verbs and paths', async () => {
  const calls = mockFetch((url, init) => (init.method === 'DELETE' ? new Response(null, { status: 204 }) : json({ ok: true })))
  await apiClient.updateColumn('customers', 'email', { is_sensitive: true })
  assert.equal(calls[0].init.method, 'PUT')
  assert.match(calls[0].url, /\/admin\/schema\/customers\/email$/)
  assert.equal(await apiClient.deleteExample(7), null)
  assert.equal(calls[1].init.method, 'DELETE')
  assert.match(calls[1].url, /\/admin\/examples\/7$/)
})

test('HTTP errors become ApiError with status and readable detail', async () => {
  mockFetch(() => json({ detail: { message: 'Example SQL failed validation', errors: ['Unknown column x'] } }, 422))
  await assert.rejects(apiClient.createExample({}), (e) => {
    assert.ok(e instanceof ApiError)
    assert.equal(e.status, 422)
    assert.match(e.message, /failed validation: Unknown column x/)
    assert.deepEqual(e.detail.errors, ['Unknown column x'])
    return true
  })
})

test('rate limiting and unreachable backend give friendly messages', async () => {
  mockFetch(() => new Response('slow down', { status: 429 }))
  await assert.rejects(apiClient.executeQuery({ nl_query: 'x' }), /Too many requests/)
  globalThis.fetch = async () => { throw new TypeError('fetch failed') }
  await assert.rejects(apiClient.executeQuery({ nl_query: 'x' }), (e) => e.status === 0 && /Cannot reach the backend/.test(e.message))
})

test('describeError handles strings, validation arrays and objects', () => {
  assert.equal(describeError('plain', 'fb'), 'plain')
  assert.equal(describeError([{ msg: 'bad a' }, { msg: 'bad b' }], 'fb'), 'bad a; bad b')
  assert.equal(describeError(undefined, 'fb'), 'fb')
  assert.equal(describeError({ other: 1 }, 'fb'), 'fb')
})
