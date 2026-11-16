import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
function deferred() {
  let resolve
  const promise = new Promise((r) => { resolve = r })
  return { promise, resolve }
}

async function environment(run) {
  const original = globalThis.fetch
  const server = await createServer({ configFile: false, root: fileURLToPath(new URL('..', import.meta.url)),
    server: { middlewareMode: true, ws: false }, appType: 'custom' })
  try {
    const c = await server.ssrLoadModule('/src/shared/api/client.ts')
    const s = await server.ssrLoadModule('/src/shared/auth/session.ts')
    const { queryClient } = await server.ssrLoadModule('/src/shared/api/queryClient.ts')
    await run(c, s, queryClient)
  } finally { globalThis.fetch = original; await server.close() }
}

test('late A profile cannot overwrite B login or restore cached A history', () => environment(async (c, s, cache) => {
  const aProfile = deferred(), started = deferred()
  globalThis.fetch = async (url, options) => {
    if (url.endsWith('/auth/login')) return json({ access_token: JSON.parse(options.body).email })
    if (options.headers.get('Authorization') === 'Bearer A') {
      started.resolve()
      return aProfile.promise
    }
    return json({ id: 'B', role: 'STUDENT' })
  }
  const a = s.login({ email: 'A', password: 'test' })
  const stale = assert.rejects(a, { name: 'AbortError' })
  await started.promise
  cache.setQueryData(['attempts', 'mine'], ['A'])
  await s.login({ email: 'B', password: 'test' })
  assert.equal(cache.getQueryData(['attempts', 'mine']), undefined)
  aProfile.resolve(json({ id: 'A', role: 'STUDENT' }))
  await stale
  assert.equal(s.getSession().user.id, 'B')
}))

test('login waits for old refresh cookie operation then keeps B', () => environment(async (c, s) => {
  const refresh = deferred(), started = deferred()
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push(url)
    if (url.endsWith('/auth/refresh')) { started.resolve(); return refresh.promise }
    if (url.endsWith('/auth/login')) return json({ access_token: 'B' })
    assert.equal(options.headers.get('Authorization'), 'Bearer B')
    return json({ id: 'B', role: 'STUDENT' })
  }
  const restore = s.restoreSession()
  await started.promise
  const login = s.login({ email: 'B', password: 'test' })
  await new Promise(setImmediate)
  assert.equal(calls.some((url) => url.endsWith('/auth/login')), false)
  refresh.resolve(json({ access_token: 'A' }))
  await Promise.all([restore, login])
  assert.equal(s.getSession().user.id, 'B')
}))

test('logout during pending login revokes after login cookie finishes', () => environment(async (c, s) => {
  const response = deferred(), started = deferred()
  const calls = []
  globalThis.fetch = async (url) => {
    calls.push(url)
    if (url.endsWith('/auth/login')) { started.resolve(); return response.promise }
    if (url.endsWith('/auth/logout')) return new Response(null, { status: 204 })
    throw new Error('stale profile request must not run')
  }
  const login = s.login({ email: 'A', password: 'test' })
  const stale = assert.rejects(login, { name: 'AbortError' })
  await started.promise
  const logout = s.logout()
  assert.equal(s.getSession().user, null)
  await new Promise(setImmediate)
  assert.equal(calls.some((url) => url.endsWith('/auth/logout')), false)
  response.resolve(json({ access_token: 'A' }))
  await Promise.all([stale, logout])
  assert.equal(calls.length, 2)
  assert.equal(s.getSession().user, null)
}))

test('logout-login-logout keeps final logout intent', () => environment(async (c, s) => {
  const firstLogout = deferred(), started = deferred()
  let logouts = 0, logins = 0
  globalThis.fetch = async (url) => {
    if (url.endsWith('/auth/logout')) {
      logouts++
      if (logouts === 1) { started.resolve(); return firstLogout.promise }
      return new Response(null, { status: 204 })
    }
    if (url.endsWith('/auth/login')) { logins++; return json({ access_token: 'A' }) }
    return json({ id: 'A', role: 'STUDENT' })
  }
  const one = s.logout()
  // Its HTTP response belongs to the old generation, so it must be discarded.
  const staleLogout = assert.rejects(one, { name: 'AbortError' })
  await started.promise
  const login = s.login({ email: 'A', password: 'test' })
  const stale = assert.rejects(login, { name: 'AbortError' })
  const last = s.logout()
  firstLogout.resolve(new Response(null, { status: 204 }))
  await Promise.all([staleLogout, stale, last])
  assert.equal(s.getSession().user, null)
  assert.equal(logins, 0)
  assert.equal(logouts, 2)
}))

for (const operation of ['logoutAll', 'resetPassword']) {
  test(`${operation} waits for in-flight login cookie before invalidating it`, () => environment(async (c, s) => {
    const pendingLogin = deferred(), started = deferred()
    const calls = []
    globalThis.fetch = async (url, options) => {
      calls.push(url)
      if (url.endsWith('/auth/login')) {
        if (JSON.parse(options.body).email === 'A') return json({ access_token: 'A' })
        started.resolve()
        return pendingLogin.promise
      }
      if (url.endsWith('/users/me')) return json({ id: 'A', role: 'STUDENT' })
      return new Response(null, { status: 204 })
    }
    await s.login({ email: 'A', password: 'test' })
    const login = s.login({ email: 'B', password: 'test' })
    const stale = assert.rejects(login, { name: 'AbortError' })
    await started.promise
    const endpoint = operation === 'logoutAll' ? '/auth/logout-all' : '/auth/reset-password'
    const action = operation === 'logoutAll' ? s.logoutAll() : s.resetPassword({ token: 'test', new_password: 'test-only' })
    await new Promise(setImmediate)
    assert.equal(calls.some((url) => url.endsWith(endpoint)), false)
    pendingLogin.resolve(json({ access_token: 'B' }))
    await Promise.all([stale, action])
    assert.equal(s.getSession().user, null)
  }))
}

test('logout-all expired token fails without queue deadlock or refresh', () => environment(async (c, s) => {
  let refreshes = 0
  globalThis.fetch = async (url) => {
    if (url.endsWith('/auth/login')) return json({ access_token: 'A' })
    if (url.endsWith('/users/me')) return json({ id: 'A', role: 'STUDENT' })
    if (url.endsWith('/auth/refresh')) refreshes++
    return json({ code: 'UNAUTHENTICATED', message: 'expired' }, 401)
  }
  await s.login({ email: 'A', password: 'test' })
  await assert.rejects(s.logoutAll(), /expired/)
  assert.equal(refreshes, 0)
  assert.equal(s.getSession().user, null)
}))

test('same-session failed refresh expires current session once', () => environment(async (c, s, cache) => {
  let expiredCalls = 0
  globalThis.fetch = async (url) => {
    if (url.endsWith('/auth/login')) return json({ access_token: 'A' })
    if (url.endsWith('/users/me')) return json({ id: 'A', role: 'STUDENT' })
    if (url.endsWith('/auth/refresh')) return json({ code: 'UNAUTHENTICATED', message: 'expired' }, 401)
    return json({ code: 'UNAUTHENTICATED', message: 'expired' }, 401)
  }
  await s.login({ email: 'A', password: 'test' })
  c.setUnauthorizedHandler(() => { expiredCalls++ })
  cache.setQueryData(['private'], ['A'])
  await assert.rejects(c.apiGet('/private'))
  assert.equal(expiredCalls, 1)
  assert.equal(s.getSession().user, null)
  assert.equal(cache.getQueryData(['private']), undefined)
}))
