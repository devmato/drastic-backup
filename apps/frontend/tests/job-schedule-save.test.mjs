import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'
import { sameSchedule, scheduleTiming } from '../src/utils/schedule.js'

const source = readFileSync(new URL('../src/pages/JobsPage.vue', import.meta.url), 'utf8')
  .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')

function setup(protocol) {
  const original = { id: 7, enabled: true, cron_string: '0 2 * * 1,5', repository_id: 2, retention_id: 3,
    config: { repository_check: { enabled: true, read_data: '100%' } } }
  const job = { id: 1, agent_id: 1, name: 'Original', config: {}, schedules: [original], actions: [] }
  const writes = [], notices = []
  const agent = { id: 1, protocol_version: protocol }
  const jobStore = {
    updateJob: async (id, data) => { writes.push(['job', id, data]); job.name = data.name },
    getJob: async () => job, loadJobs: async () => {},
    updateSchedule: async (id, data) => { writes.push(['schedule', id, data]) },
    createSchedule: async (id, data) => { writes.push(['create-schedule', id, data]) },
    deleteSchedule: async id => { writes.push(['delete-schedule', id]) },
  }
  const page = runInNewContext(`${source}
    ;({ onJobSubmit, editingJob, currentAgentId, showJobDialog })`, {
    ref, computed, sameSchedule, backupStates: [], watch: () => {}, onMounted: () => {}, defineOptions: () => {},
    useRoute: () => ({ query: {} }), useRouter: () => ({}),
    useQuasar: () => ({ notify: notice => notices.push(notice) }),
    useJobStore: () => jobStore, useAgentStore: () => ({ agents: [agent], loadAgents: async () => {} }),
    useRepositoryStore: () => ({ repositories: [] }), useOperationStore: () => ({}),
    useUserStore: () => ({ withRecoveryKey: operation => operation(null) }),
    shouldIgnoreApiError: () => false, getApiErrorMessage: error => error.message,
  })
  page.editingJob.value = job
  page.currentAgentId.value = agent.id
  page.showJobDialog.value = true
  const payload = () => ({ name: 'Renamed', config: {}, actions: [], schedules: [
    { ...original, timing: scheduleTiming(original), config: JSON.parse(JSON.stringify(original.config)) },
  ] })
  return { page, payload, writes, notices, original }
}

test('renaming a job on protocol 11 does not resubmit unchanged legacy schedules', async () => {
  const { page, payload, writes, notices } = setup(11)
  await page.onJobSubmit(payload())
  assert.deepEqual(writes.map(item => item[0]), ['job'])
  assert.equal(notices.at(-1).message, 'Job updated')
  assert.equal(page.showJobDialog.value, false)
})

test('changed and new typed schedules are rejected before any writes on protocol 11', async () => {
  const { page, payload, writes, notices } = setup(11)
  for (const kind of ['time', 'new', 'repository', 'retention', 'check', 'enabled']) {
    const changed = payload()
    const schedule = changed.schedules[0]
    if (kind === 'time') schedule.timing.hour = 3
    if (kind === 'new') schedule.id = 'draft-1'
    if (kind === 'repository') schedule.repository_id = 5
    if (kind === 'retention') schedule.retention_id = 5
    if (kind === 'check') schedule.config.repository_check.read_data = '5%'
    if (kind === 'enabled') schedule.enabled = false
    await page.onJobSubmit(changed)
    assert.equal(writes.length, 0, kind)
    assert.match(notices.at(-1).message, /protocol 12/)
    assert.equal(page.showJobDialog.value, true)
  }
})

test('protocol 12 sends actual schedule changes but ignores presentation and weekday order', async () => {
  const { page, payload, writes, original } = setup(12)
  const unchanged = payload()
  unchanged.schedules[0].timing.weekdays.reverse()
  unchanged.schedules[0].cron_description = 'A translated description'
  assert.equal(sameSchedule(original, unchanged.schedules[0]), true)
  await page.onJobSubmit(unchanged)
  assert.deepEqual(writes.map(item => item[0]), ['job'])
  writes.length = 0
  const changed = payload()
  changed.schedules[0].timing = { type: 'periodic', interval: 90, offset: 5 }
  await page.onJobSubmit(changed)
  assert.deepEqual(writes.map(item => item[0]), ['job', 'schedule'])
  assert.deepEqual(JSON.parse(JSON.stringify(writes[1][2].timing)), { type: 'periodic', interval: 90, offset: 5 })
})
