import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, reactive, ref } from 'vue'
import { backupStates, filterAgentJobs, getBackupState, getBackupStateColor, summarizeBackupResults } from '../src/utils/backup-results.js'

test('donut segments and job filters represent the same latest backup statuses across agents', () => {
  const agents = [
    { id: 1, jobs: [{ id: 1, last_operation: { state: 'success' } }, { id: 2, last_operation: null }] },
    { id: 2, jobs: ['failed', 'warning', 'running', 'cancelled', 'success'].map((state, index) => ({ id: index + 3, last_operation: { state } })) },
    { id: 3, jobs: [] },
  ]
  const jobs = agents.flatMap(agent => agent.jobs)
  const segments = summarizeBackupResults(jobs)
  assert.equal(segments.reduce((sum, segment) => sum + segment.count, 0), jobs.length)
  assert.ok(Math.abs(segments.reduce((sum, segment) => sum + segment.fraction, 0) - 1) < 1e-10)
  for (const segment of segments) {
    assert.equal(getBackupStateColor(segment.value), segment.color)
    const filtered = filterAgentJobs(agents, segment.value)
    assert.equal(filtered.flatMap(agent => agent.jobs).length, segment.count)
    assert.ok(filtered.every(agent => agent.jobs.length > 0))
  }
  assert.deepEqual(filterAgentJobs(agents, 'attention').flatMap(agent => agent.jobs).map(job => job.id), [3, 4])
  assert.equal(filterAgentJobs(agents, null), agents)
  assert.equal(agents[0].jobs.length, 2)
  assert.deepEqual(filterAgentJobs(agents, 'unknown'), [])
  assert.equal(getBackupStateColor('unknown'), 'grey-6')
  assert.ok(summarizeBackupResults([]).every(segment => segment.count === 0 && segment.fraction === 0))
  for (const state of backupStates) {
    const single = summarizeBackupResults([{ last_operation: state.value === 'never' ? null : { state: state.value } }])
    assert.equal(single.find(segment => segment.value === state.value).fraction, 1)
  }
})

test('first-backup guidance selects an online agent and disappears once jobs exist', () => {
  const source = readFileSync(new URL('../src/pages/IndexPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const agents = reactive({ agents: [] })
  const jobs = reactive({ agentJobs: [] })
  const { setupPrompt } = runInNewContext(`${source}\n;({ setupPrompt })`, {
    computed, ref, backupStates, getBackupState,
    useAgentStore: () => agents,
    useJobStore: () => jobs,
    onMounted: () => {},
    defineOptions: () => {},
    getApiErrorMessage: () => 'error',
  })
  assert.equal(setupPrompt.value.to, '/agents/install')
  agents.agents.push({ id: 1, online: false })
  assert.equal(setupPrompt.value.to, '/agents')
  agents.agents.push({ id: 2, online: true })
  assert.equal(setupPrompt.value.to.path, '/jobs')
  assert.equal(setupPrompt.value.to.query.add_for_agent, '2')
  agents.agents[1].online = false
  assert.equal(setupPrompt.value.to, '/agents')
  jobs.agentJobs.push({ id: 1, jobs: [{ id: 1, last_operation: null }] })
  assert.equal(setupPrompt.value, null)
})
