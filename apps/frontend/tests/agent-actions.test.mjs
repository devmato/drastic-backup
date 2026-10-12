import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, reactive, ref } from 'vue'
import { getAgentConnections, getAvailableConnectionTypes } from '../src/utils/agent-connections.js'
import { hasVersionMismatch, supportsAgentUpdate } from '../src/utils/agent-updates.js'
import { getApiErrorMessage, shouldIgnoreApiError } from '../src/utils/api-error.js'

test('install cards share native setup and copy the complete prepared command', async () => {
  const source = readFileSync(new URL('../src/pages/AgentInstallPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const user = reactive({ environment: 'prod' })
  let copied
  const page = runInNewContext(`${source}\n;({ platformCards, copyAction })`, {
    computed, ref, useUserStore: () => user, useAgentStore: () => ({}), useQuasar: () => ({ notify: () => {} }),
    copyToClipboard: text => { copied = text },
    window: { location: { origin: 'https://backup.example.net' } }, onMounted: () => {}, defineOptions: () => {},
  })
  const [linux, mac, docker] = page.platformCards.value
  assert.deepEqual(Array.from(page.platformCards.value, card => card.title), ['Linux', 'macOS', 'Docker'])
  assert.notEqual(linux.icon, mac.icon)
  assert.equal(linux.actions[0].snippet, mac.actions[0].snippet)
  assert.equal(mac.actions[0].snippet, "curl -fsSL 'https://backup.example.net/install' | bash")
  assert.equal(linux.actions.length, 1)
  assert.equal(mac.actions.length, 1)
  assert.equal(docker.actions.length, 2)
  await page.copyAction(docker.actions[1])
  assert.equal(copied, docker.actions[1].snippet)
  assert.ok(copied.endsWith('docker compose -f /opt/drastic-agent/docker-compose.yaml up -d'))
  user.environment = 'dev'
  assert.equal(page.platformCards.value[0].actions[1].type, 'develop')
  assert.equal(page.platformCards.value[1].actions.length, 1)
})

test('install card selects an action and falls back when that action disappears', () => {
  const source = readFileSync(new URL('../src/components/agents/AgentInstallCard.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const props = reactive({ card: { actions: [{ id: 'setup' }, { id: 'develop' }] } })
  const card = runInNewContext(`${source}\n;({ selectedActionId, activeAction })`, {
    computed, ref, defineProps: () => props, defineEmits: () => () => {},
  })
  assert.equal(card.activeAction.value.id, 'setup')
  card.selectedActionId.value = 'develop'
  assert.equal(card.activeAction.value.id, 'develop')
  props.card.actions = [props.card.actions[0]]
  assert.equal(card.activeAction.value.id, 'setup')
})

test('agent list flags differing known builds without claiming an update is available', () => {
  const backend_version = '2026-10-08-a1b2c3d4'
  for (const version of [backend_version, undefined, null, '', 'unknown']) {
    assert.equal(hasVersionMismatch({ version, backend_version }), false)
  }
  for (const version of ['2026-10-07-12345678', '2026-10-09-12345678', '2026-10-08-12345678', `${backend_version}-dirty`, '0.1.0']) {
    assert.equal(hasVersionMismatch({ version, backend_version }), true)
    assert.equal(hasVersionMismatch({ version, backend_version: version }), false)
    for (const missing of [undefined, null, '', 'unknown']) {
      assert.equal(hasVersionMismatch({ version, backend_version: missing }), false)
    }
  }
})

test('agent actions preserve confirmation, prevent duplicate runs and recover after failure', async () => {
  const source = readFileSync(new URL('../src/pages/AgentDetailPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const agent = reactive({ id: 1, online: true, protocol_version: 3, install_type: 'git', os: 'Linux' })
  const calls = []
  let finish, confirm
  const page = runInNewContext(`${source}\n;({ agentActions, pendingActions })`, {
    computed, ref, getAgentConnections, getAvailableConnectionTypes, supportsAgentUpdate,
    useAgentStore: () => ({ agents: [agent], runAction: (id, action) => {
      calls.push({ id, action })
      return new Promise((resolve, reject) => { finish = { resolve, reject } })
    }, syncAgent: (id, recoveryKey) => {
      calls.push({ id, action: 'sync', recoveryKey })
    } }),
    useUserStore: () => ({ withRecoveryKey: operation => operation('recovery-key') }),
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
  await action('sync').run()
  assert.deepEqual(calls.at(-1), { id: 1, action: 'sync', recoveryKey: 'recovery-key' })
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
  agent.protocol_version = 4
  assert.equal(action('update').name, 'update')
  agent.install_type = 'manual'
  assert.equal(action('update'), undefined)
  agent.install_type = 'docker'
  agent.os = 'Windows'
  assert.equal(action('update'), undefined)
  agent.os = 'Darwin'
  assert.equal(action('update'), undefined)
  agent.install_type = 'git'
  assert.equal(action('update').name, 'update')
  agent.install_type = 'manual'
  assert.equal(action('update'), undefined)
})

test('one-shot single and bulk updates share the existing action, skip duplicates and release buttons after start responses', async () => {
  const source = readFileSync(new URL('../src/pages/AgentsPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const requests = [], notifications = [], acknowledgements = new Map()
  const agent = { online: true, os: 'Linux', protocol_version: 4, install_type: 'git',
    version: '2026-10-07-12345678', backend_version: '2026-10-08-a1b2c3d4' }
  const store = reactive({ agents: [1, 2, 3, 4, 5].map(id => ({ ...agent, id, display_name: `Agent ${id}` })),
    runAction: (id, action) => new Promise((resolve, reject) => { requests.push(`${id}:${action}`); acknowledgements.set(id, { resolve, reject }) }) })
  store.agents[1].install_type = 'docker'
  store.agents[3].online = false
  store.agents[4].version = agent.backend_version
  const page = runInNewContext(`${source}\n;({ startAgentUpdates, pendingUpdates })`, {
    computed, ref, hasVersionMismatch, supportsAgentUpdate, getApiErrorMessage, shouldIgnoreApiError,
    useAgentStore: () => store, onMounted: () => {}, defineOptions: () => {},
    useQuasar: () => ({ notify: notification => notifications.push(notification) }),
  })
  const single = page.startAgentUpdates([1])
  assert.deepEqual(requests, ['1:update'])
  const bulk = page.startAgentUpdates()
  await page.startAgentUpdates([1, 4, 5])
  assert.deepEqual(requests, ['1:update', '2:update', '3:update'])
  acknowledgements.get(1).resolve()
  await single
  assert.equal(page.pendingUpdates.value.includes(1), false)
  assert.equal(notifications[0].message, 'Agent update started')
  acknowledgements.get(2).reject({ response: { status: 409, data: { message: 'Agent busy' } } })
  acknowledgements.get(3).reject({ response: { status: 504 } })
  await bulk
  assert.equal(page.pendingUpdates.value.length, 0)
  assert.equal(notifications[1].message, 'Agent 2: Agent busy')
  assert.equal(notifications[1].color, 'negative')
  assert.match(notifications[2].message, /start not confirmed/)
  assert.equal(notifications[2].color, 'warning')
})
