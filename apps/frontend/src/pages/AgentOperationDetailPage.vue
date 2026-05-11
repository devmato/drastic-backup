<template>
  <q-page class="q-pa-md">
    <q-breadcrumbs class="q-pb-md">
      <q-breadcrumbs-el label="Agents" icon="desktop_windows" to="/agents" />
      <q-breadcrumbs-el :label="`Agent #${route.params.agentId}`" />
      <q-breadcrumbs-el label="Operations" :to="operationListRoute" />
      <q-breadcrumbs-el :label="`Operation #${route.params.operationId}`" />
    </q-breadcrumbs>

    <div class="row items-center q-col-gutter-md q-row-gutter-sm q-mb-lg">
      <div class="col">
        <div class="text-h5">{{ pageTitle }}</div>
      </div>
      <div class="col-auto">
        <q-btn color="primary" icon="refresh" label="Refresh" :loading="loading" @click="loadOperation" />
      </div>
    </div>

    <q-inner-loading :showing="loading && !operation" />

    <q-banner v-if="!loading && !operation" class="bg-grey-2 text-grey-8">
      Operation not found or no longer available.
    </q-banner>

    <div v-if="operation" class="q-gutter-md">
      <q-card flat bordered>
        <q-card-section class="q-pa-sm">
          <q-markup-table flat dense separator="none">
            <tbody>
              <tr>
                <td>
                  <div class="text-caption text-grey-7">Status</div>
                  <q-badge :color="stateColor(operation.state)" :label="operation.state || '-'" />
                </td>
                <td>
                  <div class="text-caption text-grey-7">Type</div>
                  <div class="text-body2 text-weight-medium">{{ operation.type_text || operation.type || '-' }}</div>
                </td>
                <td>
                  <div class="text-caption text-grey-7">Source</div>
                  <div class="text-body2 text-weight-medium">{{ operation.source || '-' }}</div>
                </td>
                <td>
                  <div class="text-caption text-grey-7">Job</div>
                  <div class="text-body2 text-weight-medium">{{ operation.job_id ? `#${operation.job_id}` : '-' }}</div>
                </td>
              </tr>
              <tr>
                <td>
                  <div class="text-caption text-grey-7">Repository</div>
                  <div class="text-body2 text-weight-medium">{{ operation.repository_id ? `#${operation.repository_id}` : '-' }}</div>
                </td>
                <td>
                  <div class="text-caption text-grey-7">Started</div>
                  <div class="text-body2 text-weight-medium">{{ formatDate(operation.started) }}</div>
                </td>
                <td>
                  <div class="text-caption text-grey-7">Ended</div>
                  <div class="text-body2 text-weight-medium">{{ formatDate(operation.ended) }}</div>
                </td>
                <td />
              </tr>
            </tbody>
          </q-markup-table>
        </q-card-section>

        <q-card-section>
          <div class="q-gutter-md">
            <div v-if="showProgress" class="q-gutter-xs">
              <div class="row items-center no-wrap">
                <div class="text-subtitle2">Progress</div>
                <q-space />
                <div class="text-caption text-grey-7 q-ml-sm">{{ progressLabel }}</div>
              </div>
              <q-linear-progress
                rounded
                size="10px"
                :color="stateColor(operation.state)"
                :value="progressValue || 0"
                :indeterminate="progressIndeterminate"
              />
              <div v-if="progressBasis" class="text-caption text-grey-7">{{ progressBasis }}</div>
            </div>

            <q-list v-if="metricCards.length > 0" dense>
              <q-item v-for="metric in metricCards" :key="metric.label" class="q-px-none">
                <q-item-section>
                  <q-item-label caption>{{ metric.label }}</q-item-label>
                  <q-item-label class="text-body2 text-weight-medium ellipsis">
                    {{ metric.value }}
                    <q-tooltip v-if="metric.fullValue">{{ metric.fullValue }}</q-tooltip>
                  </q-item-label>
                </q-item-section>
              </q-item>
            </q-list>

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

            <q-banner v-if="!hasStructuredMetrics" class="bg-grey-2 text-grey-8">
              No structured metrics available.
            </q-banner>
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
import { useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { subscribeToSocketEvents } from 'src/utils/socket'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const route = useRoute()
const $q = useQuasar()
const agentStore = useAgentStore()

const operation = ref(null)
const loading = ref(false)
const logFilter = ref('')
const logPagination = ref({ rowsPerPage: 0, sortBy: 'time', descending: false })

let stopOperationSocketListener = null
let operationRefreshInFlight = false
let operationRefreshQueued = false

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
    label: 'Zeit',
    field: 'created',
    align: 'left',
    sortable: true,
    sort: (left, right) => new Date(left || 0) - new Date(right || 0),
  },
  { name: 'level', label: 'Level', field: 'level', align: 'left', sortable: true },
  { name: 'message', label: 'Meldung', field: 'message', align: 'left', sortable: true },
]

