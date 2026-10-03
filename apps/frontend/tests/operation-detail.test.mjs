import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'
import { getBackupStateColor } from '../src/utils/backup-results.js'

test('background operation updates stay quiet and preserve data on failure', async () => {
  const source = readFileSync(new URL('../src/pages/AgentOperationDetailPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const route = { params: { operationId: '1', agentId: '1' }, query: {} }
  let resolveRequest
  const store = { getOperation: () => new Promise(resolve => { resolveRequest = resolve }) }
  const page = runInNewContext(`${source}\n;({ operation, loading, loadOperation, queueOperationRefresh, progressValue, progressLabel, metricCards, duration, summaryColumns, technicalDetails, currentFiles })`, {
    computed, ref, stateColor: getBackupStateColor,
    useRoute: () => route,
    useAgentStore: () => store,
    useOperationStore: () => ({}),
    useQuasar: () => ({ notify: () => assert.fail('background error notification') }),
    subscribeToSocketEvents: async () => () => {},
    watch: () => {},
    onBeforeUnmount: () => {},
    defineOptions: () => {},
    shouldIgnoreApiError: () => false,
    getApiErrorMessage: () => 'error',
  })
  const initialLoad = page.loadOperation()
  assert.equal(page.loading.value, true)
  resolveRequest({ id: 1, state: 'running', data: {} })
  await initialLoad
  assert.equal(page.loading.value, false)

  const refresh = page.queueOperationRefresh()
  assert.equal(page.loading.value, false)
  resolveRequest({ id: 1, type: 'backup', state: 'running', data: { bytes_processed: 512, bytes_total: 0 } })
  await refresh
  assert.equal(page.operation.value.data.bytes_processed, 512)
  assert.equal(page.metricCards.value[0].value, '512 B')
  assert.equal(page.progressValue.value, null)
  assert.equal(page.progressLabel.value, null)

  store.getOperation = async () => { throw new Error('temporary failure') }
  const previousOperation = page.operation.value
  await page.queueOperationRefresh()
  assert.equal(page.operation.value, previousOperation)

  page.operation.value = { id: 1, type: 'backup', state: 'success', data: { bytes_processed: 1024, bytes_total: 2048 } }
  assert.equal(page.progressValue.value, 1)
  assert.equal(page.progressLabel.value, '100%')
  page.operation.value.state = 'warning'
  assert.equal(page.progressValue.value, 1)
  page.operation.value.state = 'failed'
  assert.equal(page.progressValue.value, 0.5)
  page.operation.value.data.bytes_total = 1024
  assert.equal(page.metricCards.value[0].value, '1 KiB')

  page.operation.value = {
    id: 1, type: 'backup', state: 'success', source: 'manual',
    agent_hostname: 'Backup host', job_id: 2, job_name: 'Documents', repository_id: 3, repository_name: 'Offsite',
    started: '2026-10-01T22:38:44', ended: '2026-10-01T23:47:32',
    data: {
      bytes_processed: 544, files_processed: 1, files_total: 1,
      files_new: 1, files_changed: 0, files_unmodified: 0,
      duration: 3, data_added: 544, data_added_packed: 256,
      snapshot_id: 'snapshot-id', current_files: ['/old/file'],
    },
  }
  const field = (fields, label) => fields.find(item => item.label === label)?.value
  assert.equal(field(page.summaryColumns.value[0], 'Status'), 'success')
  assert.equal(page.duration.value, '1h 8m 48s')
  assert.equal(field(page.summaryColumns.value[0], 'Job'), 'Documents')
  assert.equal(field(page.summaryColumns.value[0], 'Repository'), 'Offsite')
  assert.equal(field(page.summaryColumns.value[1], 'Device'), 'Backup host')
  assert.equal(field(page.metricCards.value, 'Files'), '1')
  assert.equal(field(page.metricCards.value, 'Changes'), '1 new')
  assert.equal(field(page.metricCards.value, 'Added to repo'), '256 B')
  assert.equal(field(page.metricCards.value, 'Snapshot'), undefined)
  assert.equal(field(page.technicalDetails.value, 'Snapshot'), 'snapshot-id')
  assert.equal(field(page.technicalDetails.value, 'Restic duration'), '3s')
  assert.equal(page.currentFiles.value.length, 0)

  page.operation.value.state = 'running'
  page.operation.value.ended = null
  page.operation.value.data.bytes_total = 1024
  assert.equal(field(page.summaryColumns.value[0], 'Status'), 'running')
  assert.equal(page.progressValue.value, 544 / 1024)
  assert.equal(field(page.metricCards.value, 'Data processed'), '544 B / 1 KiB')
  assert.equal(page.currentFiles.value[0], '/old/file')
  page.operation.value.data.files_new = 0
  page.operation.value.data.data_added_packed = 0
  assert.equal(field(page.metricCards.value, 'Changes'), 'No changes')
  assert.equal(field(page.metricCards.value, 'Added to repo'), '0 B')
  page.operation.value.data = {}
  assert.equal(field(page.metricCards.value, 'Changes'), undefined)
  assert.equal(field(page.metricCards.value, 'Added to repo'), undefined)
  page.operation.value.started = 'invalid'
  assert.equal(page.duration.value, null)

  page.operation.value = {
    id: 1, type: 'restore', state: 'success',
    data: { restore_bytes_restored: 1024, restore_files_restored: 2, restore_location: '/restore', snapshot_id: 'restore-snapshot' },
  }
  assert.equal(field(page.metricCards.value, 'Data restored'), '1 KiB')
  assert.equal(field(page.metricCards.value, 'Restore target'), '/restore')
  assert.equal(field(page.technicalDetails.value, 'Snapshot'), 'restore-snapshot')
  assert.equal(page.progressValue.value, 1)

  page.operation.value = {
    id: 1, type: 'restore', state: 'running',
    data: { restore_phase: 'Importing VM 101 into local-lvm', restore_bytes_restored: 1024, restore_bytes_total: 1024, target_vmid: 101 },
  }
  assert.equal(page.progressValue.value, null)
  assert.equal(field(page.metricCards.value, 'Target VM'), 101)

  page.operation.value = {
    id: 1, type: 'backup', state: 'running',
    data: { proxmox_progress: { vmid: 100, phase: 'backing_up', percent_done: 50, bytes_processed: 1024, bytes_total: 2048 } },
  }
  assert.equal(field(page.metricCards.value, 'VM data processed'), '1 KiB')
  assert.equal(field(page.metricCards.value, 'Total VM size'), '2 KiB')
  assert.equal(page.progressValue.value, 0.5)
  assert.equal(page.progressLabel.value, '50%')
  page.operation.value.data.proxmox_progress.percent_done = 0
  assert.equal(page.progressLabel.value, '0%')
  page.operation.value.data.proxmox_progress.percent_done = null
  assert.equal(page.progressLabel.value, null)
})
