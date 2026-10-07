<template>
  <q-page class="q-pa-md">
    <PageHeader :title="pageTitle">
      <template #breadcrumbs>
        <q-breadcrumbs>
          <q-breadcrumbs-el label="Agents" icon="desktop_windows" to="/agents" />
          <q-breadcrumbs-el :label="agentDisplayName" :to="`/agents/${route.params.agentId}`" />
          <q-breadcrumbs-el label="Operations" :to="operationListRoute" />
          <q-breadcrumbs-el :label="`Operation #${route.params.operationId}`" />
        </q-breadcrumbs>
      </template>
      <template #actions>
        <q-btn flat round color="primary" icon="refresh" aria-label="Refresh operation" :loading="loading" @click="loadOperation()">
          <q-tooltip>Refresh operation</q-tooltip>
        </q-btn>
        <q-btn v-if="isRestoreOperation && operation?.state === 'running'" outline no-caps no-wrap color="negative" label="Cancel restore" :loading="cancelling" @click="cancelRestore" />
      </template>
    </PageHeader>

    <q-inner-loading :showing="loading && !operation" />

    <q-banner v-if="!loading && !operation" :class="$q.dark.isActive ? 'bg-grey-9 text-grey-4' : 'bg-grey-2 text-grey-8'">
      Operation not found or no longer available.
    </q-banner>

    <div v-if="operation" class="column q-gutter-y-md">
      <q-card flat bordered>
        <q-card-section>
          <div class="q-gutter-md">
            <div v-if="showProgress" class="q-gutter-xs">
              <q-linear-progress
                rounded
                size="24px"
                :color="stateColor(operation.state)"
                :value="progressValue || 0"
                :indeterminate="progressIndeterminate"
                :aria-label="progressBasis || 'Operation progress'"
                :aria-valuetext="progressLabel"
              >
                <div v-if="progressLabel" class="absolute-full flex flex-center">
                  <q-badge color="white" text-color="black" :label="progressLabel" />
                </div>
              </q-linear-progress>
            </div>
            <div v-if="progressBasis" class="text-caption db-break-word" role="status">{{ progressBasis }}</div>

            <div>
              <div class="row q-col-gutter-md text-body2">
                <div v-for="(column, index) in summaryColumns" :key="index" class="col-12 col-md-6">
                  <div v-for="field in column" :key="field.label" class="row no-wrap items-baseline q-py-xs">
                    <span class="col-auto q-mr-sm" :title="field.tooltip">{{ field.label }}:</span>
                    <div class="col db-break-word">
                      <q-badge v-if="field.color" :color="field.color" text-color="grey-10" :label="field.value" class="text-capitalize" />
                      <router-link v-else-if="field.to" :to="field.to" class="text-primary">{{ field.value }}</router-link>
                      <span v-else class="text-weight-medium">{{ field.value }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <q-list v-if="currentFiles.length > 0" dense>
              <q-item-label header class="q-px-none">Current files</q-item-label>
              <q-item v-for="(file, index) in currentFiles" :key="`${index}-${file}`" class="q-px-none">
                <q-item-section>
                  <q-item-label class="text-monospace db-break-word">
                    {{ file }}
                  </q-item-label>
                </q-item-section>
              </q-item>
            </q-list>

            <q-expansion-item
              dense dense-toggle switch-toggle-side label="Technical details"
              header-class="q-px-none" class="text-body2"
            >
              <div v-for="field in technicalDetails" :key="field.label" class="row no-wrap items-center q-mt-sm">
                <span class="col-auto q-mr-sm">{{ field.label }}:</span>
                <span class="col db-break-word">
                  {{ field.value }}
                </span>
                <q-btn
                  v-if="field.label === 'Snapshot'"
                  flat dense round size="sm" icon="content_copy" aria-label="Copy snapshot ID"
                  @click="copySnapshot"
                >
                  <q-tooltip>Copy snapshot ID</q-tooltip>
                </q-btn>
              </div>
            </q-expansion-item>
          </div>
        </q-card-section>
      </q-card>

      <q-card v-if="jobArtifacts.length > 0" flat bordered>
        <q-expansion-item
          label="Job details" :caption="jobDetailsCaption" switch-toggle-side
          header-class="q-pa-md text-subtitle1 text-weight-medium"
        >
          <q-separator />
          <q-card-section><h2 class="text-subtitle1 text-weight-medium q-my-none">{{ datasetArtifacts.length ? 'Dataset backups' : 'VM backups' }}</h2></q-card-section>
          <q-list separator>
            <q-item v-for="artifact in jobArtifacts" :key="artifact.uuid">
              <q-item-section>
                <q-item-label class="db-break-word">{{ artifact.data.dataset || `VM ${artifact.data.vmid}${artifact.data.guest_name ? ` (${artifact.data.guest_name})` : ''}` }}</q-item-label>
                <q-item-label caption class="db-break-word">{{ artifact.snapshot_id || 'No completed Restic snapshot' }}</q-item-label>
                <q-item-label v-for="field in nativeArtifactDetails(artifact)" :key="field.label" caption class="db-break-word">
                  {{ field.label }}: {{ field.value }}
                </q-item-label>
              </q-item-section>
              <q-item-section side><q-badge :color="stateColor(artifact.state)" text-color="grey-10" :label="artifact.state" /></q-item-section>
            </q-item>
          </q-list>
        </q-expansion-item>
      </q-card>

      <q-card flat bordered>
        <q-table
          v-if="sortedLogs.length > 0"
          :rows="sortedLogs"
          :columns="logColumns"
          :table-header-class="$q.dark.isActive ? 'bg-dark text-grey-4' : 'bg-grey-1 text-grey-7'"
          v-model:pagination="logPagination"
          :filter="logFilter"
          :filter-method="filterLogs"
          :rows-per-page-options="[0]"
          row-key="sequence"
          flat
          hide-bottom
        >
          <template #top-left>
            <h2 class="text-subtitle1 text-weight-medium q-my-none">Log</h2>
          </template>
          <template #top-right>
            <q-input
              v-model="logFilter"
              dense
              outlined
              clearable
              debounce="200"
              placeholder="Search logs"
            >
              <template #prepend>
                <q-icon name="search" />
              </template>
            </q-input>
          </template>
          <template #body-cell-time="props">
            <q-td :props="props" class="text-no-wrap">
              {{ formatDate(props.row.created) }}
            </q-td>
          </template>
          <template #body-cell-level="props">
            <q-td :props="props">
              <q-badge :color="logLevelColor(props.row.level)" :label="props.row.level || '-'" />
            </q-td>
          </template>
          <template #body-cell-message="props">
            <q-td :props="props">
              <div class="db-log-message">{{ props.row.message }}</div>
              <pre v-if="props.row.data && Object.keys(props.row.data).length > 0" class="db-code-block db-code-block--wrap q-mt-sm q-mb-none">{{ formatJson(props.row.data) }}</pre>
            </q-td>
          </template>
        </q-table>

        <q-card-section v-else-if="operation.log">
          <h2 class="text-subtitle1 text-weight-medium q-mt-none q-mb-md">Log</h2>
          <pre class="db-code-block db-code-block--wrap q-ma-none">{{ operation.log }}</pre>
        </q-card-section>

        <q-card-section v-else>
          <h2 class="text-subtitle1 text-weight-medium q-mt-none q-mb-md">Log</h2>
          <q-banner :class="$q.dark.isActive ? 'bg-grey-9 text-grey-4' : 'bg-grey-2 text-grey-8'">No log entries available.</q-banner>
        </q-card-section>
      </q-card>
    </div>
  </q-page>
</template>

<script setup>
import PageHeader from 'components/PageHeader.vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { copyToClipboard, useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { useOperationStore } from 'stores/operation'
import { subscribeToSocketEvents } from 'src/utils/socket'
import { createQueuedReload } from 'src/utils/queued-reload'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import { backupStates, getBackupStateColor as stateColor } from 'src/utils/backup-results'
import { recordBrowserDiagnostic } from 'src/utils/diagnostics'

const route = useRoute()
const $q = useQuasar()
const agentStore = useAgentStore()
const operationStore = useOperationStore()
const cancelling = ref(false)

async function cancelRestore() {
  cancelling.value = true
  try {
    await operationStore.cancelRestore(operation.value.id)
    $q.notify({ message: 'Restore cancellation requested', color: 'info' })
    await loadOperation()
  } catch (e) {
    if (!shouldIgnoreApiError(e)) $q.notify({ message: getApiErrorMessage(e), color: 'negative' })
  } finally {
    cancelling.value = false
  }
}

const operation = ref(null)
const agentDisplayName = computed(() => agentStore.agents.find(agent => String(agent.id) === String(route.params.agentId))?.display_name
  || operation.value?.agent_display_name || `Agent #${route.params.agentId}`)
const loading = ref(false)
const logFilter = ref('')
const logPagination = ref({ rowsPerPage: 0, sortBy: 'time', descending: false })

let stopOperationSocketListener = null
let operationListenerVersion = 0
let operationRequest = 0
let queueOperationRefresh = createQueuedReload(() => loadOperation(true))
let disposed = false

const pageTitle = computed(() => {
  if (!operation.value) return `Operation #${route.params.operationId}`
  return `Operation #${operation.value.id} - ${operation.value.type_text || operation.value.type || 'Details'}`
})

const operationListRoute = computed(() => ({
  path: `/agents/${route.params.agentId}/operations`,
  query: route.query,
}))

const sortedLogs = computed(() => {
  const logs = Array.isArray(operation.value?.logs) ? operation.value.logs : []
  return [...logs].sort((left, right) => (left.sequence || 0) - (right.sequence || 0))
})

const logColumns = [
  {
    name: 'time',
    label: 'Time',
    field: 'created',
    align: 'left',
    sortable: true,
    sort: (left, right) => new Date(left || 0) - new Date(right || 0),
  },
  { name: 'level', label: 'Level', field: 'level', align: 'left', sortable: true },
  { name: 'message', label: 'Message', field: 'message', align: 'left', sortable: true },
]

const operationData = computed(() => operation.value?.data || {})
const datasetArtifacts = computed(() => (operation.value?.artifacts || []).filter(artifact => artifact.data?.dataset))
const jobArtifacts = computed(() => (operation.value?.artifacts || []).filter(artifact => artifact.data?.dataset
  || artifact.data?.vmid && !artifact.artifact_key?.endsWith(':manifest')))
const backupModeLabels = { snapshot: 'Snapshot', native: 'Native — full read', native_cbt: 'Native — CBT' }
function nativeArtifactDetails(artifact) {
  const data = artifact.data || {}
  if (data.backup_method !== 'native') return []
  return [
    { label: 'Requested', value: backupModeLabels[data.requested_mode] || data.requested_mode },
    { label: 'Used', value: backupModeLabels[data.effective_mode] || data.effective_mode },
    ...(data.fallback_reason ? [{ label: 'Full-read reason', value: data.fallback_reason }] : []),
    ...(data.bytes_read != null ? [{ label: 'Disk data read', value: formatBytes(data.bytes_read) }] : []),
    ...(data.bytes_reused != null ? [{ label: 'Disk data reused', value: formatBytes(data.bytes_reused) }] : []),
    ...(data.data_added_packed != null ? [{ label: 'Newly stored', value: formatBytes(data.data_added_packed) }] : []),
    ...(data.cleanup ? [{ label: 'Cleanup', value: data.cleanup }] : []),
  ]
}
const jobDetailsCaption = computed(() => {
  const counts = backupStates.map(state => ({
    label: state.label.toLowerCase(),
    count: jobArtifacts.value.filter(artifact => artifact.state === state.value).length,
  })).filter(state => state.count > 0)
  const unit = datasetArtifacts.value.length ? 'dataset' : 'VM'
  return [`${jobArtifacts.value.length} ${unit}${jobArtifacts.value.length === 1 ? '' : 's'}`,
    ...counts.map(state => `${state.count} ${state.label}`)].join(' · ')
})
const proxmoxProgress = computed(() => operationData.value.proxmox_progress)
const truenasProgress = computed(() => operationData.value.truenas_progress)
const backupProgress = computed(() => operationData.value.backup_progress)
const isBackupOperation = computed(() => operation.value?.type === 'backup')
const isRestoreOperation = computed(() => operation.value?.type === 'restore')
const bytesProcessed = computed(() => numberOrNull(operationData.value.bytes_processed))
const bytesTotal = computed(() => {
  if (isBackupOperation.value && operation.value?.state === 'running' && !backupProgress.value) return null
  const total = numberOrNull(operationData.value.bytes_total)
  return total === 0 && bytesProcessed.value > 0 ? null : total
})
const filesProcessed = computed(() => numberOrNull(operationData.value.files_processed))
const filesTotal = computed(() => isBackupOperation.value && operation.value?.state === 'running' && !backupProgress.value
  ? null : numberOrNull(operationData.value.files_total))
const restoreBytesRestored = computed(() => numberOrNull(operationData.value.restore_bytes_restored ?? operationData.value.bytes_restored))
const restoreBytesTotal = computed(() => numberOrNull(operationData.value.restore_bytes_total ?? operationData.value.total_bytes))
const restoreFilesRestored = computed(() => numberOrNull(operationData.value.restore_files_restored ?? operationData.value.files_restored))
const restoreFilesTotal = computed(() => numberOrNull(operationData.value.restore_files_total ?? operationData.value.total_files))
const backupBytesTotal = computed(() => {
  const data = operationData.value
  // Older agents may supply a source-specific job total; never use a single VM/dataset as the job total.
  return numberOrNull('backup_bytes_total' in data ? data.backup_bytes_total
    : data.proxmox_bytes_total
      ?? (!proxmoxProgress.value && !truenasProgress.value && backupProgress.value?.total_known
        ? bytesTotal.value ?? backupProgress.value.bytes_total : null))
})
const backupTotalEstimated = computed(() => operationData.value.backup_bytes_total_estimated ?? false)
const backupDataComplete = computed(() => operationData.value.backup_data_complete
  ?? (['success', 'warning'].includes(operation.value?.state) && !operationData.value.partial_failure))
const backupPhaseLabel = computed(() => {
  const phase = operationData.value.backup_phase
  const phases = {
    preparing: 'Preparing backup', finalizing: 'Finalizing backup', check: 'Checking repository',
    retention: 'Applying retention policy', statistics: 'Updating repository statistics', hooks: 'Running backup actions',
  }
  if (phase && phase !== 'backup') return phases[phase] || 'Finalizing backup'
  const vm = proxmoxProgress.value
  if (vm) {
    const phases = { snapshots: 'Preparing VM backup', backing_up: 'Backing up', cleanup: 'Cleaning up VM backup',
      finalizing: 'Finalizing snapshot', complete: 'VM backup completed', failed: 'VM backup failed' }
    return `VM ${vm.guest_index} of ${vm.guests_total} · ${vm.vmid} — ${phases[vm.phase] || 'Preparing VM backup'}`
  }
  const dataset = truenasProgress.value
  if (dataset?.phase === 'snapshots') return 'Creating TrueNAS snapshots'
  if (dataset?.phase === 'cleanup') return 'Cleaning up TrueNAS snapshots'
  const scope = dataset ? `Dataset ${dataset.dataset_index} of ${dataset.datasets_total} · ${dataset.dataset} — ` : ''
  return `${scope}${backupProgress.value?.complete ? 'Finalizing snapshot' : 'Backing up'}`
})

const progressSource = computed(() => {
  if (isBackupOperation.value) {
    const total = backupBytesTotal.value
    const value = backupDataComplete.value ? 1 : total > 0
      ? Math.min(0.99, clampProgress((bytesProcessed.value ?? 0) / total)) : null
    const basis = `Data processed${backupTotalEstimated.value ? ' — estimated' : ''}`
    if (operation.value?.state !== 'running') return { value, basis: value === null ? null : basis }
    const sizeHint = total === null ? ' · Total size not yet known'
      : total === 0 && !backupDataComplete.value ? ' · No file data to back up' : ''
    return {
      value, basis: `${basis} — ${backupPhaseLabel.value}${sizeHint}`,
    }
  }

  if (isRestoreOperation.value) {
    if (['success', 'warning'].includes(operation.value?.state)) {
      return { value: 1 }
    }

    if (operationData.value.restore_phase && operationData.value.restore_phase !== 'Restoring VM archive'
      && !String(operationData.value.restore_phase).startsWith('Streaming VM ')) {
      return { value: null, basis: operationData.value.restore_phase }
    }

    if (restoreBytesTotal.value && restoreBytesTotal.value > 0) {
      return {
        value: clampProgress((restoreBytesRestored.value || 0) / restoreBytesTotal.value),
        basis: operationData.value.restore_phase,
      }
    }

    return { value: null, basis: operationData.value.restore_phase }
  }

  if (['success', 'warning'].includes(operation.value?.state)) {
    return { value: 1 }
  }

  if (bytesTotal.value && bytesTotal.value > 0) {
    return {
      value: clampProgress((bytesProcessed.value || 0) / bytesTotal.value),
    }
  }

  if (filesTotal.value && filesTotal.value > 0) {
    return {
      value: clampProgress((filesProcessed.value || 0) / filesTotal.value),
    }
  }

  return { value: null }
})

const progressValue = computed(() => progressSource.value.value)
const progressIndeterminate = computed(() => progressValue.value === null && operation.value?.state === 'running')
const showProgress = computed(() => progressValue.value !== null || progressIndeterminate.value)
const progressLabel = computed(() => progressValue.value === null ? null : `${Math.floor(progressValue.value * 100)}%`)
const progressBasis = computed(() => progressSource.value.basis)

const metricCards = computed(() => {
  const metrics = []

  if (isRestoreOperation.value) {
    if (operationData.value.target_vmid) {
      metrics.push({ label: 'Target VM', value: operationData.value.target_vmid })
      metrics.push({ label: 'Target storage', value: operationData.value.target_storage })
    }
    if (restoreBytesRestored.value !== null) {
      metrics.push({ label: 'Data restored', value: formatBytes(restoreBytesRestored.value) })
    }

    if (restoreFilesRestored.value !== null) {
      metrics.push({ label: 'Files restored', value: formatNumber(restoreFilesRestored.value) })
    }

    if (restoreFilesTotal.value !== null) {
      metrics.push({ label: 'Selected files', value: formatNumber(restoreFilesTotal.value) })
    }

    if (operationData.value.restore_location) {
      metrics.push({ label: 'Restore target', value: operationData.value.restore_location })
    }

    if (Array.isArray(operationData.value.include_paths) && operationData.value.include_paths.length > 0) {
      metrics.push({
        label: 'Selected paths',
        value: operationData.value.include_paths.join(', '),
      })
    }

    return metrics
  }

  const total = isBackupOperation.value ? backupBytesTotal.value : bytesTotal.value
  if (bytesProcessed.value !== null || total !== null || bytesTotal.value !== null) {
    metrics.push({
      label: 'Data processed',
      value: formatProcessed(total !== null ? bytesProcessed.value ?? 0 : bytesProcessed.value,
        operation.value?.state === 'running' ? total : total ?? bytesTotal.value, formatBytes),
      tooltip: 'Data processed across the entire job, including data reused from previous backups.',
    })
  }

  if (filesProcessed.value !== null || filesTotal.value !== null) {
    metrics.push({
      label: isBackupOperation.value ? 'Files processed' : 'Files',
      value: formatProcessed(filesProcessed.value, filesTotal.value, formatNumber),
    })
  }

  if (isBackupOperation.value) {
    const addedBytes = numberOrNull(operationData.value.data_added_packed)
    metrics.push({ label: 'Data transferred', value: addedBytes === null ? 'Not yet known' : formatBytes(addedBytes),
      tooltip: 'New data written to the repository after deduplication and compression. Updated after completed files or archive streams; finalized at backup completion. Excludes network overhead.' })
    if (operation.value?.state === 'running' && operationData.value.backup_phase === 'backup'
      && !backupDataComplete.value && !backupProgress.value?.complete
      && (!proxmoxProgress.value || proxmoxProgress.value.phase === 'backing_up')
      && (!truenasProgress.value || truenasProgress.value.phase === 'backup')) {
      const speed = numberOrNull(operationData.value.processing_bytes_per_second)
      metrics.push({ label: 'Processing speed', value: speed !== null && speed >= 0 ? `${formatBytes(speed)}/s` : 'Not yet known',
        tooltip: 'Restic processing rate over the last 10 seconds, including reused data. This is not the network upload speed.' })
    }
  }

  if (proxmoxProgress.value && operation.value?.state === 'running') {
    const progress = proxmoxProgress.value
    const native = ['native', 'native_cbt'].includes(progress.backup_mode) || 'proxmox_bytes_total' in operationData.value
    if (native && progress.bytes_processed != null) metrics.push({ label: 'Disk data read (current VM)',
      value: formatProcessed(numberOrNull(progress.bytes_processed), numberOrNull(progress.bytes_total), formatBytes) })
  }

  const newFiles = numberOrNull(operationData.value.files_new)
  const modifiedFiles = numberOrNull(operationData.value.files_changed)
  const changes = [
    newFiles > 0 ? `${formatNumber(newFiles)} new` : null,
    modifiedFiles > 0 ? `${formatNumber(modifiedFiles)} modified` : null,
  ].filter(Boolean)
  if (changes.length > 0 || (newFiles === 0 && modifiedFiles === 0)) {
    metrics.push({ label: 'Changes', value: changes.join(' · ') || 'No changes' })
  }

  return metrics
})

const duration = computed(() => {
  if (!operation.value?.started) return null
  const end = operation.value.ended
    ? new Date(operation.value.ended).getTime()
    : operation.value.state === 'running' ? Date.now() : NaN
  const seconds = (end - new Date(operation.value.started).getTime()) / 1000
  return Number.isFinite(seconds) && seconds >= 0 ? formatDuration(seconds) : null
})

const summaryColumns = computed(() => {
  const current = operation.value
  if (!current) return []
  return [
    [
      { label: 'Type', value: current.type_text || current.type || '-' },
      { label: 'Status', value: current.state || '-', color: stateColor(current.state) },
      ...(current.started ? [{ label: 'Started', value: formatDate(current.started) }] : []),
      ...(current.ended ? [{ label: 'Ended', value: formatDate(current.ended) }] : []),
      ...(duration.value !== null ? [{ label: 'Duration', value: duration.value }] : []),
      ...(current.job_id ? [{ label: 'Job', value: current.job_name || `#${current.job_id}` }] : []),
      ...(current.repository_id ? [{ label: 'Repository', value: current.repository_name || `#${current.repository_id}` }] : []),
    ],
    [
      { label: 'Device', value: agentDisplayName.value, to: operationListRoute.value },
      ...metricCards.value,
    ],
  ]
})

const technicalDetails = computed(() => {
  const current = operation.value
  if (!current) return []
  const details = [
    { label: 'Operation ID', value: `#${current.id}` },
    { label: 'Source', value: current.source || '-' },
  ]
  if (current.job_id) details.push({ label: 'Job ID', value: `#${current.job_id}` })
  if (current.repository_id) details.push({ label: 'Repository ID', value: `#${current.repository_id}` })
  const resticDuration = numberOrNull(operationData.value.duration)
  if (resticDuration !== null) {
    details.push({ label: 'Restic duration', value: formatDuration(resticDuration) })
  }
  const fileChanges = changeSummary('files_new', 'files_changed', 'files_unmodified')
  if (fileChanges) details.push({ label: 'Files', value: fileChanges })
  const dirChanges = changeSummary('dirs_new', 'dirs_changed', 'dirs_unmodified')
  if (dirChanges) details.push({ label: 'Directories', value: dirChanges })
  if (operationData.value.snapshot_id) {
    details.push({ label: 'Snapshot', value: String(operationData.value.snapshot_id) })
  }

  return details
})

async function copySnapshot() {
  try {
    await copyToClipboard(String(operationData.value.snapshot_id))
    $q.notify({ message: 'Snapshot ID copied', color: 'positive', position: 'top' })
  } catch {
    $q.notify({ message: 'Could not copy snapshot ID', color: 'negative', position: 'top' })
  }
}

function formatProcessed(processed, total, formatter) {
  if (processed !== null && total !== null && operation.value?.state === 'running') {
    return `${formatter(processed)} / ${formatter(total)}`
  }
  return formatter(processed ?? total)
}

const currentFiles = computed(() => {
  if (isRestoreOperation.value || operation.value?.state !== 'running') return []
  if (operationData.value.backup_phase && operationData.value.backup_phase !== 'backup') return []
  if (truenasProgress.value && truenasProgress.value.phase !== 'backup') return []
  if (proxmoxProgress.value) return proxmoxProgress.value.phase === 'backing_up' ? [proxmoxProgress.value.archive_filename].filter(Boolean) : []

  const files = Array.isArray(operationData.value.current_files) ? operationData.value.current_files : []
  return files.map(formatCurrentFile).filter(Boolean).slice(0, 8)
})

function logLevelColor(level) {
  const colors = { debug: 'grey', info: 'blue', warning: 'orange', error: 'red', critical: 'purple' }
  return colors[String(level || '').toLowerCase()] || 'grey'
}

function formatDate(isoStr) {
  if (!isoStr) return '-'
  return new Date(isoStr).toLocaleString()
}

function formatJson(value) {
  return JSON.stringify(value, null, 2)
}

function filterLogs(rows, terms) {
  const search = String(terms || '').trim().toLowerCase()
  if (!search) return rows

  return rows.filter((row) => {
    const values = [
      formatDate(row.created),
      row.level,
      row.message,
      row.data ? JSON.stringify(row.data) : '',
    ]

    return values.some(value => String(value || '').toLowerCase().includes(search))
  })
}

function numberOrNull(value) {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function formatNumber(value) {
  const number = numberOrNull(value)
  return number === null ? 'Unknown' : new Intl.NumberFormat().format(number)
}

function formatBytes(value) {
  const number = numberOrNull(value)
  if (number === null) return 'Unknown'

  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = Math.abs(number)
  let unitIndex = 0

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }

  const signedSize = number < 0 ? -size : size
  return `${signedSize.toLocaleString(undefined, { maximumFractionDigits: unitIndex === 0 ? 0 : 1 })} ${units[unitIndex]}`
}

function formatDuration(seconds) {
  const totalSeconds = Math.max(0, Math.round(seconds))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const remainingSeconds = totalSeconds % 60

  if (hours > 0) return `${hours}h ${minutes}m ${remainingSeconds}s`
  if (minutes > 0) return `${minutes}m ${remainingSeconds}s`
  return `${remainingSeconds}s`
}

function clampProgress(value) {
  return Math.min(1, Math.max(0, value))
}

function changeSummary(newKey, changedKey, unchangedKey) {
  return [[newKey, 'new'], [changedKey, 'modified'], [unchangedKey, 'unchanged']]
    .map(([key, label]) => {
      const count = numberOrNull(operationData.value[key])
      return count === null ? null : `${formatNumber(count)} ${label}`
    })
    .filter(Boolean)
    .join(' · ')
}

function formatCurrentFile(file) {
  if (typeof file === 'string') return file
  if (file?.path) return file.path
  if (file?.name) return file.name
  return JSON.stringify(file)
}

function cleanupOperationSocketListener() {
  operationListenerVersion++
  if (stopOperationSocketListener) {
    stopOperationSocketListener()
    stopOperationSocketListener = null
  }
}

async function syncOperationSocketListener() {
  if (!operation.value || operation.value.state !== 'running') {
    cleanupOperationSocketListener()
    return
  }
  if (stopOperationSocketListener || disposed) return

  const operationId = String(operation.value.id)
  const listenerVersion = operationListenerVersion
  const stopListener = await subscribeToSocketEvents((payload) => {
    if (disposed || listenerVersion !== operationListenerVersion) return
    if (payload?.name === `operationupdate${operationId}`) {
      void queueOperationRefresh(Boolean(payload.data?.state && payload.data.state !== 'running'))
    }
  })
  if (disposed || listenerVersion !== operationListenerVersion || stopOperationSocketListener || String(route.params.operationId) !== operationId || operation.value?.state !== 'running') {
    stopListener()
  } else {
    stopOperationSocketListener = stopListener
  }
}

async function loadOperation(background = false) {
  const request = ++operationRequest
  const operationId = String(route.params.operationId)
  if (!background) loading.value = true
  try {
    const updatedOperation = await agentStore.getOperation(operationId)
    if (disposed || request !== operationRequest || String(route.params.operationId) !== operationId) return
    operation.value = updatedOperation
    recordBrowserDiagnostic('operation.loaded', Number(operationId))
    await syncOperationSocketListener()
  } catch (e) {
    recordBrowserDiagnostic('operation.load_failed', Number(operationId), e?.response?.status || null)
    if (disposed || request !== operationRequest || String(route.params.operationId) !== operationId || background) return
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Could not load operation'), color: 'red', position: 'top' })
  } finally {
    if (!disposed && request === operationRequest) loading.value = false
  }
}

watch(
  () => route.params.operationId,
  async () => {
    cleanupOperationSocketListener()
    queueOperationRefresh.cancel()
    queueOperationRefresh = createQueuedReload(() => loadOperation(true))
    operation.value = null
    await loadOperation()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  disposed = true
  queueOperationRefresh.cancel()
  cleanupOperationSocketListener()
})

defineOptions({ name: 'AgentOperationDetailPage' })
</script>