const operationData = computed(() => operation.value?.data || {})
const isRestoreOperation = computed(() => operation.value?.type === 'restore')
const bytesProcessed = computed(() => numberOrNull(operationData.value.bytes_processed))
const bytesTotal = computed(() => numberOrNull(operationData.value.bytes_total))
const filesProcessed = computed(() => numberOrNull(operationData.value.files_processed))
const filesTotal = computed(() => numberOrNull(operationData.value.files_total))
const restoreBytesRestored = computed(() => numberOrNull(operationData.value.restore_bytes_restored ?? operationData.value.bytes_restored))
const restoreBytesTotal = computed(() => numberOrNull(operationData.value.restore_bytes_total ?? operationData.value.total_bytes))
const restoreFilesRestored = computed(() => numberOrNull(operationData.value.restore_files_restored ?? operationData.value.files_restored))
const restoreFilesTotal = computed(() => numberOrNull(operationData.value.restore_files_total ?? operationData.value.total_files))

const progressSource = computed(() => {
  if (isRestoreOperation.value) {
    if (['success', 'warning'].includes(operation.value?.state)) {
      return { value: 1, basis: restoreProgressBasis.value || 'Restore completed' }
    }

    if (restoreBytesTotal.value && restoreBytesTotal.value > 0) {
      return {
        value: clampProgress((restoreBytesRestored.value || 0) / restoreBytesTotal.value),
        basis: `${formatBytes(restoreBytesRestored.value)} of ${formatBytes(restoreBytesTotal.value)} restored`,
      }
    }

    return { value: null, basis: '' }
  }

  if (bytesTotal.value && bytesTotal.value > 0) {
    return {
      value: clampProgress((bytesProcessed.value || 0) / bytesTotal.value),
      basis: `${formatBytes(bytesProcessed.value)} of ${formatBytes(bytesTotal.value)}`,
    }
  }

  if (filesTotal.value && filesTotal.value > 0) {
    return {
      value: clampProgress((filesProcessed.value || 0) / filesTotal.value),
      basis: `${formatNumber(filesProcessed.value)} of ${formatNumber(filesTotal.value)} files`,
    }
  }

  if (['success', 'warning'].includes(operation.value?.state)) {
    return { value: 1, basis: 'Operation completed' }
  }

  return { value: null, basis: '' }
})

const progressValue = computed(() => progressSource.value.value)
const progressIndeterminate = computed(() => progressValue.value === null && operation.value?.state === 'running')
const showProgress = computed(() => progressValue.value !== null || progressIndeterminate.value)
const progressLabel = computed(() => {
  if (progressIndeterminate.value) return 'Running'
  return `${Math.round((progressValue.value || 0) * 100)}%`
})
const progressBasis = computed(() => progressSource.value.basis)

const restoreProgressBasis = computed(() => {
  if (restoreBytesRestored.value !== null) {
    return `${formatBytes(restoreBytesRestored.value)} restored`
  }

  if (restoreFilesRestored.value !== null) {
    return `${formatNumber(restoreFilesRestored.value)} files restored`
  }

  return ''
})

