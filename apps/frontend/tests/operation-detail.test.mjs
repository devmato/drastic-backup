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
  const store = { agents: [], getOperation: () => new Promise(resolve => { resolveRequest = resolve }) }
  const page = runInNewContext(`${source}\n;({ operation, loading, loadOperation, queueOperationRefresh, progressValue, progressLabel, progressIndeterminate, showProgress, progressBasis, metricCards, duration, summaryColumns, technicalDetails, currentFiles })`, {
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
    Date: class extends Date { static now() { return Date.parse('2026-10-04T07:54:47Z') } },
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
  assert.equal(page.progressValue.value, null)
  page.operation.value.data.bytes_total = 1024
  assert.equal(page.metricCards.value[0].value, '1 KiB')

  page.operation.value = {
    id: 1, type: 'backup', state: 'success', source: 'manual',
    agent_hostname: 'Backup host', agent_display_name: 'Backup host', job_id: 2, job_name: 'Documents', repository_id: 3, repository_name: 'Offsite',
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
  // Older agents' scan-in-progress totals are not a reliable denominator.
  assert.equal(page.progressValue.value, null)
  assert.equal(field(page.metricCards.value, 'Data processed'), '544 B')
  page.operation.value.data.backup_progress = { total_known: true, bytes_total: 1024, bytes_processed: 544 }
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

  const originalTimezone = process.env.TZ
  process.env.TZ = 'Europe/Berlin'
  try {
    for (const started of ['2026-10-04T07:54:41+00:00', '2026-10-04T09:54:41+02:00']) {
      page.operation.value = { id: 1, state: 'running', started, ended: null, data: {} }
      assert.equal(page.duration.value, '6s')
      assert.equal(field(page.summaryColumns.value[0], 'Started'), new Date('2026-10-04T09:54:41+02:00').toLocaleString())
    }
  } finally {
    if (originalTimezone === undefined) delete process.env.TZ
    else process.env.TZ = originalTimezone
  }

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
  assert.equal(page.showProgress.value, true)
  assert.equal(page.progressIndeterminate.value, true)

  for (const phase of ['finalizing', 'manifest', 'complete', 'failed']) {
    page.operation.value.data.proxmox_progress.phase = phase
    page.operation.value.data.proxmox_progress.percent_done = 100
    assert.equal(page.showProgress.value, true, phase)
    assert.equal(page.progressIndeterminate.value, true, phase)
    assert.equal(page.currentFiles.value.length, 0)
  }
  page.operation.value.state = 'success'
  page.operation.value.data.proxmox_progress.percent_done = null
  assert.equal(page.progressValue.value, 1)
  assert.equal(page.progressIndeterminate.value, false)
  page.operation.value.state = 'warning'
  page.operation.value.data.partial_failure = true
  assert.equal(page.showProgress.value, false)
  assert.equal(page.progressIndeterminate.value, false)

  page.operation.value = {
    id: 1, type: 'backup', state: 'running',
    data: {
      backup_phase: 'backup', bytes_processed: 2048, bytes_total: null,
      truenas_progress: { phase: 'backup', dataset: 'tank/photos', dataset_index: 2, datasets_total: 3 },
      backup_progress: { total_known: false, complete: false, bytes_processed: 100, bytes_total: null },
    },
  }
  const data = page.operation.value.data
  assert.equal(page.showProgress.value, true)
  assert.equal(page.progressIndeterminate.value, true)
  assert.equal(page.progressLabel.value, null)
  assert.match(page.progressBasis.value, /Dataset 2 of 3 · tank\/photos/)
  assert.match(page.progressBasis.value, /Total size not yet known/)
  assert.equal(field(page.metricCards.value, 'Data processed (job)'), '2 KiB')
  assert.equal(field(page.metricCards.value, 'Dataset data processed'), '100 B')
  data.backup_progress.total_known = true
  data.backup_progress.bytes_total = 200
  assert.equal(page.progressValue.value, 0.5)
  assert.equal(page.showProgress.value, true)
  assert.equal(page.progressIndeterminate.value, false)
  assert.equal(field(page.metricCards.value, 'Dataset data processed'), '100 B / 200 B')

  data.backup_progress.bytes_processed = 199.9
  assert.equal(page.progressLabel.value, '99%')
  data.backup_progress.bytes_processed = 201
  assert.equal(page.showProgress.value, true)
  assert.equal(page.progressIndeterminate.value, true)
  assert.match(page.progressBasis.value, /Source size changed/)
  data.backup_progress.bytes_processed = 200
  data.backup_progress.complete = true
  assert.equal(page.showProgress.value, true)
  assert.equal(page.progressIndeterminate.value, true)
  assert.match(page.progressBasis.value, /Finalizing snapshot/)

  data.backup_progress = { total_known: false, complete: false, bytes_total: null, bytes_processed: 0 }
  data.truenas_progress.dataset_index = 3
  assert.equal(page.showProgress.value, true)
  assert.equal(page.progressIndeterminate.value, true)
  assert.match(page.progressBasis.value, /Dataset 3 of 3/)
  data.backup_progress = { total_known: true, complete: false, bytes_total: 0, bytes_processed: 0 }
  assert.equal(page.showProgress.value, true)
  assert.equal(page.progressIndeterminate.value, true)
  assert.match(page.progressBasis.value, /No file data/)

  for (const phase of ['snapshots', 'cleanup']) {
    data.truenas_progress.phase = phase
    data.backup_progress = { total_known: true, bytes_total: 100, bytes_processed: 100 }
    assert.equal(page.showProgress.value, true)
    assert.equal(page.progressIndeterminate.value, true)
    assert.equal(page.currentFiles.value.length, 0)
  }
  for (const phase of ['preparing', 'finalizing', 'check', 'retention', 'statistics', 'hooks']) {
    data.backup_phase = phase
    assert.equal(page.showProgress.value, true, phase)
    assert.equal(page.progressIndeterminate.value, true, phase)
    assert.ok(page.progressBasis.value)
  }
  for (const state of ['success', 'warning', 'failed', 'cancelled']) {
    page.operation.value.state = state
    assert.equal(page.progressIndeterminate.value, false, state)
  }
})
