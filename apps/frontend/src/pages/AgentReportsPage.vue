<template>
  <q-page class="q-pa-md">
    <q-breadcrumbs class="q-pb-md">
      <q-breadcrumbs-el label="Agents" icon="desktop_windows" to="/agents" />
      <q-breadcrumbs-el :label="`Agent #${route.params.agentId}`" />
      <q-breadcrumbs-el label="Operations" />
    </q-breadcrumbs>

    <div class="q-mb-md">
      <div class="text-h5">Operations for Agent #{{ route.params.agentId }}</div>
    </div>

    <div class="row q-col-gutter-sm q-mb-md items-end">
      <div class="col-12 col-sm-6 col-md-3">
        <q-select v-model="filterType" :options="typeOptions" label="Operation type" emit-value map-options clearable outlined dense />
      </div>
      <div class="col-12 col-sm-6 col-md-3">
        <q-select v-model="filterState" :options="stateOptions" label="State" emit-value map-options clearable outlined dense />
      </div>
      <div class="col-12 col-md-auto">
        <q-btn label="Filter" color="primary" @click="loadOperations" />
      </div>
    </div>

    <q-table
      :rows="operations"
      :columns="columns"
      :loading="loading"
      row-key="id"
      flat bordered
      :rows-per-page-options="[25, 50, 0]"
      no-data-label="No operations found"
    >
      <template v-slot:body-cell-state="props">
        <q-td :props="props">
          <q-badge :color="stateColor(props.value)" :label="props.value" />
        </q-td>
      </template>
      <template v-slot:body-cell-actions="props">
        <q-td :props="props">
          <q-btn flat dense icon="visibility" color="blue" @click="showOperation(props.row)">
            <q-tooltip>View operation</q-tooltip>
          </q-btn>
          <q-btn flat dense icon="delete" color="red" @click="confirmDeleteOperation(props.row)">
            <q-tooltip>Delete operation</q-tooltip>
          </q-btn>
        </q-td>
      </template>
    </q-table>

  </q-page>
</template>

<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { subscribeToSocketEvents } from 'src/utils/socket'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

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

function stateColor(state) {
  const colors = { running: 'blue', success: 'green', warning: 'orange', failed: 'red', cancelled: 'grey' }
  return colors[state] || 'grey'
}

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

function cleanupJobSocketListener() {
  if (stopJobSocketListener) {
    stopJobSocketListener()
    stopJobSocketListener = null
  }
}

async function syncJobSocketListener() {
  cleanupJobSocketListener()
  stopJobSocketListener = await subscribeToSocketEvents(async (payload) => {
    const eventName = payload?.name || ''
    const eventAgentId = Number(payload?.data?.agent_id)

    if (eventName.startsWith('operationupdate') && eventAgentId === Number(route.params.agentId)) {
      await loadOperations()
      return
    }

    if (filterJobId.value && eventName === `jobstate${filterJobId.value}`) {
      await loadOperations()
    }
  })
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

async function loadOperations() {
  loading.value = true
  const params = {}
  if (filterType.value) params.type = filterType.value
  if (filterState.value) params.state = filterState.value
  if (filterJobId.value) params.job_id = filterJobId.value
  try {
    operations.value = await agentStore.getAgentOperations(route.params.agentId, params)
  } catch (e) {
    operations.value = []
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Could not load operations'), color: 'red', position: 'top' })
  } finally {
    loading.value = false
  }
}

watch(
  () => [route.params.agentId, route.query.type, route.query.state, route.query.job_id],
  async () => {
    syncFiltersFromRoute()
    await loadOperations()
    await syncJobSocketListener()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  cleanupJobSocketListener()
})

defineOptions({ name: 'AgentOperationsPage' })
</script>
