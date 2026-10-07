import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'
import { backupStates, getBackupStateColor } from '../src/utils/backup-results.js'
import { createQueuedReload } from '../src/utils/queued-reload.js'

test('background operation updates stay quiet and preserve data on failure', async () => {
  const source = readFileSync(new URL('../src/pages/AgentOperationDetailPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const route = { params: { operationId: '1', agentId: '1' }, query: {} }
  let resolveRequest
  const store = { agents: [], getOperation: () => new Promise(resolve => { resolveRequest = resolve }) }
  const page = runInNewContext(`${source}\n;({ operation, loading, loadOperation, queueOperationRefresh, progressValue, progressLabel, progressIndeterminate, showProgress, progressBasis, metricCards, duration, summaryColumns, technicalDetails, currentFiles, datasetArtifacts, jobDetailsCaption })`, {
    computed, ref, backupStates, stateColor: getBackupStateColor, createQueuedReload,
    useRoute: () => route,
    useAgentStore: () => store,
    useOperationStore: () => ({}),
    useQuasar: () => ({ notify: () => assert.fail('background error notification') }),
    subscribeToSocketEvents: async () => () => {},
    recordBrowserDiagnostic: () => {},
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
  await page.queueOperationRefresh(true)
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
  assert.equal(field(page.metricCards.value, 'Newly stored (job)'), '256 B')
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
  assert.equal(field(page.metricCards.value, 'Data processed (job)'), '544 B')
  page.operation.value.data.backup_progress = { total_known: true, bytes_total: 1024, bytes_processed: 544 }
  assert.equal(page.progressValue.value, 544 / 1024)
  assert.equal(field(page.metricCards.value, 'Data processed (job)'), '544 B / 1 KiB')
  assert.equal(page.currentFiles.value[0], '/old/file')
  page.operation.value.data.files_new = 0
  page.operation.value.data.data_added_packed = 0
  assert.equal(field(page.metricCards.value, 'Changes'), 'No changes')
  assert.equal(field(page.metricCards.value, 'Newly stored (job)'), '0 B')
  page.operation.value.data = {}
  assert.equal(field(page.metricCards.value, 'Changes'), undefined)
  assert.equal(field(page.metricCards.value, 'Newly stored (job)'), undefined)
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
  page.operation.value.data = { restore_phase: 'Streaming VM 101 to local-lvm', restore_bytes_restored: 512,
    restore_bytes_total: 1024, target_vmid: 101 }
  assert.equal(page.progressValue.value, 0.5)
  assert.equal(page.progressLabel.value, '50%')
  assert.equal(page.progressBasis.value, 'Streaming VM 101 to local-lvm')
  page.operation.value.data.restore_phase = 'Opening backup disks on demand'
  assert.equal(page.progressValue.value, null)

  page.operation.value = {
    id: 1, type: 'backup', state: 'running',
    data: { proxmox_progress: { vmid: 100, phase: 'backing_up', percent_done: 50, bytes_processed: 1024, bytes_total: 2048 } },
  }
  assert.equal(field(page.metricCards.value, 'VM data processed'), '1 KiB / 2 KiB')
  assert.equal(field(page.metricCards.value, 'Total VM size'), '2 KiB')
  assert.equal(page.progressValue.value, null) // An older agent's VM progress is not a job total.
  assert.equal(page.progressLabel.value, null)
  page.operation.value.data.proxmox_progress.percent_done = 0
  assert.equal(page.progressLabel.value, null)
  page.operation.value.data.proxmox_progress.percent_done = null
  assert.equal(page.progressLabel.value, null)
  assert.equal(page.showProgress.value, true)
  assert.equal(page.progressIndeterminate.value, true)

  for (const phase of ['snapshots', 'cleanup', 'finalizing', 'complete', 'failed']) {
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
    id: 1, type: 'backup', state: 'running', data: { backup_phase: 'backup', backup_bytes_total: 4096,
      backup_bytes_total_estimated: true, backup_data_complete: false },
  }
  const native = page.operation.value.data
  assert.equal(page.progressLabel.value, '0%')
  assert.equal(field(page.metricCards.value, 'Data processed (job)'), '0 B / 4 KiB')
  native.proxmox_progress = { vmid: 101, guest_index: 1, guests_total: 2, phase: 'backing_up',
    backup_mode: 'native_cbt', percent_done: 99, bytes_processed: 512, bytes_total: 512 }
  native.bytes_processed = 1024 // Restic has processed read AND reused data.
  assert.equal(page.progressLabel.value, '25%')
  assert.equal(field(page.metricCards.value, 'Disk data read (current VM)'), '512 B / 512 B')
  assert.match(page.progressBasis.value, /Data processed \(job\).*estimated.*VM 1 of 2/)
  for (const phase of ['cleanup', 'complete', 'snapshots']) {
    native.proxmox_progress.phase = phase
    assert.equal(page.progressLabel.value, '25%', phase)
  }
  native.proxmox_progress = { vmid: 102, guest_index: 2, guests_total: 2, phase: 'backing_up',
    bytes_processed: 0, bytes_total: 0, percent_done: null }
  assert.equal(page.progressLabel.value, '25%') // No reset, even with zero dirty blocks.
  assert.match(page.progressBasis.value, /VM 2 of 2/)
  native.bytes_processed = 2048
  assert.equal(page.progressLabel.value, '50%')
  native.backup_bytes_total = 8192 // Authoritative export size changed after preflight.
  assert.equal(page.progressLabel.value, '25%')
  native.bytes_processed = 8192
  assert.equal(page.progressLabel.value, '99%')
  native.backup_data_complete = true
  native.backup_bytes_total_estimated = false
  for (const phase of ['finalizing', 'check', 'retention', 'statistics', 'hooks']) {
    native.backup_phase = phase
    assert.equal(page.progressLabel.value, '100%', phase)
    assert.equal(page.progressIndeterminate.value, false, phase)
    assert.match(page.progressBasis.value, /Data processed \(job\) — /, phase)
  }
  native.bytes_processed = 2048
  native.partial_failure = true
  native.backup_data_complete = false
  for (const state of ['warning', 'failed', 'cancelled']) {
    page.operation.value.state = state
    assert.equal(page.progressLabel.value, '25%', state)
    assert.equal(page.progressIndeterminate.value, false, state)
  }
  native.partial_failure = false
  native.bytes_processed = 8192
  native.backup_data_complete = true
  page.operation.value.state = 'success'
  assert.equal(page.progressLabel.value, '100%')
  assert.equal(page.progressBasis.value, 'Data processed (job)')

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
  assert.equal(page.progressValue.value, null) // A dataset's scan still does not reveal the other datasets' sizes.
  assert.equal(page.showProgress.value, true)
  assert.equal(page.progressIndeterminate.value, true)
  assert.equal(field(page.metricCards.value, 'Dataset data processed'), '100 B / 200 B')

  data.backup_progress.bytes_processed = 199.9
  assert.equal(page.progressLabel.value, null)
  data.backup_progress.bytes_processed = 201
  assert.equal(page.showProgress.value, true)
  assert.equal(page.progressIndeterminate.value, true)
  assert.match(page.progressBasis.value, /Total size not yet known/)
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
  assert.match(page.progressBasis.value, /Total size not yet known/)

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

  page.operation.value.state = 'running'
  data.backup_phase = 'backup'
  data.truenas_progress.phase = 'backup'
  data.backup_bytes_total = null
  assert.equal(page.progressIndeterminate.value, true) // A known current dataset is not a job total.
  assert.match(page.progressBasis.value, /Data processed \(job\).*Total size not yet known/)
  data.backup_bytes_total = 8192
  data.backup_bytes_total_estimated = true
  data.backup_data_complete = false
  assert.equal(page.progressLabel.value, '25%')
  assert.match(page.progressBasis.value, /Data processed \(job\).*estimated.*Dataset 3 of 3/)
  assert.equal(field(page.metricCards.value, 'Data processed (job)'), '2 KiB / 8 KiB')
  for (const phase of ['snapshots', 'cleanup', 'backup']) {
    data.truenas_progress.phase = phase
    assert.equal(page.progressLabel.value, '25%', phase)
  }
  data.backup_progress = { total_known: false, complete: false, bytes_processed: 0, bytes_total: null }
  assert.equal(page.progressLabel.value, '25%') // Starting another dataset does not reset the job counter.
  data.backup_bytes_total = 4096 // Restic's existing scan corrects the estimate.
  data.backup_bytes_total_estimated = false
  assert.equal(page.progressLabel.value, '50%')
  assert.doesNotMatch(page.progressBasis.value, /estimated/)
  assert.equal(field(page.metricCards.value, 'Data processed (job)'), '2 KiB / 4 KiB')
  data.bytes_processed = 8192
  assert.equal(page.progressLabel.value, '99%') // Even underestimated totals cannot imply success.
  for (const state of ['warning', 'failed', 'cancelled']) {
    page.operation.value.state = state
    data.partial_failure = true
    assert.equal(page.progressLabel.value, '99%', state)
    data.bytes_processed = 2048
    assert.equal(page.progressLabel.value, '50%', state)
    data.bytes_processed = 8192
  }
  page.operation.value.state = 'running'
  data.partial_failure = false
  data.bytes_processed = 4096
  data.backup_data_complete = true
  data.truenas_progress.phase = 'cleanup'
  assert.equal(page.progressLabel.value, '100%')
  assert.match(page.progressBasis.value, /Cleaning up TrueNAS snapshots/)
  for (const phase of ['finalizing', 'check', 'retention', 'statistics', 'hooks']) {
    data.backup_phase = phase
    assert.equal(page.progressLabel.value, '100%', phase)
    assert.match(page.progressBasis.value, /Data processed \(job\) — /, phase)
  }
  page.operation.value.state = 'success'
  assert.equal(page.progressBasis.value, 'Data processed (job)')
  // Empty datasets never divide by zero, and reach 100% only after successful data completion.
  page.operation.value.state = 'running'
  data.backup_phase = 'backup'
  data.bytes_processed = 0
  data.backup_bytes_total = 0
  data.backup_data_complete = false
  assert.equal(page.progressIndeterminate.value, true)
  assert.match(page.progressBasis.value, /No file data to back up/)
  data.backup_data_complete = true
  assert.equal(page.progressLabel.value, '100%')

  for (const source of [
    {},
    { proxmox_progress: { vmid: 101, guest_index: 2, guests_total: 3, phase: 'backing_up', archive_filename: 'qemu-101.tar', percent_done: 99 } },
    { proxmox_progress: { vmid: 101, guest_index: 2, guests_total: 3, phase: 'backing_up', backup_mode: 'native_cbt', percent_done: 0 } },
    { truenas_progress: { dataset: 'tank/photos', dataset_index: 2, datasets_total: 3, phase: 'backup' } },
  ]) {
    page.operation.value = { id: 1, type: 'backup', state: 'running', data: {
      ...source, backup_phase: 'backup', bytes_processed: 1024, backup_bytes_total: 4096,
      backup_bytes_total_estimated: false, backup_data_complete: false, data_added_packed: 100,
    } }
    const job = page.operation.value.data
    assert.equal(page.progressLabel.value, '25%')
    assert.match(page.progressBasis.value, /^Data processed \(job\) — /)
    assert.equal(page.metricCards.value[0].label, 'Data processed (job)')
    assert.equal(page.metricCards.value[0].value, '1 KiB / 4 KiB')
    assert.equal(page.metricCards.value[1].label, 'Newly stored (job)')
    job.bytes_processed = 4096
    assert.equal(page.progressLabel.value, '99%')
    page.operation.value.state = 'warning'
    assert.equal(page.progressLabel.value, '99%') // An explicit false completion flag wins over final status.
    page.operation.value.state = 'running'
    job.backup_data_complete = true
    job.backup_phase = 'statistics'
    assert.equal(page.progressLabel.value, '100%')
    assert.match(page.progressBasis.value, /Updating repository statistics/)
    job.backup_data_complete = false
    job.backup_bytes_total = null
    job.proxmox_bytes_total = 4096 // New unknown totals must not fall back to older fields.
    assert.equal(page.progressIndeterminate.value, true)
  }
  page.operation.value.data = { proxmox_bytes_total: 4096, bytes_processed: 1024 }
  assert.equal(page.progressLabel.value, '25%') // Historical reports still use the common display.

  assert.equal(page.datasetArtifacts.value.length, 0)
  page.operation.value.artifacts = [
    { uuid: 'a', state: 'success', data: { dataset: 'tank/photos' }, snapshot_id: 'snapshot-a' },
    { uuid: 'b', state: 'running', data: { dataset: 'tank/documents' } },
    { uuid: 'c', state: 'failed', data: { dataset: 'tank/media' } },
    { uuid: 'manifest', state: 'success', data: {} },
  ]
  assert.equal(page.datasetArtifacts.value.length, 3)
  assert.equal(page.jobDetailsCaption.value, '3 datasets · 1 successful · 1 failed · 1 running')
  page.operation.value.artifacts[1].state = 'warning'
  assert.equal(page.jobDetailsCaption.value, '3 datasets · 1 successful · 1 failed · 1 warning')
  page.operation.value.artifacts = [page.operation.value.artifacts[0]]
  assert.equal(page.jobDetailsCaption.value, '1 dataset · 1 successful')
})
