<template>
  <q-page class="q-pa-md">
    <PageHeader title="Agent Reports" description="Review operation history, status and logs.">
      <template #breadcrumbs>
        <q-breadcrumbs>
          <q-breadcrumbs-el label="Agents" icon="desktop_windows" to="/agents" />
          <q-breadcrumbs-el label="Reports" />
        </q-breadcrumbs>
      </template>
    </PageHeader>

    <q-card flat bordered class="q-mb-md">
      <q-form @submit="applyFilters">
        <q-card-section>
          <div class="row q-col-gutter-md items-center">
            <div class="col-12 col-sm-6 col-md-3">
              <q-select v-model="filterType" :options="typeOptions" label="Report type / group" emit-value map-options clearable outlined dense />
            </div>
            <div class="col-12 col-sm-6 col-md-3">
              <q-select v-model="filterState" :options="stateOptions" label="State" emit-value map-options clearable outlined dense />
            </div>
            <div class="col-12 col-sm-6 col-md-3">
              <q-select v-model="filterAgentId" :options="agentOptions" label="Agent" emit-value map-options clearable outlined dense @update:model-value="resetJobForAgent" />
            </div>
            <div class="col-12 col-sm-6 col-md-3">
              <q-select v-model="filterJobId" :options="jobOptions" :loading="jobStore.loading" label="Job" emit-value map-options clearable outlined dense />
            </div>
            <div class="col-12">
              <div class="row q-col-gutter-md items-center">
                <div class="col-12 col-sm-6 col-md-3">
                  <q-input v-model="filterSince" type="datetime-local" step="any" label="Started from (UTC)" stack-label clearable outlined dense />
                </div>
                <div class="col-12 col-sm-6 col-md-3">
                  <q-input v-model="filterUntil" type="datetime-local" step="any" label="Started before (UTC)" stack-label clearable outlined dense :rules="[value => !value || !filterSince || value > filterSince || 'End must be after start']" hide-bottom-space />
                </div>
              </div>
            </div>
            <div class="col-12">
              <div class="row items-center justify-end q-gutter-sm">
                <q-btn flat no-caps no-wrap label="Last 7 days" color="primary" class="q-mr-auto" @click="setLastSevenDays" />
                <div class="row items-center no-wrap">
                  <q-btn flat no-caps no-wrap icon="restart_alt" label="Reset" :color="$q.dark.isActive ? 'grey-5' : 'grey-7'" class="q-mr-sm" @click="clearFilters" />
                  <q-btn unelevated no-caps no-wrap icon="filter_list" label="Filter" color="primary" type="submit" :loading="loading" />
                </div>
              </div>
            </div>
          </div>
        </q-card-section>
      </q-form>
    </q-card>

    <q-card v-if="operations.length === 0 && !loading" flat bordered>
      <EmptyState icon="receipt_long" title="No operations found" description="Operations will appear here after a backup or maintenance task runs. Check your filters if you expected results." />
    </q-card>
    <q-table
      v-else
      hide-no-data
      :rows="operations"
      :columns="columns"
      :loading="loading"
      :hide-header="operations.length === 0"
      :table-header-class="$q.dark.isActive ? 'bg-dark text-grey-4' : 'bg-grey-1 text-grey-7'"
      row-key="id"
      flat bordered
      :rows-per-page-options="[25, 50, 0]"
    >
      <template v-slot:body-cell-state="props">
        <q-td :props="props">
          <q-badge :color="stateColor(props.value)" text-color="grey-10" :label="props.value" />
        </q-td>
      </template>
      <template v-slot:body-cell-actions="props">
        <q-td :props="props">
          <TableActionButton icon="visibility" label="View operation" @click="showOperation(props.row)" />
          <TableActionButton icon="delete" label="Delete operation" color="negative" @click="confirmDeleteOperation(props.row)" />
        </q-td>
      </template>
    </q-table>

  </q-page>
</template>

