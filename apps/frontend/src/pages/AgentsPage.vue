<template>
  <q-page class="q-pa-md">
    <PageHeader title="Agents" description="Manage connected systems and their backup configuration.">
      <template #actions>
        <q-btn unelevated no-caps no-wrap color="primary" icon="add" label="Add Agent" to="/agents/install" />
      </template>
    </PageHeader>

    <q-card v-if="agentStore.agents.length === 0 && !agentStore.loading" flat bordered>
      <EmptyState icon="desktop_windows" title="No agents connected yet" description="Add an agent to connect a system and start configuring backups." />
    </q-card>
    <q-table
      v-else
      hide-no-data
      :rows="agentStore.agents"
      :columns="columns"
      :loading="agentStore.loading"
      :hide-header="agentStore.agents.length === 0"
      :table-header-class="$q.dark.isActive ? 'bg-dark text-grey-4' : 'bg-grey-1 text-grey-7'"
      row-key="id"
      flat
      bordered
      :rows-per-page-options="[0]"
    >
      <template #body-cell-hostname="props">
        <q-td :props="props">
          <router-link :to="`/agents/${props.row.id}`" class="text-weight-medium text-primary">{{ props.row.hostname || `Agent #${props.row.id}` }}</router-link>
        </q-td>
      </template>
      <template #body-cell-status="props">
        <q-td :props="props">
          <q-badge :color="props.row.online ? 'positive' : 'negative'" :label="props.row.online ? 'Online' : 'Offline'" />
        </q-td>
      </template>
      <template #body-cell-actions="props">
        <q-td :props="props">
          <TableActionButton icon="settings" label="Agent properties" :to="`/agents/${props.row.id}`" />
          <TableActionButton icon="sync" label="Synchronize agent" :disable="!props.row.online" @click="syncAgent(props.row)" />
          <TableActionButton icon="description" label="Show reports" :to="`/agents/${props.row.id}/operations`" />
          <TableActionButton icon="delete" label="Delete agent" color="negative" @click="confirmDelete(props.row)" />
        </q-td>
      </template>
    </q-table>


  </q-page>
</template>

<script setup>
import { onMounted } from 'vue'
import PageHeader from 'components/PageHeader.vue'
import EmptyState from 'components/EmptyState.vue'
import TableActionButton from 'components/TableActionButton.vue'
import { useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { useUserStore } from 'stores/user'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const $q = useQuasar()
const agentStore = useAgentStore()
const userStore = useUserStore()


const columns = [
  { name: 'id', label: 'ID', field: 'id', align: 'left', sortable: true },
  { name: 'hostname', label: 'Name', field: 'hostname', align: 'left', sortable: true },
  { name: 'os', label: 'OS', field: 'os', align: 'left' },
  { name: 'version', label: 'Version', field: 'version', align: 'left' },
  { name: 'status', label: 'Status', field: 'online', align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

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
