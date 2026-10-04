<template>
  <q-page class="q-pa-md">
    <PageHeader :title="`Operations for Agent #${route.params.agentId}`" description="Review operation history, status and logs.">
      <template #breadcrumbs>
        <q-breadcrumbs>
          <q-breadcrumbs-el label="Agents" icon="desktop_windows" to="/agents" />
          <q-breadcrumbs-el :label="`Agent #${route.params.agentId}`" :to="`/agents/${route.params.agentId}`" />
          <q-breadcrumbs-el label="Operations" />
        </q-breadcrumbs>
      </template>
    </PageHeader>

    <q-card flat bordered class="q-mb-md">
      <q-form @submit="loadOperations()">
        <q-card-section>
          <div class="row q-col-gutter-md items-center">
            <div class="col-12 col-sm-6 col-md-3">
              <q-select v-model="filterType" :options="typeOptions" label="Operation type" emit-value map-options clearable outlined dense />
            </div>
            <div class="col-12 col-sm-6 col-md-3">
              <q-select v-model="filterState" :options="stateOptions" label="State" emit-value map-options clearable outlined dense />
            </div>
            <div class="col-12 col-md-auto">
              <q-btn unelevated no-caps no-wrap icon="filter_list" label="Filter" color="primary" type="submit" :loading="loading" />
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
import { ref, watch, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { subscribeToSocketEvents } from 'src/utils/socket'
import { createQueuedReload } from 'src/utils/queued-reload'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import { getBackupStateColor as stateColor } from 'src/utils/backup-results'

const route = useRoute()
const router = useRouter()
const $q = useQuasar()
const agentStore = useAgentStore()

const operations = ref([])
const loading = ref(false)
const filterType = ref(null)
const filterState = ref(null)
const filterJobId = ref(null)

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
  { label: 'Repository stats', value: 'repository_stats' },
  { label: 'Agent command', value: 'command' },
]

const stateOptions = [
  { label: 'Running', value: 'running' },
  { label: 'Success', value: 'success' },
  { label: 'Warning', value: 'warning' },
  { label: 'Failed', value: 'failed' },
  { label: 'Cancelled', value: 'cancelled' },
]

const columns = [
  { name: 'id', label: '#', field: 'id', align: 'left', sortable: true },
  { name: 'type_text', label: 'Type', field: 'type_text', align: 'left', sortable: true },
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
    path: `/agents/${route.params.agentId}/operations/${operation.id}`,
    query: route.query,
  })
}

function syncFiltersFromRoute() {
  filterType.value = typeof route.query.type === 'string' ? route.query.type : null
  filterState.value = typeof route.query.state === 'string' ? route.query.state : null
  filterJobId.value = route.query.job_id ? Number(route.query.job_id) : null
}

async function syncJobSocketListener() {
  const stopListener = await subscribeToSocketEvents((payload) => {
    if (disposed) return
    const eventName = payload?.name || ''
    const eventAgentId = Number(payload?.data?.agent_id)

    if (eventName.startsWith('operationupdate') && eventAgentId === Number(route.params.agentId)) {
      void queueOperationsReload(Boolean(payload.data?.state && payload.data.state !== 'running'))
      return
    }

    if (filterJobId.value && eventName === `jobstate${filterJobId.value}`) {
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
    await agentStore.deleteOperation(operation.id)
    await loadOperations()
    $q.notify({ message: 'Operation deleted', color: 'green', position: 'top' })
  })
}

async function loadOperations(background = false) {
  const request = ++operationsRequest
  const agentId = String(route.params.agentId)
  if (!background) loading.value = true
  const params = {}
  if (filterType.value) params.type = filterType.value
  if (filterState.value) params.state = filterState.value
  if (filterJobId.value) params.job_id = filterJobId.value
  try {
    const updatedOperations = await agentStore.getAgentOperations(agentId, params)
    if (disposed || request !== operationsRequest || String(route.params.agentId) !== agentId) return
    operations.value = updatedOperations
  } catch (e) {
    if (disposed || request !== operationsRequest || String(route.params.agentId) !== agentId || background) return
    operations.value = []
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Could not load operations'), color: 'red', position: 'top' })
  } finally {
    if (!disposed && request === operationsRequest) loading.value = false
  }
}

void syncJobSocketListener()

watch(
  () => [route.params.agentId, route.query.type, route.query.state, route.query.job_id],
  async () => {
    queueOperationsReload.cancel()
    queueOperationsReload = createQueuedReload(() => loadOperations(true))
    syncFiltersFromRoute()
    await loadOperations()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  disposed = true
  queueOperationsReload.cancel()
  stopJobSocketListener?.()
})

defineOptions({ name: 'AgentOperationsPage' })
</script>
