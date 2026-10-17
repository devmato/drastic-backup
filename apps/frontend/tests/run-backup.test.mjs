import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, effectScope, ref, watch } from 'vue'

test('backup dialog selects an agent job and prevents duplicate starts', async t => {
  const scope = effectScope()
  t.after(() => scope.stop())
  const source = readFileSync(new URL('../src/components/jobs/RunBackupDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const assigned = { id: 2, name: 'Assigned', location: '/assigned' }
  const jobStore = { agentJobs: [
    { id: 1, jobs: [{ id: 3, name: 'Running', last_operation: { state: 'running' } }, { id: 4, name: 'Files' }] },
    { id: 6, jobs: [{ id: 7, name: 'Another agent’s job' }] },
  ] }
  const props = { agent: { id: 1, online: true, repositories: [assigned] }, jobId: null }
  const visible = ref(true)
  const requests = [], events = []
  let finish
  const dialog = scope.run(() => runInNewContext(`${source}\n;({ selectedJobId, selectedRepositoryId, jobOptions, submitBackup })`, {
    computed, ref, watch, defineProps: () => props, defineModel: () => visible,
    defineEmits: () => (name, data) => events.push({ name, data }), defineOptions: () => {},
    useQuasar: () => ({ notify: () => {} }),
    useJobStore: () => jobStore, useRepositoryStore: () => ({ repositories: [assigned] }),
    useUserStore: () => ({ withRecoveryKey: operation => operation('recovery-key') }),
    useOperationStore: () => ({ startBackupJob: data => {
      requests.push(data)
      return new Promise(resolve => { finish = resolve })
    } }),
    shouldIgnoreApiError: () => false, getApiErrorMessage: error => error.message,
  }))
  assert.deepEqual(Array.from(dialog.jobOptions.value, option => option.value), [3, 4])
  assert.equal(dialog.selectedJobId.value, 4)
  assert.equal(dialog.selectedRepositoryId.value, 2)
  const first = dialog.submitBackup()
  await dialog.submitBackup()
  assert.equal(requests.length, 1)
  assert.deepEqual({ ...requests[0] }, { jobId: 4, repositoryId: 2, recoveryKey: 'recovery-key' })
  finish({ operation_id: 9, dispatch_status: 'unknown' })
  await first
  assert.equal(visible.value, false)
  assert.equal(events[0].name, 'started')
  assert.equal(events[0].data.operation_id, 9)
  assert.equal(events[0].data.agent_id, 1)
})
