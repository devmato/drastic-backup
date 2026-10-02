import assert from 'node:assert/strict'
import { test } from 'node:test'
import { supportsJob } from '../src/utils/agent-connections.js'

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
