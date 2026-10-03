import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, reactive, ref } from 'vue'
import { getAgentConnections, getAvailableConnectionTypes } from '../src/utils/agent-connections.js'

test('agent actions preserve confirmation, prevent duplicate runs and recover after failure', async () => {
  const source = readFileSync(new URL('../src/pages/AgentDetailPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const agent = reactive({ id: 1, online: true, protocol_version: 3, install_type: 'git', os: 'Linux' })
  const calls = []
  let finish, confirm
  const page = runInNewContext(`${source}\n;({ agentActions, pendingActions })`, {
    computed, ref, getAgentConnections, getAvailableConnectionTypes,
    useAgentStore: () => ({ agents: [agent], runAction: (id, action) => {
      calls.push({ id, action })
      return new Promise((resolve, reject) => { finish = { resolve, reject } })
    } }),
    useRoute: () => ({ params: { agentId: '1' }, query: {} }), useRouter: () => ({}),
    useQuasar: () => ({ dialog: () => ({ onOk: callback => { confirm = callback } }), notify: () => {} }),
    watch: () => {}, defineOptions: () => {},
    getApiErrorMessage: error => error.message, shouldIgnoreApiError: () => false,
  })
  const action = name => page.agentActions.value.find(item => item.name === name)
  const update = action('update').run()
  assert.equal(page.pendingActions.value.update, true)
  await action('update').run()
  assert.equal(calls.length, 1)
  finish.reject(new Error('Update failed'))
  await update
  assert.equal(page.pendingActions.value.update, false)
  for (const name of ['reset-known-hosts', 'rotate-ssh-key']) {
    const before = calls.length
    action(name).run()
    assert.equal(calls.length, before, 'SSH action must wait for confirmation')
    const request = confirm()
    assert.deepEqual(calls.at(-1), { id: 1, action: name })
    assert.equal(page.pendingActions.value[name], true)
    finish.resolve()
    await request
    assert.equal(page.pendingActions.value[name], false)
  }
  agent.online = false
  const before = calls.length
  await action('update').run()
  assert.equal(calls.length, before)
  agent.install_type = 'docker'
  assert.equal(action('update'), undefined)
})