<script setup>
import PageHeader from 'components/PageHeader.vue'
import EmptyState from 'components/EmptyState.vue'
import TableActionButton from 'components/TableActionButton.vue'
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { useJobStore } from 'stores/job'
import { subscribeToSocketEvents } from 'src/utils/socket'
import { createQueuedReload } from 'src/utils/queued-reload'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import { getBackupStateColor as stateColor } from 'src/utils/backup-results'
import { lastSevenDays, operationFilters, operationStates } from 'src/utils/operation-results'
import { listOperations } from 'src/api/operations'

const route = useRoute()
const router = useRouter()
const $q = useQuasar()
const agentStore = useAgentStore()
const jobStore = useJobStore()

const operations = ref([])
const loading = ref(false)
const filterType = ref(null)
const filterState = ref(null)
const filterJobId = ref(null)
const filterAgentId = ref(null)
const filterSince = ref('')
const filterUntil = ref('')
const agentOptions = computed(() => agentStore.agents.map(agent => ({ label: agent.display_name, value: String(agent.id) })))
const jobOptions = computed(() => {
  const agentId = filterAgentId.value
  return jobStore.agentJobs
    .filter(agent => !agentId || String(agent.id) === String(agentId))
    .flatMap(agent => agent.jobs.map(job => ({
      label: agentId ? job.name : `${job.name} · ${agent.display_name}`,
      value: String(job.id),
    })))
})

function resetJobForAgent(agentId) {
  if (!agentId || !filterJobId.value) return
  const jobs = jobStore.agentJobs.find(agent => String(agent.id) === String(agentId))?.jobs || []
  if (!jobs.some(job => String(job.id) === filterJobId.value)) filterJobId.value = null
}

let stopJobSocketListener = null
let disposed = false
let operationsRequest = 0
let queueOperationsReload = createQueuedReload(() => loadOperations(true))

const typeOptions = [
  { label: 'Backup', value: 'backup' },
  { label: 'Repository check', value: 'repository_check' },
  { label: 'Retention', value: 'retention' },
  { label: 'Repository unlock', value: 'repository_unlock' },
  { label: 'Backup restore', value: 'restore' },
  { label: 'Agent command', value: 'command' },
  { label: 'Agent update', value: 'agent_update' },
  { label: 'Agent sync', value: 'sync' },
  { label: 'Other', value: 'other' },
]

const stateOptions = [
  ...operationStates,
  { label: 'Warnings / failures', value: 'attention' },
]

