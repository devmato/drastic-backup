import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import axios from 'axios'
import { createPinia, defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { canRefreshAuthError, isAuthError, isRecoveryKeyError, isSessionAuthError, markAuthError } from '../src/utils/auth.js'

function source(path) {
  return readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8').replace(/^import .*$/gm, '')
}

function setup(adapter, { router, disconnectAppSocket = () => {} } = {}) {
  let userStore
  const bootSource = source('boot/axios.js')
    .replaceAll("import('stores/user')", 'Promise.resolve(storeModule)')
    .replace('export default boot', 'const install = boot').replace('export { api }', '')
  const { api, install } = runInNewContext(`${bootSource}\n;({ api, install })`, {
    boot: callback => callback, axios, disconnectAppSocket, getCSRFToken: () => 'csrf',
    canRefreshAuthError, isSessionAuthError, markAuthError, storeModule: { useUserStore: () => userStore },
  })
  install({ app: { config: { globalProperties: {} } }, router: router || {
    currentRoute: { value: { path: '/jobs', fullPath: '/jobs', meta: { auth: true } } }, replace: async () => {},
  } })
  api.defaults.adapter = adapter
  const userApi = runInNewContext(`${source('api/user.js').replaceAll('export ', '')}
    ;({ refreshSession, getAuthStatus, logout })`, { api })
  const useUserStore = runInNewContext(`${source('stores/user.js').replace('export const', 'const')}
    ;useUserStore`, {
    ref, computed, defineStore, userApi, isAuthError, isRecoveryKeyError,
    disconnectAppSocket, setDiagnosticRecording: () => {},
  })
  userStore = useUserStore(createPinia())
  return userStore
}

test('expired access tokens refresh on reload; network/server failures preserve the session', async () => {
  for (const failure of [null, 'offline', 'server']) {
    let expired = true, refreshes = 0
    const userStore = setup(async config => {
      if (config.url === '/auth/refresh') {
        refreshes++
        if (failure === 'offline') throw new Error('Network unavailable')
        if (failure === 'server') throw new axios.AxiosError('Unavailable', null, config, null, { status: 503 })
        expired = false
      }
      if (config.url === '/auth/status' && expired) {
        throw new axios.AxiosError('Expired', null, config, null, { status: 401, data: { code: 'token_expired' } })
      }
      return { status: 200, config, headers: {}, data: { authenticated: true, user: { id: 1 } } }
    })
    if (failure) {
      userStore.user = { id: 1 }
      userStore.recoveryKey = 'in-memory-key'
      await assert.rejects(userStore.getUser(), error => failure === 'offline'
        ? error.message === 'Network unavailable' : error.response?.status === 503)
      assert.equal(userStore.recoveryKey, 'in-memory-key')
    } else {
      assert.equal((await userStore.getUser()).id, 1)
    }
    assert.equal(userStore.loggedIn, true)
    assert.equal(refreshes, 1)
  }
})

test('an in-flight keepalive cannot restore the user after logout', async () => {
  const pending = Promise.withResolvers()
  const userStore = setup(async config => ({ status: 200, config, headers: {},
    data: config.url === '/auth/status' ? await pending.promise : { ok: true },
  }))
  userStore.user = { id: 1 }
  const checking = userStore.fetchAuthStatus()
  await userStore.logout()
  pending.resolve({ authenticated: true, user: { id: 1 } })
  await checking
  assert.equal(userStore.loggedIn, false)
})

test('unauthenticated keepalive closes the session and redirects only the active protected view', async () => {
  for (const state of ['active', 'disposed', 'new-login', 'public']) {
    const pending = Promise.withResolvers()
    const navigations = []
    let disconnects = 0, reconnects = 0, tick, dispose
    const router = {
      currentRoute: { value: { path: '/jobs', fullPath: '/jobs?agent=2#current', meta: { auth: state !== 'public' } } },
      replace: async route => { navigations.push(route) },
    }
    const userStore = setup(async config => ({ status: 200, config, headers: {}, data: await pending.promise }), {
      router, disconnectAppSocket: () => { disconnects++ },
    })
    userStore.user = { id: 1 }
    userStore.recoveryKey = 'old-key'
    const keepAlive = runInNewContext(`${source('composables/useSessionKeepAlive.js').replace('export ', '')}
      ;useSessionKeepAlive`, {
      useRouter: () => router,
      onMounted: callback => callback(), onBeforeUnmount: callback => { dispose = callback },
      setInterval: callback => { tick = callback; return 1 }, clearInterval: () => {},
      document: { addEventListener: () => {}, removeEventListener: () => {} },
      ensureSocket: async () => { reconnects++ },
    })
    keepAlive(userStore)
    const checking = tick()
    if (state === 'disposed') dispose()
    if (state === 'new-login') {
      userStore.user = { id: 2 }
      userStore.recoveryKey = 'new-key'
    }
    pending.resolve({ authenticated: false, user: null })
    await checking
    assert.equal(userStore.loggedIn, state === 'new-login')
    assert.equal(userStore.recoveryKey, state === 'new-login' ? 'new-key' : null)
    assert.equal(disconnects, state === 'new-login' ? 0 : 1)
    assert.equal(reconnects, state === 'new-login' ? 1 : 0)
    assert.equal(navigations.length, state === 'active' ? 1 : 0)
    if (state === 'active') {
      assert.equal(navigations[0].path, '/login')
      assert.equal(navigations[0].query.redirect, '/jobs?agent=2#current')
    }
    dispose()
  }
})
