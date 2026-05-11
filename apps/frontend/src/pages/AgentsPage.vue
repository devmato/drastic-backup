<template>
  <q-page class="q-pa-md">
    <div class="row items-center q-col-gutter-md q-row-gutter-sm q-mb-md">
      <div class="col">
        <div class="text-h5">Agents</div>
      </div>
      <div class="col-auto">
        <q-btn color="primary" icon="add" label="Add Agent" @click="router.push('/agents/install')" />
      </div>
    </div>

    <q-table
      :rows="agentStore.agents"
      :columns="columns"
      :loading="agentStore.loading"
      row-key="id"
      flat
      bordered
      :rows-per-page-options="[0]"
      no-data-label="No agents registered"
    >
      <template #body-cell-status="props">
        <q-td :props="props">
          <q-badge :color="props.row.online ? 'green' : 'red'" :label="props.row.online ? 'Online' : 'Offline'" />
        </q-td>
      </template>
      <template #body-cell-actions="props">
        <q-td :props="props">
          <q-btn flat dense icon="settings" color="primary" @click="showAgentProperties(props.row)">
            <q-tooltip>Agent properties</q-tooltip>
          </q-btn>
          <q-btn flat dense icon="sync" color="secondary" :disable="!props.row.online" @click="syncAgent(props.row)">
            <q-tooltip>Synchronize agent</q-tooltip>
          </q-btn>
          <q-btn flat dense icon="description" color="blue" @click="router.push(`/agents/${props.row.id}/operations`)" >
            <q-tooltip>Show reports</q-tooltip>
          </q-btn>
          <q-btn flat dense icon="delete" color="red" @click="confirmDelete(props.row)">
            <q-tooltip>Delete agent</q-tooltip>
          </q-btn>
        </q-td>
      </template>
    </q-table>

    <AgentPropertiesDialog
      v-model="showPropertiesDialog"
      :agent="selectedAgent"
      @updated="refreshSelectedAgent"
    />

  </q-page>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { useUserStore } from 'stores/user'
import AgentPropertiesDialog from 'components/agents/AgentPropertiesDialog.vue'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const router = useRouter()
const $q = useQuasar()
const agentStore = useAgentStore()
const userStore = useUserStore()

const showPropertiesDialog = ref(false)
const selectedAgent = ref(null)

const columns = [
  { name: 'id', label: 'ID', field: 'id', align: 'left', sortable: true },
  { name: 'hostname', label: 'Name', field: 'hostname', align: 'left', sortable: true },
  { name: 'os', label: 'OS', field: 'os', align: 'left' },
  { name: 'version', label: 'Version', field: 'version', align: 'left' },
  { name: 'status', label: 'Status', field: 'online', align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

function showAgentProperties(agent) {
  selectedAgent.value = agent
  showPropertiesDialog.value = true
}

async function refreshSelectedAgent() {
  await agentStore.loadAgents()
  selectedAgent.value = agentStore.agents.find(agent => agent.id === selectedAgent.value?.id) || selectedAgent.value
}

function confirmDelete(agent) {
  $q.dialog({
    title: 'Delete Agent',
    message: `Really delete agent ${agent.hostname}? Warning: This will delete all assigned jobs!`,
    cancel: true,
    persistent: true,
  }).onOk(async () => {
    await agentStore.deleteAgent(agent.id)
    $q.notify({ message: 'Agent deleted', color: 'green', position: 'top' })
  })
}

async function syncAgent(agent) {
  try {
    await userStore.withRecoveryKey(recoveryKey => agentStore.syncAgent(agent.id, recoveryKey))
    $q.notify({ message: 'Agent synchronized', color: 'green', position: 'top' })
  } catch (error) {
    if (shouldIgnoreApiError(error)) return
    $q.notify({ message: getApiErrorMessage(error, 'Agent sync failed'), color: 'negative', position: 'top' })
  }
}

onMounted(() => {
  agentStore.loadAgents()
})

defineOptions({ name: 'AgentsPage' })
</script>