const metricCards = computed(() => {
  const metrics = []

  if (isRestoreOperation.value) {
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

    if (operationData.value.snapshot_id) {
      const snapshotId = String(operationData.value.snapshot_id)
      metrics.push({ label: 'Snapshot', value: truncateMiddle(snapshotId), fullValue: snapshotId })
    }

    return metrics
  }

  if (bytesProcessed.value !== null || bytesTotal.value !== null) {
    metrics.push({ label: 'Data processed', value: `${formatBytes(bytesProcessed.value)} / ${formatBytes(bytesTotal.value)}` })
  }

  if (filesProcessed.value !== null || filesTotal.value !== null) {
    metrics.push({ label: 'Files processed', value: `${formatNumber(filesProcessed.value)} / ${formatNumber(filesTotal.value)}` })
  }

  const fileChanges = changeSummary('files_new', 'files_changed', 'files_unmodified')
  if (fileChanges) {
    metrics.push({ label: 'Files changed', value: fileChanges })
  }

  const dirChanges = changeSummary('dirs_new', 'dirs_changed', 'dirs_unmodified')
  if (dirChanges) {
    metrics.push({ label: 'Directories changed', value: dirChanges })
  }

  const duration = numberOrNull(operationData.value.duration)
  if (duration !== null) {
    metrics.push({ label: 'Duration', value: formatDuration(duration) })
  }

  if (operationData.value.snapshot_id) {
    const snapshotId = String(operationData.value.snapshot_id)
    metrics.push({ label: 'Snapshot', value: truncateMiddle(snapshotId), fullValue: snapshotId })
  }

  return metrics
})

const currentFiles = computed(() => {
  if (isRestoreOperation.value) return []

  const files = Array.isArray(operationData.value.current_files) ? operationData.value.current_files : []
  return files.map(formatCurrentFile).filter(Boolean).slice(0, 8)
})

const hasStructuredMetrics = computed(() => metricCards.value.length > 0 || currentFiles.value.length > 0 || showProgress.value)

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
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function formatNumber(value) {
  const number = numberOrNull(value)
  return number === null ? '-' : new Intl.NumberFormat().format(number)
}

function formatBytes(value) {
  const number = numberOrNull(value)
  if (number === null) return '-'

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
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
  const newCount = numberOrNull(operationData.value[newKey])
  const changedCount = numberOrNull(operationData.value[changedKey])
  const unchangedCount = numberOrNull(operationData.value[unchangedKey])

  if (newCount === null && changedCount === null && unchangedCount === null) {
    return null
  }

  return `${formatNumber(newCount)} new / ${formatNumber(changedCount)} changed / ${formatNumber(unchangedCount)} unchanged`
}

function formatCurrentFile(file) {
  if (typeof file === 'string') return file
  if (file?.path) return file.path
  if (file?.name) return file.name
  return JSON.stringify(file)
}

function truncateMiddle(value, maxLength = 20) {
  if (value.length <= maxLength) return value

  const visibleLength = maxLength - 3
  const startLength = Math.ceil(visibleLength / 2)
  const endLength = Math.floor(visibleLength / 2)
  return `${value.slice(0, startLength)}...${value.slice(-endLength)}`
}

function cleanupOperationSocketListener() {
  if (stopOperationSocketListener) {
    stopOperationSocketListener()
    stopOperationSocketListener = null
  }
}

async function syncOperationSocketListener() {
  cleanupOperationSocketListener()
  if (!operation.value || operation.value.state !== 'running') return

  stopOperationSocketListener = await subscribeToSocketEvents(async (payload) => {
    if (payload?.name === `operationupdate${operation.value.id}`) {
      await queueOperationRefresh()
    }
  })
}

async function loadOperation() {
  loading.value = true
  try {
    operation.value = await agentStore.getOperation(route.params.operationId)
    await syncOperationSocketListener()
  } catch (e) {
    operation.value = null
    cleanupOperationSocketListener()
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Could not load operation'), color: 'red', position: 'top' })
  } finally {
    loading.value = false
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
      await loadOperation()
    } while (operationRefreshQueued)
  } finally {
    operationRefreshInFlight = false
  }
}

watch(
  () => route.params.operationId,
  async () => {
    operation.value = null
    operationRefreshInFlight = false
    operationRefreshQueued = false
    await loadOperation()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  cleanupOperationSocketListener()
})

defineOptions({ name: 'AgentOperationDetailPage' })
</script>
