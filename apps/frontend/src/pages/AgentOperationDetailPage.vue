<template>
  <q-page class="q-pa-md">
    <q-breadcrumbs class="q-pb-md">
      <q-breadcrumbs-el label="Agents" icon="desktop_windows" to="/agents" />
      <q-breadcrumbs-el :label="operation?.agent_hostname || `Agent #${route.params.agentId}`" />
      <q-breadcrumbs-el label="Operations" :to="operationListRoute" />
      <q-breadcrumbs-el :label="`Operation #${route.params.operationId}`" />
    </q-breadcrumbs>

    <div class="row items-center q-col-gutter-md q-row-gutter-sm q-mb-lg">
      <div class="col">
        <div class="text-h5">{{ pageTitle }}</div>
      </div>
      <div class="col-auto">
        <q-btn color="primary" icon="refresh" label="Refresh" :loading="loading" @click="loadOperation()" />
      </div>
      <div v-if="isRestoreOperation && operation?.state === 'running'" class="col-auto">
        <q-btn outline color="negative" label="Cancel restore" :loading="cancelling" @click="cancelRestore" />
      </div>
    </div>

    <q-inner-loading :showing="loading && !operation" />

    <q-banner v-if="!loading && !operation" class="bg-grey-2 text-grey-8">
      Operation not found or no longer available.
    </q-banner>

    <div v-if="operation" class="q-gutter-md">
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
                aria-label="Operation progress"
                :aria-valuetext="progressLabel"
              >
                <div v-if="progressLabel" class="absolute-full flex flex-center">
                  <q-badge color="white" text-color="black" :label="progressLabel" />
                </div>
              </q-linear-progress>
              <div v-if="operation.state === 'running' && progressBasis" class="text-caption" role="status">{{ progressBasis }}</div>
            </div>

            <div>
              <div class="row q-col-gutter-md text-body2">
                <div v-for="(column, index) in summaryColumns" :key="index" class="col-12 col-md-6">
                  <div v-for="field in column" :key="field.label" class="row no-wrap items-baseline q-py-xs">
                    <span class="col-auto q-mr-sm">{{ field.label }}:</span>
                    <div class="col ellipsis">
                      <q-badge v-if="field.color" :color="field.color" :label="field.value" class="text-capitalize" />
                      <router-link v-else-if="field.to" :to="field.to" class="text-primary">{{ field.value }}</router-link>
                      <span v-else class="text-weight-medium">{{ field.value }}</span>
                      <q-tooltip>{{ field.fullValue || field.value }}</q-tooltip>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <q-list v-if="currentFiles.length > 0" dense>
              <q-item-label header class="q-px-none">Current files</q-item-label>
              <q-item v-for="(file, index) in currentFiles" :key="`${index}-${file}`" class="q-px-none">
                <q-item-section>
                  <q-item-label class="text-monospace ellipsis">
                    {{ file }}
                    <q-tooltip>{{ file }}</q-tooltip>
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
                <span class="col ellipsis">
                  {{ field.value }}
                  <q-tooltip>{{ field.value }}</q-tooltip>
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

      <q-card flat bordered>
        <q-table
          v-if="sortedLogs.length > 0"
          :rows="sortedLogs"
          :columns="logColumns"
          v-model:pagination="logPagination"
          :filter="logFilter"
          :filter-method="filterLogs"
          :rows-per-page-options="[0]"
          row-key="sequence"
          flat
          dense
          hide-bottom
          separator="none"
        >
          <template #top-left>
            <div class="row items-center no-wrap">
              <div>
                <div class="text-h6">Log</div>
              </div>
              <q-spinner v-if="operation.state === 'running'" class="q-ml-sm" color="blue" size="sm" />
            </div>
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
          <div class="row items-center no-wrap q-mb-md">
            <div>
              <div class="text-h6">Log</div>
            </div>
            <q-spinner v-if="operation.state === 'running'" class="q-ml-sm" color="blue" size="sm" />
          </div>
          <pre class="db-code-block db-code-block--wrap q-ma-none">{{ operation.log }}</pre>
        </q-card-section>

        <q-card-section v-else>
          <div class="row items-center no-wrap q-mb-md">
            <div>
              <div class="text-h6">Log</div>
            </div>
            <q-spinner v-if="operation.state === 'running'" class="q-ml-sm" color="blue" size="sm" />
          </div>
          <q-banner class="bg-grey-2 text-grey-8">No log entries available.</q-banner>
        </q-card-section>
      </q-card>
    </div>
  </q-page>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { copyToClipboard, useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { useOperationStore } from 'stores/operation'
import { subscribeToSocketEvents } from 'src/utils/socket'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

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
const loading = ref(false)
const logFilter = ref('')
const logPagination = ref({ rowsPerPage: 0, sortBy: 'time', descending: false })

let stopOperationSocketListener = null
let operationRefreshInFlight = false
let operationRefreshQueued = false
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
const proxmoxProgress = computed(() => operationData.value.proxmox_progress)
const isRestoreOperation = computed(() => operation.value?.type === 'restore')
const bytesProcessed = computed(() => numberOrNull(operationData.value.bytes_processed))
const bytesTotal = computed(() => {
  const total = numberOrNull(operationData.value.bytes_total)
  return total === 0 && bytesProcessed.value > 0 ? null : total
})
const filesProcessed = computed(() => numberOrNull(operationData.value.files_processed))
const filesTotal = computed(() => numberOrNull(operationData.value.files_total))
const restoreBytesRestored = computed(() => numberOrNull(operationData.value.restore_bytes_restored ?? operationData.value.bytes_restored))
const restoreBytesTotal = computed(() => numberOrNull(operationData.value.restore_bytes_total ?? operationData.value.total_bytes))
const restoreFilesRestored = computed(() => numberOrNull(operationData.value.restore_files_restored ?? operationData.value.files_restored))
const restoreFilesTotal = computed(() => numberOrNull(operationData.value.restore_files_total ?? operationData.value.total_files))

const progressSource = computed(() => {
  if (proxmoxProgress.value) {
    const progress = proxmoxProgress.value
    const phases = {
      backing_up: `Backing up VM ${progress.vmid}`,
      finalizing: 'Finalizing restic snapshot',
      manifest: 'Saving manifest',
      complete: 'VM backup completed',
      failed: 'VM backup failed',
    }
    return {
      value: progress.percent_done == null ? null : clampProgress(progress.percent_done / 100),
      basis: `VM ${progress.guest_index} of ${progress.guests_total} — ${phases[progress.phase] || 'Running'}`,
    }
  }

  if (isRestoreOperation.value) {
    if (['success', 'warning'].includes(operation.value?.state)) {
      return { value: 1 }
    }

    if (operationData.value.restore_phase && operationData.value.restore_phase !== 'Restoring VMA archive') {
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
const showProgress = computed(() => !!proxmoxProgress.value || progressValue.value !== null || progressIndeterminate.value)
const progressLabel = computed(() => progressValue.value === null ? null : `${Math.round(progressValue.value * 100)}%`)
const progressBasis = computed(() => progressSource.value.basis)

const metricCards = computed(() => {
  const metrics = []

  if (proxmoxProgress.value) {
    const progress = proxmoxProgress.value
    return [
      ...(progress.bytes_processed != null ? [{ label: 'VM data processed', value: formatBytes(progress.bytes_processed) }] : []),
      ...(progress.bytes_total > 0 ? [{ label: 'Total VM size', value: formatBytes(progress.bytes_total) }] : []),
      ...(progress.archive_bytes != null ? [{ label: 'VMA archive size', value: formatBytes(progress.archive_bytes) }] : []),
    ]
  }

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
        fullValue: operationData.value.include_paths.join('\n'),
      })
    }

    return metrics
  }

  if (bytesProcessed.value !== null || bytesTotal.value !== null) {
    metrics.push({
      label: bytesProcessed.value === null ? 'Total data' : 'Data processed',
      value: formatProcessed(bytesProcessed.value, bytesTotal.value, formatBytes),
    })
  }

  if (filesProcessed.value !== null || filesTotal.value !== null) {
    metrics.push({
      label: filesProcessed.value === null ? 'Total files' : 'Files',
      value: formatProcessed(filesProcessed.value, filesTotal.value, formatNumber),
    })
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

  const addedBytes = numberOrNull(operationData.value.data_added_packed ?? operationData.value.data_added)
  if (addedBytes !== null) {
    metrics.push({ label: 'Added to repo', value: formatBytes(addedBytes) })
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
      { label: 'Device', value: current.agent_hostname || `Agent #${route.params.agentId}`, to: operationListRoute.value },
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
  if (proxmoxProgress.value) return [proxmoxProgress.value.archive_filename]

  const files = Array.isArray(operationData.value.current_files) ? operationData.value.current_files : []
  return files.map(formatCurrentFile).filter(Boolean).slice(0, 8)
})

function stateColor(state) {
  const colors = { running: 'blue', success: 'green', warning: 'orange', failed: 'red', cancelled: 'grey' }
  return colors[state] || 'grey'
}

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
  const stopListener = await subscribeToSocketEvents(async (payload) => {
    if (payload?.name === `operationupdate${operationId}`) {
      await queueOperationRefresh()
    }
  })
  if (disposed || stopOperationSocketListener || String(route.params.operationId) !== operationId || operation.value?.state !== 'running') {
    stopListener()
  } else {
    stopOperationSocketListener = stopListener
  }
}

async function loadOperation(background = false) {
  const operationId = String(route.params.operationId)
  if (!background) loading.value = true
  try {
    const updatedOperation = await agentStore.getOperation(operationId)
    if (disposed || String(route.params.operationId) !== operationId) return
    operation.value = updatedOperation
    await syncOperationSocketListener()
  } catch (e) {
    if (disposed || String(route.params.operationId) !== operationId || background) return
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Could not load operation'), color: 'red', position: 'top' })
  } finally {
    if (!background && String(route.params.operationId) === operationId) loading.value = false
  }
}

async function queueOperationRefresh() {
  if (operationRefreshInFlight) {
    operationRefreshQueued = true
    return
  }

  operationRefreshInFlight = true
  try {
    do {
      operationRefreshQueued = false
      await loadOperation(true)
    } while (operationRefreshQueued && !disposed)
  } finally {
    operationRefreshInFlight = false
  }
}

watch(
  () => route.params.operationId,
  async () => {
    cleanupOperationSocketListener()
    operation.value = null
    operationRefreshQueued = false
    await loadOperation()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  disposed = true
  cleanupOperationSocketListener()
})

defineOptions({ name: 'AgentOperationDetailPage' })
</script>
