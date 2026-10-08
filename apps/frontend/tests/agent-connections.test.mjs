import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, reactive, ref } from 'vue'
import { getAgentConnections, getAvailableConnectionTypes, supportsJob } from '../src/utils/agent-connections.js'
import { supportsAgentUpdate } from '../src/utils/agent-updates.js'

test('connection-aware jobs require configuration and local support, independently of online state', () => {
  assert.equal(supportsJob(null, 'file'), true)
  assert.equal(supportsJob({ protocol_version: 2 }, 'truenas'), false)
  assert.equal(supportsJob({ protocol_version: 2 }, 'proxmox'), true)
  const agent = { protocol_version: 3, online: false, connections: { truenas: { configured: true, available: true } } }
  assert.equal(supportsJob(agent, 'truenas'), true)
  assert.equal(supportsJob(agent, 'proxmox'), false)
  agent.connections.truenas.configured = false
  assert.equal(supportsJob(agent, 'truenas'), false)
  agent.connections.truenas = { configured: true, available: false }
  assert.equal(supportsJob(agent, 'truenas'), false)
})

test('connection overview keeps configured offline connections and handles legacy agents without inventing status', () => {
  assert.deepEqual(getAgentConnections(null), [])
  assert.deepEqual(getAvailableConnectionTypes(null), [])
  assert.deepEqual(getAgentConnections({ protocol_version: 0 }), [])
  for (const protocol_version of [1, 2]) {
    const rows = getAgentConnections({ protocol_version })
    assert.equal(rows.length, 1)
    assert.equal(rows[0].value, 'proxmox')
    assert.equal(rows[0].configured, null)
    assert.equal(rows[0].available, null)
    assert.deepEqual(getAvailableConnectionTypes({ protocol_version }), [])
  }
  const agent = { protocol_version: 3, online: false, connections: {
    proxmox: { configured: true, available: false },
    truenas: { configured: false, available: true },
  } }
  assert.deepEqual(getAgentConnections(agent).map(row => row.value), ['proxmox'])
  assert.equal(getAgentConnections(agent)[0].available, false)
  assert.deepEqual(getAvailableConnectionTypes(agent).map(type => type.value), ['truenas'])
  agent.connections.truenas.configured = true
  assert.equal(getAgentConnections(agent).length, 2)
  assert.deepEqual(getAvailableConnectionTypes(agent), [])
  agent.connections.proxmox.configured = false
  assert.deepEqual(getAgentConnections(agent).map(row => row.value), ['truenas'])
  assert.deepEqual(getAvailableConnectionTypes(agent).map(type => type.value), ['proxmox'])
})

test('connection removal waits for confirmation, preserves rows on failure and rejects stale agent actions', async () => {
  const source = readFileSync(new URL('../src/pages/AgentDetailPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const agent = { id: 1, online: true, protocol_version: 3, connections: { truenas: { configured: true, available: true } } }
  const route = reactive({ params: { agentId: '1' }, query: {} })
  let confirm, attempts = 0
  const store = { agents: [agent], deleteConnection: async () => { attempts++; throw new Error('Pending snapshots') } }
  const page = runInNewContext(`${source}\n;({ confirmRemoveConnection, removingConnection, connections })`, {
    computed, ref, getAgentConnections, getAvailableConnectionTypes, supportsAgentUpdate,
    useAgentStore: () => store,
    useRoute: () => route,
    useRouter: () => ({}),
    useQuasar: () => ({ dialog: () => ({ onOk: callback => { confirm = callback } }), notify: () => {} }),
    watch: () => {}, defineOptions: () => {},
    getApiErrorMessage: error => error.message, shouldIgnoreApiError: () => false,
  })
  page.confirmRemoveConnection({ value: 'truenas', label: 'TrueNAS' })
  assert.equal(attempts, 0)
  await confirm()
  assert.equal(attempts, 1)
  assert.equal(page.removingConnection.value, null)
  assert.equal(page.connections.value[0].configured, true)
  page.confirmRemoveConnection({ value: 'truenas', label: 'TrueNAS' })
  route.params.agentId = '2'
  await confirm()
  assert.equal(attempts, 1, 'confirmation must not affect another agent after navigation')
})