const columns = [
  { name: 'id', label: '#', field: 'id', align: 'left', sortable: true },
  { name: 'agent', label: 'Agent', field: row => row.agent_display_name || `Agent #${row.agent_id}`, align: 'left', sortable: true },
  { name: 'type_text', label: 'Type', field: 'type_text', align: 'left', sortable: true },
  { name: 'job_name', label: 'Job', field: row => row.job_name || '-', align: 'left', sortable: true },
  { name: 'repository_name', label: 'Repository', field: row => row.repository_name || '-', align: 'left', sortable: true },
  { name: 'state', label: 'State', field: 'state', align: 'left', sortable: true },
  { name: 'started', label: 'Started', field: row => formatDate(row.started), align: 'left', sortable: true },
  { name: 'ended', label: 'Ended', field: row => formatDate(row.ended), align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

function formatDate(isoStr) {
  if (!isoStr) return '-'
  return new Date(isoStr).toLocaleString()
}

function showOperation(operation) {
  router.push({
    path: `/agents/${operation.agent_id}/operations/${operation.id}`,
    query: operationFilters(route.query),
  })
}

function syncFiltersFromRoute() {
  filterType.value = route.query.group === 'other' ? 'other' : typeof route.query.type === 'string' ? route.query.type : null
  filterState.value = typeof route.query.state === 'string' ? route.query.state : null
  filterJobId.value = typeof route.query.job_id === 'string' ? route.query.job_id : null
  filterAgentId.value = typeof route.query.agent_id === 'string' ? route.query.agent_id : null
  filterSince.value = utcInput(route.query.since)
  filterUntil.value = utcInput(route.query.until)
}

function utcInput(value) {
  const date = new Date(value)
  return typeof value === 'string' && Number.isFinite(date.getTime()) ? date.toISOString().slice(0, -1) : ''
}

async function applyFilters() {
  const query = {
    ...(filterType.value === 'other' ? { group: 'other' } : filterType.value ? { type: filterType.value } : {}),
    ...(filterState.value ? { state: filterState.value } : {}),
    ...(filterAgentId.value ? { agent_id: filterAgentId.value } : {}),
    ...(filterJobId.value ? { job_id: String(filterJobId.value) } : {}),
    ...(filterSince.value ? { since: new Date(`${filterSince.value}Z`).toISOString() } : {}),
    ...(filterUntil.value ? { until: new Date(`${filterUntil.value}Z`).toISOString() } : {}),
  }
  if (JSON.stringify(operationFilters(route.query)) === JSON.stringify(query)) await loadOperations()
  else await router.push({ query })
}

function setLastSevenDays() {
  const period = lastSevenDays()
  filterSince.value = utcInput(period.since)
  filterUntil.value = utcInput(period.until)
  void applyFilters()
}

function clearFilters() {
  filterType.value = filterState.value = filterJobId.value = filterAgentId.value = null
  filterSince.value = filterUntil.value = ''
  void applyFilters()
}

async function syncJobSocketListener() {
  const stopListener = await subscribeToSocketEvents((payload) => {
    if (disposed) return
    const eventName = payload?.name || ''
    const eventAgentId = Number(payload?.data?.agent_id)

    const agentId = route.query.agent_id
    if (eventName.startsWith('operationupdate') && (!agentId || eventAgentId === Number(agentId))) {
      void queueOperationsReload(Boolean(payload.data?.state && payload.data.state !== 'running'))
      return
    }

    if (route.query.job_id && eventName === `jobstate${route.query.job_id}`) {
      void queueOperationsReload(true)
    }
  })
  if (disposed) stopListener()
  else stopJobSocketListener = stopListener
}

function confirmDeleteOperation(operation) {
  $q.dialog({
    title: 'Delete Operation',
    message: 'Delete this operation?',
    cancel: true,
  }).onOk(async () => {
    try {
      await agentStore.deleteOperation(operation.id)
      await loadOperations()
      $q.notify({ message: 'Operation deleted', color: 'green', position: 'top' })
    } catch (error) {
      if (shouldIgnoreApiError(error)) return
      $q.notify({ message: getApiErrorMessage(error), color: 'negative', position: 'top' })
    }
  })
}

async function loadOperations(background = false) {
  const request = ++operationsRequest
  if (!background) loading.value = true
  const params = operationFilters(route.query)
  try {
    const updatedOperations = await listOperations(params)
    if (disposed || request !== operationsRequest) return
    operations.value = updatedOperations
  } catch (e) {
    if (disposed || request !== operationsRequest || background) return
    operations.value = []
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Could not load operations'), color: 'red', position: 'top' })
  } finally {
    if (!disposed && request === operationsRequest) loading.value = false
  }
}

void syncJobSocketListener()

watch(
  () => JSON.stringify(operationFilters(route.query)),
  async () => {
    queueOperationsReload.cancel()
    queueOperationsReload = createQueuedReload(() => loadOperations(true))
    syncFiltersFromRoute()
    await loadOperations()
  },
  { immediate: true }
)

onMounted(async () => {
  try {
    await Promise.all([agentStore.loadAgents(), jobStore.loadJobs()])
  } catch (error) {
    if (disposed || shouldIgnoreApiError(error)) return
    $q.notify({ message: getApiErrorMessage(error, 'Could not load filter options'), color: 'negative', position: 'top' })
  }
})

onBeforeUnmount(() => {
  disposed = true
  queueOperationsReload.cancel()
  stopJobSocketListener?.()
})

defineOptions({ name: 'AgentOperationsPage' })
</script>
