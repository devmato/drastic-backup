<template>
  <q-page class="q-pa-md">
    <PageHeader title="Agents" description="Manage connected systems and their backup configuration.">
      <template #actions>
        <q-btn v-if="updateCandidates.length || pendingUpdates.length" outline no-caps no-wrap color="primary" icon="system_update"
          :label="`Update agents (${updateCandidates.length})`" :loading="pendingUpdates.length > 0"
          :disable="agentStore.loading" @click="startAgentUpdates()">
          <q-tooltip>Update online agents whose version differs from the backend, using their saved Git repository and ref. Tags and commits stay pinned.</q-tooltip>
        </q-btn>
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
      <template #body-cell-display_name="props">
        <q-td :props="props">
          <router-link :to="`/agents/${props.row.id}`" class="text-weight-medium text-primary">{{ props.row.display_name }}</router-link>
        </q-td>
      </template>
      <template #body-cell-version="props">
        <q-td :props="props">
          {{ props.row.version || '-' }}
          <template v-if="hasVersionMismatch(props.row)">
            <TableActionButton v-if="props.row.online && supportsAgentUpdate(props.row)" icon="system_update" size="sm"
              :loading="pendingUpdates.includes(props.row.id)"
              :label="`Update agent using its saved Git ref. Agent: ${props.row.version}. Backend: ${props.row.backend_version}.`"
              @click="startAgentUpdates([props.row.id])" />
            <TableActionButton v-else icon="info_outline" size="sm"
              :color="$q.dark.isActive ? 'grey-5' : 'grey-7'" :to="`/agents/${props.row.id}`"
              :label="`${props.row.online ? 'Web updates unavailable' : 'Agent offline'}. Agent: ${props.row.version}. Backend: ${props.row.backend_version}.`" />
          </template>
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
import { computed, onMounted, ref } from 'vue'
import PageHeader from 'components/PageHeader.vue'
import EmptyState from 'components/EmptyState.vue'
import TableActionButton from 'components/TableActionButton.vue'
import { useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { useUserStore } from 'stores/user'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import { hasVersionMismatch, supportsAgentUpdate } from 'src/utils/agent-updates'

const $q = useQuasar()
const agentStore = useAgentStore()
const userStore = useUserStore()
const pendingUpdates = ref([])
const updateCandidates = computed(() => agentStore.agents.filter(agent => agent.online
  && supportsAgentUpdate(agent) && hasVersionMismatch(agent)))

async function startAgentUpdates(agentIds = updateCandidates.value.map(agent => agent.id)) {
  const selected = updateCandidates.value.filter(agent => agentIds.includes(agent.id) && !pendingUpdates.value.includes(agent.id))
  if (!selected.length) return
  pendingUpdates.value.push(...selected.map(agent => agent.id))
  const results = await Promise.allSettled(selected.map(async agent => {
    try {
      return await agentStore.runAction(agent.id, 'update')
    } finally {
      pendingUpdates.value = pendingUpdates.value.filter(id => id !== agent.id)
    }
  }))
  const started = results.filter(result => result.status === 'fulfilled').length
  if (started) $q.notify({ message: started === 1 ? 'Agent update started' : `Updates started for ${started} agents`, color: 'positive', position: 'top' })
  results.forEach((result, index) => {
    if (result.status !== 'rejected' || shouldIgnoreApiError(result.reason)) return
    const error = result.reason
    const unconfirmed = !error.response || error.response.status >= 500
    const message = getApiErrorMessage(error, unconfirmed
      ? 'Update start not confirmed; check the agent logs before retrying' : 'Could not start agent update')
    $q.notify({ message: `${selected[index].display_name}: ${message}`, color: unconfirmed ? 'warning' : 'negative', position: 'top' })
  })
}


const columns = [
  { name: 'id', label: 'ID', field: 'id', align: 'left', sortable: true },
  { name: 'display_name', label: 'Name', field: 'display_name', align: 'left', sortable: true },
  { name: 'os', label: 'OS', field: 'os', align: 'left' },
  { name: 'version', label: 'Version', field: 'version', align: 'left' },
  { name: 'status', label: 'Status', field: 'online', align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

function confirmDelete(agent) {
  $q.dialog({
    title: 'Delete Agent',
    message: `Really delete agent ${agent.display_name}? Warning: This will delete all assigned jobs!`,
    cancel: true,
    persistent: true,
  }).onOk(async () => {
    try {
      await agentStore.deleteAgent(agent.id)
      $q.notify({ message: 'Agent deleted', color: 'green', position: 'top' })
    } catch (error) {
      if (shouldIgnoreApiError(error)) return
      $q.notify({ message: getApiErrorMessage(error), color: 'negative', position: 'top' })
    }
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
