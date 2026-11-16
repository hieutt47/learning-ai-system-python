import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'vite'

const deferred = () => {
  let resolve
  const promise = new Promise((r) => { resolve = r })
  return { promise, resolve }
}
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const user = (id) => ({ id, role: 'STUDENT', full_name: id })
const aborted = (promise) => assert.rejects(promise, { name: 'AbortError' })

async function environment(run) {
  const original = globalThis.fetch
  const server = await createServer({ configFile: false, server: { middlewareMode: true, ws: false },
    root: new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'),
    appType: 'custom' })
  try {
    const client = await server.ssrLoadModule('/src/shared/api/client.ts')
    await run(client, () => server.ssrLoadModule('/src/shared/auth/session.ts'),
      () => server.ssrLoadModule('/src/shared/api/queryClient.ts'))
  } finally {
    globalThis.fetch = original
    await server.close()
  }
}

for (const blob of [false, true]) {
  for (const status of [200, 401, 403]) {
    test(`${blob ? 'blob' : 'JSON'} rejects late A response ${status} without retrying as B`, () => environment(async (c) => {
      let token = 'A', refreshes = 0, expired = 0, calls = 0
      c.setAccessTokenProvider(() => token)
      c.setRefreshTokenHandler(async () => { refreshes++ })
      c.setSessionExpiredHandler(() => { expired++ })
      const pending = deferred()
      globalThis.fetch = () => { calls++; return pending.promise }
      const request = blob ? c.apiGetBlob('/private') : c.apiGet('/private')
      const rejection = aborted(request)
      token = 'B'
      c.advanceSessionGeneration?.()
      pending.resolve(json({ code: 'ACCOUNT_INACTIVE', message: 'old account' }, status))
      await rejection
      assert.equal(calls, 1)
      assert.equal(refreshes, 0)
      assert.equal(expired, 0)
    }))
  }
}

for (const blob of [false, true]) {
  test(`${blob ? 'blob' : 'JSON'} rejects body decoded after switching sessions`, () => environment(async (c) => {
    const body = deferred(), reading = deferred()
    c.setAccessTokenProvider(() => 'A')
    globalThis.fetch = async () => ({ ok: true, status: 200, headers: new Headers(),
      json: () => { reading.resolve(); return body.promise },
      blob: () => { reading.resolve(); return body.promise },
    })
    const request = blob ? c.apiGetBlob('/private') : c.apiGet('/private')
    const rejection = aborted(request)
    await reading.promise
    c.advanceSessionGeneration()
    body.resolve(blob ? new Blob(['A']) : { owner: 'A' })
    await rejection
  }))
}

test('same-session refresh still retries once with new token', () => environment(async (c) => {
  let token = 'old', calls = 0
  c.setAccessTokenProvider(() => token)
  c.setRefreshTokenHandler(async () => { token = 'fresh' })
  globalThis.fetch = async (_, options) => {
    calls++
    if (calls === 1) return json({ message: 'expired' }, 401)
    assert.equal(options.headers.get('Authorization'), 'Bearer fresh')
    return json({ ok: true })
  }
  assert.deepEqual(await c.apiGet('/private'), { ok: true })
  assert.equal(calls, 2)
}))

test('logout clears local user/cache even if server revocation fails', () => environment(async (c, loadSession, loadQuery) => {
  const s = await loadSession()
  const { queryClient } = await loadQuery()
  globalThis.fetch = async (url, options) => {
    if (url.endsWith('/auth/login')) {
      assert.equal(options.headers.get('Authorization'), null)
      return json({ access_token: 'A' })
    }
    if (url.endsWith('/users/me')) return json(user('A'))
    throw new Error('offline')
  }
  await s.login({ email: 'a@example.test', password: 'test-only' })
  queryClient.setQueryData(['private'], { owner: 'A' })
  await assert.rejects(s.logout())
  assert.equal(s.getSession().user, null)
  assert.equal(queryClient.getQueryData(['private']), undefined)
}))

test('pending restore cannot resurrect user after logout', () => environment(async (c, loadSession) => {
  const s = await loadSession()
  const refresh = deferred()
  globalThis.fetch = async (url) => {
    if (url.endsWith('/auth/refresh')) return refresh.promise
    if (url.endsWith('/auth/logout')) return new Response(null, { status: 204 })
    return json(user('A'))
  }
  const restore = s.restoreSession()
  await new Promise(setImmediate)
  const logout = s.logout()
  assert.equal(s.getSession().user, null)
  refresh.resolve(json({ access_token: 'A' }))
  await Promise.allSettled([restore, logout])
  assert.equal(s.getSession().user, null)
}))
