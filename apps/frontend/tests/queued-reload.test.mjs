import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createQueuedReload } from '../src/utils/queued-reload.js'

test('progress bursts produce one trailing reload, with no overlap or idle polling', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 0 })
  const starts = []
  let finish
  const reload = createQueuedReload(() => {
    starts.push(Date.now())
    return new Promise(resolve => { finish = resolve })
  })
  t.after(() => reload.cancel())
  const first = reload()
  for (let i = 0; i < 50; i++) reload()
  t.mock.timers.tick(500)
  assert.deepEqual(starts, [0])
  finish()
  await first
  t.mock.timers.tick(499)
  assert.deepEqual(starts, [0])
  t.mock.timers.tick(1)
  assert.deepEqual(starts, [0, 1000])
  finish()
  await setImmediate()
  t.mock.timers.tick(5000)
  assert.deepEqual(starts, [0, 1000])
})

test('completion bypasses the delay but waits for an active reload', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 0 })
  const starts = []
  let finish
  const reload = createQueuedReload(() => {
    starts.push(Date.now())
    return new Promise(resolve => { finish = resolve })
  })
  t.after(() => reload.cancel())
  const first = reload()
  t.mock.timers.tick(100)
  reload(true)
  reload() // Later progress must not erase the pending completion.
  assert.deepEqual(starts, [0])
  finish()
  await first
  assert.deepEqual(starts, [0, 100])
  finish()
  await setImmediate()
  reload()
  t.mock.timers.tick(100)
  const completion = reload(true)
  assert.deepEqual(starts, [0, 100, 200])
  finish()
  await completion
  t.mock.timers.tick(2000)
  assert.deepEqual(starts, [0, 100, 200])
})

test('failures release the queue and cancellation removes delayed and in-flight follow-ups', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 0 })
  const warning = t.mock.method(console, 'warn', () => {})
  let calls = 0
  const reload = createQueuedReload(async () => {
    if (++calls === 1) throw new Error('temporary failure')
  })
  await reload()
  assert.equal(warning.mock.callCount(), 1)
  await reload(true)
  assert.equal(calls, 2)
  reload()
  reload.cancel()
  reload(true)
  t.mock.timers.tick(2000)
  assert.equal(calls, 2)

  let finish
  const active = createQueuedReload(() => {
    calls++
    return new Promise(resolve => { finish = resolve })
  })
  const first = active()
  active(true)
  active.cancel()
  finish()
  await first
  t.mock.timers.tick(2000)
  assert.equal(calls, 3)
})
