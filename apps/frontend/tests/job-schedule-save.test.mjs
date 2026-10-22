import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'
import { sameSchedule, scheduleTiming } from '../src/utils/schedule.js'

const source = readFileSync(new URL('../src/pages/JobsPage.vue', import.meta.url), 'utf8')
  .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
const editorSource = readFileSync(new URL('../src/composables/useJobEditor.js', import.meta.url), 'utf8')
  .replace(/^import .*$/gm, '').replace('export function', 'function')

function setup(protocol) {
  const original = { id: 7, enabled: true, cron_string: '0 2 * * 1,5', repository_id: 2, retention_id: 3,
    config: { repository_check: { enabled: true, read_data: '100%' } } }
  const job = { id: 1, agent_id: 1, name: 'Original', config: {}, schedules: [original], actions: [] }
  const writes = [], notices = []
  const loads = { jobs: 0, agents: 0 }
  const agent = { id: 1, protocol_version: protocol }
  let confirm
  const jobStore = {
    agentJobs: [{ ...agent, jobs: [job] }],
    cancelJob: async id => { writes.push(['cancel', id]) },
    updateJob: async (id, data) => { writes.push(['job', id, data]); job.name = data.name },
    getJob: async () => job, loadJobs: async () => { loads.jobs++ },
    updateSchedule: async (id, data) => { writes.push(['schedule', id, data]) },
    createSchedule: async (id, data) => { writes.push(['create-schedule', id, data]) },
    deleteSchedule: async id => { writes.push(['delete-schedule', id]) },
  }
  const page = runInNewContext(`${source}
    ;({ onJobSubmit, editingJob, currentAgentId, showJobDialog, jobDialogSubmitting, confirmCancel })`, {
    ref, computed, backupStates: [], watch: () => {}, onMounted: () => {}, defineOptions: () => {},
    useJobEditor: runInNewContext(`${editorSource}\n;useJobEditor`, { ref, sameSchedule, jobsApi: jobStore }),
    useRoute: () => ({ query: {} }), useRouter: () => ({}),
    useQuasar: () => ({ notify: notice => notices.push(notice), dialog: () => ({ onOk: callback => { confirm = callback } }) }),
    useJobStore: () => jobStore, useAgentStore: () => ({ agents: [agent], loadAgents: async () => { loads.agents++ } }),
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
  return { page, payload, writes, notices, original, jobStore, loads, job, confirm: () => confirm() }
}

test('job cancellation waits for confirmation and ignores completed or replaced runs', async () => {
  const { page, job, jobStore, writes, confirm } = setup(12)
  job.last_operation = { id: 10, state: 'running' }
  page.confirmCancel(job)
  assert.equal(writes.length, 0)
  await confirm()
  assert.deepEqual(writes, [['cancel', 1]])
  page.confirmCancel(job)
  job.last_operation.state = 'success'
  await confirm()
  job.last_operation.state = 'running'
  page.confirmCancel(job)
  jobStore.agentJobs[0].jobs = [{ ...job, last_operation: { id: 11, state: 'running' } }]
  await confirm()
  assert.equal(writes.length, 1)
})

test('renaming a job on protocol 11 does not resubmit unchanged legacy schedules', async () => {
  const { page, payload, writes, notices } = setup(11)
  await page.onJobSubmit(payload())
  assert.deepEqual(writes.map(item => item[0]), ['job'])
  assert.equal(notices.at(-1).message, 'Job updated')
  assert.equal(page.showJobDialog.value, false)
})

test('changed and new schedules are rejected before any writes on protocol 23', async () => {
  const { page, payload, writes, notices } = setup(23)
  for (const kind of ['time', 'new', 'repository', 'check', 'enabled']) {
    const changed = payload()
    const schedule = changed.schedules[0]
    if (kind === 'time') schedule.timing.hour = 3
    if (kind === 'new') schedule.id = 'draft-1'
    if (kind === 'repository') schedule.repository_id = 5
    if (kind === 'check') schedule.config.repository_check.read_data = '5%'
    if (kind === 'enabled') schedule.enabled = false
    await page.onJobSubmit(changed)
    assert.equal(writes.length, 0, kind)
    assert.match(notices.at(-1).message, /protocol 24/)
    assert.equal(page.showJobDialog.value, true)
  }
})

test('protocol 24 sends actual schedule changes but ignores presentation and weekday order', async () => {
  const { page, payload, writes, original } = setup(24)
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

test('job editing preserves drafts and refreshes once after success or a partially saved failure', async () => {
  const { page, payload, notices, jobStore, loads } = setup(24)
  const draft = payload()
  const before = JSON.stringify(draft)
  await page.onJobSubmit(draft)
  assert.equal(JSON.stringify(draft), before)
  assert.deepEqual(loads, { jobs: 1, agents: 1 })
  page.showJobDialog.value = true
  draft.schedules[0].timing.hour = 5
  jobStore.updateSchedule = async () => { throw new Error('Schedule save failed') }
  await page.onJobSubmit(draft)
  assert.equal(notices.at(-1).message, 'Schedule save failed')
  assert.equal(page.showJobDialog.value, true)
  assert.equal(page.jobDialogSubmitting.value, false)
  assert.deepEqual(loads, { jobs: 2, agents: 2 })
  assert.equal(draft.schedules[0].timing.hour, 5)
})

test('retry-only edits are saved and old agents are rejected before the first write', async () => {
  for (const protocol of [17, 24]) {
    const { page, payload, writes, notices } = setup(protocol)
    const draft = payload()
    draft.schedules[0].config.retry = { enabled: true, max_attempts: 3, delay_minutes: 10 }
    await page.onJobSubmit(draft)
    if (protocol === 17) {
      assert.equal(writes.length, 0)
      assert.match(notices.at(-1).message, /protocol 18/)
    } else {
      assert.deepEqual(writes.map(item => item[0]), ['job', 'schedule'])
      assert.equal(writes[1][2].config.retry.max_attempts, 3)
    }
  }
})

test('partial creates retain IDs across retries without mutating drafts, and reset for the next editor', async () => {
  for (const editing of [false, true]) {
    const saved = { id: 1, actions: [], schedules: [] }
    const counts = { jobs: 0, actions: 0, schedules: 0 }
    let fail = true
    const jobsApi = {
      createJob: async () => { counts.jobs++; return { id: 1 } },
      updateJob: async () => {},
      getJob: async () => JSON.parse(JSON.stringify(saved)),
      createAction: async (_id, action) => {
        const id = ++counts.actions
        saved.actions.push({ ...action, id })
        return { id }
      },
      updateAction: async () => {},
      deleteAction: async id => { saved.actions = saved.actions.filter(action => action.id !== id) },
      createSchedule: async (_id, schedule) => {
        if (schedule.timing.hour === 3 && fail) throw new Error('Schedule save failed')
        const id = ++counts.schedules
        saved.schedules.push({ ...schedule, id })
        return { id }
      },
      updateSchedule: async () => {},
      deleteSchedule: async id => { saved.schedules = saved.schedules.filter(schedule => schedule.id !== id) },
    }
    const factory = runInNewContext(`${editorSource}\n;useJobEditor`, { ref, sameSchedule, jobsApi })
    const editor = factory({ loadJobs: async () => {} },
      { agents: [{ id: 1, protocol_version: 27 }], loadAgents: async () => {} },
      { withRecoveryKey: operation => operation(null) })
    const draft = { name: 'Files', config: {}, actions: [
      { id: 'draft-a', hook: 'start', module: 'command', data: { command: 'prepare' } },
      { id: 'draft-b', hook: 'end', module: 'command', data: { command: 'cleanup' } },
    ], schedules: [2, 3].map(hour => ({ id: `draft-${hour}`, repository_id: 2, enabled: true,
      timing: { type: 'daily', hour, minute: 0 }, config: {} })) }
    const original = JSON.stringify(draft)
    const job = editing ? { id: 1, schedules: [] } : null
    await assert.rejects(editor.saveJob(draft, job, 1), /Schedule save failed/)
    assert.equal(JSON.stringify(draft), original)
    assert.equal(editor.createdJobId.value, editing ? null : 1)
    fail = false
    await editor.saveJob(draft, job, 1)
    assert.deepEqual(counts, { jobs: editing ? 0 : 1, actions: 2, schedules: 2 })
    assert.equal(JSON.stringify(draft), original)
    draft.actions.shift()
    draft.schedules.shift()
    await editor.saveJob(draft, job, 1)
    assert.deepEqual(saved.actions.map(action => action.id), [2])
    assert.deepEqual(saved.schedules.map(schedule => schedule.id), [2])
    editor.reset()
    assert.equal(editor.createdJobId.value, null)
    saved.actions = saved.schedules = []
    await editor.saveJob({ ...draft, actions: [], schedules: [] }, null, 1)
    assert.equal(counts.jobs, editing ? 1 : 2)
  }
})
