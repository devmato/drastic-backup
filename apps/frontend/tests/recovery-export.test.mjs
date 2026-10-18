import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'

function source(path) {
  return readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8').replace(/^import .*$/gm, '')
}

function settings(downloadRecoveryExport, crypto = webcrypto) {
  let dispose
  const script = source('pages/SettingsPage.vue').split('<script setup>')[1].split('</script>')[0]
  const page = runInNewContext(`${script}\n;({ recoveryOpen, recoveryBusy, recoveryPassword, archivePassword,
    resticTargets, recoveryError, submitRecovery, resetRecovery })`, {
    ref, crypto, btoa, Uint8Array, AbortController,
    useRouter: () => ({}), useQuasar: () => ({}),
    useUserStore: () => ({ downloadRecoveryExport }),
    onMounted: () => {}, onBeforeUnmount: callback => { dispose = callback },
    getApiErrorMessage: (error, fallback) => error.message || fallback,
  })
  page.recoveryOpen.value = true
  page.recoveryPassword.value = 'account-password'
  return { page, dispose: () => dispose() }
}

test('export uses a fresh random archive password and keeps it visible until the dialog is closed', async () => {
  const calls = []
  let complete
  const pending = new Promise(resolve => { complete = resolve })
  const { page } = settings(async (...args) => { calls.push(args); await pending })
  page.resticTargets.value = ['linux_amd64', 'windows_amd64']
  const exporting = page.submitRecovery()
  assert.equal(page.recoveryBusy.value, true)
  assert.equal(page.archivePassword.value, '')
  await page.submitRecovery()
  assert.equal(calls.length, 1, 'a second click must not start another export')
  assert.equal(calls[0][0], 'account-password')
  assert.match(calls[0][1], /^[A-Za-z0-9_-]{32}$/)
  assert.deepEqual([...calls[0][2]], ['linux_amd64', 'windows_amd64'])
  complete()
  await exporting
  assert.equal(page.archivePassword.value, calls[0][1])
  assert.equal(page.recoveryOpen.value, true)
  assert.equal(page.recoveryPassword.value, '')
  await page.submitRecovery()
  assert.equal(calls.length, 1, 'the result view must not generate another password')
  page.resetRecovery()
  assert.equal(page.archivePassword.value, '')
  assert.deepEqual([...page.resticTargets.value], [])
  page.recoveryPassword.value = 'account-password'
  await page.submitRecovery()
  assert.notEqual(calls[1][1], calls[0][1])
})

test('a failed export clears the account password and does not display an unusable archive password', async () => {
  const { page } = settings(async () => { throw { response: { status: 401 } } })
  await page.submitRecovery()
  assert.equal(page.recoveryError.value, 'Current password is wrong')
  assert.equal(page.archivePassword.value, '')
  assert.equal(page.recoveryPassword.value, '')
  assert.equal(page.recoveryBusy.value, false)
})

test('unavailable secure randomness fails without sending an export request', async () => {
  let called = false
  const { page } = settings(async () => { called = true }, { getRandomValues() { throw new Error('Randomness unavailable') } })
  await page.submitRecovery()
  assert.equal(called, false)
  assert.equal(page.archivePassword.value, '')
  assert.equal(page.recoveryError.value, 'Randomness unavailable')
})

test('leaving Settings aborts export and cannot restore the one-time password after unmount', async () => {
  let signal, complete
  const pending = new Promise(resolve => { complete = resolve })
  const { page, dispose } = settings(async (_password, _archivePassword, _targets, requestSignal) => {
    signal = requestSignal
    await pending
  })
  const exporting = page.submitRecovery()
  dispose()
  assert.equal(signal.aborted, true)
  assert.equal(page.recoveryPassword.value, '')
  complete()
  await exporting
  assert.equal(page.archivePassword.value, '')
})

test('the download uses the existing API with both passwords and respects an aborted request', async () => {
  const requests = []
  const blob = new Blob(['encrypted-zip'])
  const userApi = runInNewContext(`${source('api/user.js').replaceAll('export ', '')}; ({ recoveryExport })`, {
    Blob, api: { post: async (...args) => { requests.push(args); return { data: blob, headers: {} } } },
  })
  let clicks = 0, revoked = 0
  const useUserStore = runInNewContext(`${source('stores/user.js').replace('export const', 'const')}; useUserStore`, {
    ref, computed, defineStore: (_name, setup) => setup, userApi,
    URL: { createObjectURL: () => 'blob:kit', revokeObjectURL: () => { revoked++ } },
    document: { body: { appendChild() {} }, createElement: () => ({ style: {}, click() { clicks++ }, remove() {} }) },
  })
  const store = useUserStore()
  const controller = new AbortController()
  await store.downloadRecoveryExport('account', 'random-archive-password', ['darwin_arm64'], controller.signal)
  assert.equal(clicks, 1)
  assert.equal(revoked, 1)
  assert.equal(requests[0][0], '/user/recovery-export')
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0][1])), {
    password: 'account', archive_password: 'random-archive-password', restic_targets: ['darwin_arm64'],
  })
  assert.equal(requests[0][2].responseType, 'blob')
  assert.equal(requests[0][2].signal, controller.signal)
  controller.abort()
  await store.downloadRecoveryExport('account', 'random-archive-password', [], controller.signal)
  assert.equal(clicks, 1)
})
