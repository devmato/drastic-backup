<template>
  <q-page class="q-pa-md">
    <PageHeader title="Agent Properties" :description="agent?.display_name || `Agent #${route.params.agentId}`">
      <template #breadcrumbs>
        <q-breadcrumbs>
          <q-breadcrumbs-el label="Agents" icon="desktop_windows" to="/agents" />
          <q-breadcrumbs-el :label="agent?.display_name || `Agent #${route.params.agentId}`" />
        </q-breadcrumbs>
      </template>
      <template v-if="agent" #actions>
        <q-btn flat no-caps no-wrap color="primary" icon="description" label="Reports" :to="`/agents/${agent.id}/operations`" />
      </template>
    </PageHeader>
    <q-banner v-if="loadError" rounded class="bg-red-1 text-red-10 q-mb-lg" role="alert">
      {{ loadError }}
      <template #action><q-btn flat no-caps label="Retry" @click="loadAgent" /></template>
    </q-banner>
    <q-inner-loading :showing="loading" />
    <q-card v-if="agent" flat bordered>
      <q-card-section v-if="agent.connections?.proxmox?.guest_files_error">
        <q-banner class="bg-warning text-black" role="status">
          {{ agent.connections.proxmox.guest_files_error }}.
          Run an agent update as root to retry dependency installation.
        </q-banner>
      </q-card-section>

      <q-tabs v-model="activeTab" no-caps align="left" active-color="primary" indicator-color="primary">
        <q-tab name="info" label="Info" />
        <q-tab name="repositories" label="Repositories" />
        <q-tab name="connections" label="Connections" />
        <q-tab name="actions" label="Actions" />
      </q-tabs>

      <q-separator />

      <q-tab-panels v-model="activeTab">
        <q-tab-panel name="info">
          <q-form class="q-mb-md" @submit="saveAlias">
            <q-input v-model="alias" outlined clearable maxlength="255" label="Display name (optional)"
              hint="Leave empty to use the hostname." :disable="savingAlias" />
            <div class="row justify-end q-mt-sm">
              <q-btn unelevated no-caps color="primary" type="submit" label="Save display name"
                :loading="savingAlias" :disable="(alias || '').trim() === (agent.alias || '')" />
            </div>
          </q-form>
          <div class="row q-col-gutter-md">
            <div class="col-12 col-sm-6">
              <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Hostname</div>
              <div>{{ agent?.hostname || '-' }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Status</div>
              <q-badge :color="agent?.online ? 'positive' : 'negative'" :label="agent?.online ? 'Online' : 'Offline'" />
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">OS</div>
              <div>{{ agent?.os || '-' }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Version</div>
              <div>{{ agent?.version || '-' }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Protocol</div>
              <div>{{ agent?.protocol_version || 0 }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Install type</div>
              <div>{{ agent?.install_type || 'manual' }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Last connection</div>
              <div>{{ formatDate(agent?.last_connection) }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Created</div>
              <div>{{ formatDate(agent?.created) }}</div>
            </div>
          </div>
          <q-separator class="q-my-md" />
          <div class="text-subtitle2">SSH Identity</div>
          <div class="text-caption q-mb-sm" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
            Install this public key on SSH/SFTP repository targets used by this agent.
          </div>
          <div class="row q-col-gutter-sm q-mb-sm">
            <div class="col-12 col-sm-6">
              <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Algorithm</div>
              <div>{{ agent?.ssh_key_algorithm || '-' }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Fingerprint</div>
              <div class="db-break-word">{{ agent?.ssh_key_fingerprint || '-' }}</div>
            </div>
          </div>
          <q-input
            readonly
            outlined
            autogrow
            type="textarea"
            :model-value="agent?.ssh_public_key || ''"
            label="Public Key"
          >
            <template #append>
              <q-btn flat dense icon="content_copy" :disable="!agent?.ssh_public_key" @click="copySshPublicKey">
                <q-tooltip>Copy public key</q-tooltip>
              </q-btn>
            </template>
          </q-input>
        </q-tab-panel>

        <q-tab-panel name="repositories">
          <EmptyState v-if="agentRepositories.length === 0" icon="inventory_2" title="No repositories assigned yet" description="Repositories are assigned when they are used by a schedule or a direct backup run." />
          <q-table
            v-else
            :rows="agentRepositories"
            :columns="repositoryColumns"
            :table-header-class="$q.dark.isActive ? 'bg-dark text-grey-4' : 'bg-grey-1 text-grey-7'"
            row-key="id"
            flat
            :rows-per-page-options="[0]"
            hide-bottom
          >
            <template #body-cell-actions="props">
              <q-td :props="props">
                <TableActionButton icon="link_off" label="Remove repository assignment" color="negative" :loading="removingRepositoryId === props.row.id" @click="removeRepository(props.row)" />
              </q-td>
            </template>
          </q-table>
          <q-banner v-if="agentRepositories.length" class="q-mt-md" :class="$q.dark.isActive ? 'bg-grey-9 text-grey-4' : 'bg-grey-2 text-grey-8'">
            Repositories are assigned automatically when they are used by schedules or direct backup runs.
          </q-banner>
        </q-tab-panel>

        <q-tab-panel name="connections">
          <q-banner v-if="!supportsConnections" class="bg-warning text-black q-mb-md">
            Update this agent to protocol 3 for TrueNAS and connection management.
          </q-banner>
          <div class="row items-center q-col-gutter-md q-mb-md">
            <div class="col-12 col-sm text-body2" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
              {{ agent.online ? 'Manage the connections used by this agent’s backup jobs.' : 'Agent is offline. Showing its last reported connection status.' }}
            </div>
            <div v-if="supportsConnections" class="col-12 col-sm-auto">
              <q-btn unelevated no-caps no-wrap color="primary" icon="add" label="Add Connection" :disable="!agent.online || availableConnectionTypes.length === 0 || removingConnection !== null" @click="openConnection(null)" />
            </div>
          </div>
          <EmptyState v-if="connections.length === 0" icon="link" title="No connections configured yet" :description="supportsConnections ? 'Add a connection to configure Proxmox or TrueNAS backups.' : 'Update the agent to enable connection management.'" />
          <q-table
            v-else
            :rows="connections"
            :columns="connectionColumns"
            :table-header-class="$q.dark.isActive ? 'bg-dark text-grey-4' : 'bg-grey-1 text-grey-7'"
            hide-bottom
            :rows-per-page-options="[0]"
            row-key="value"
            flat
          >
            <template #body-cell-configured="props">
              <q-td :props="props">
                <q-badge :color="props.row.configured ? 'positive' : 'grey'" :label="props.row.configured ? 'Configured' : 'Unknown (legacy agent)'" />
              </q-td>
            </template>
            <template #body-cell-available="props">
              <q-td :props="props">
                <q-badge :color="props.row.available === true ? 'positive' : props.row.available === false ? 'orange-8' : 'grey'" :label="props.row.available === true ? 'Available' : props.row.available === false ? 'Prerequisites missing' : 'Unknown'" />
                <q-tooltip>Local agent prerequisites; this is not a connection test.</q-tooltip>
              </q-td>
            </template>
            <template #body-cell-actions="props">
              <q-td :props="props">
                <TableActionButton icon="edit" :label="props.row.configured ? `Edit ${props.row.label} connection` : `Configure ${props.row.label} connection`" :disable="!agent.online || removingConnection !== null" @click="openConnection(props.row.value)" />
                <TableActionButton v-if="supportsConnections" icon="delete" :label="`Remove ${props.row.label} connection`" color="negative" :loading="removingConnection === props.row.value" :disable="!agent.online || removingConnection !== null" @click="confirmRemoveConnection(props.row)" />
              </q-td>
            </template>
          </q-table>
        </q-tab-panel>

        <q-tab-panel name="actions">
          <q-list>
            <q-item v-for="action in agentActions" :key="action.name" clickable v-ripple
              role="button" :aria-label="action.title" :aria-busy="Boolean(pendingActions[action.name])"
              :disable="!agent?.online || Boolean(pendingActions[action.name])"
              class="rounded-borders" @click="action.run">
              <q-item-section avatar>
                <q-icon :name="action.icon" :color="action.color" />
              </q-item-section>
              <q-item-section>
                <q-item-label class="text-weight-medium">{{ action.title }}</q-item-label>
                <q-item-label caption>{{ action.description }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-spinner v-if="pendingActions[action.name]" :color="action.color" size="sm" />
                <q-icon v-else name="chevron_right" size="sm" />
              </q-item-section>
            </q-item>
          </q-list>
        </q-tab-panel>
      </q-tab-panels>

    </q-card>
    <AgentConnectionDialog
      v-if="connectionDialogOpen && agent"
      :key="agent.id"
      v-model="connectionDialogOpen"
      :agent="agent"
      :kind="editingConnection"
      @updated="loadAgent"
    />
  </q-page>
</template>

<script setup>
import PageHeader from 'components/PageHeader.vue'
import TableActionButton from 'components/TableActionButton.vue'
import EmptyState from 'components/EmptyState.vue'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { copyToClipboard, useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import AgentConnectionDialog from 'components/agents/AgentConnectionDialog.vue'
import { getAgentConnections, getAvailableConnectionTypes } from 'src/utils/agent-connections'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const $q = useQuasar()
const agentStore = useAgentStore()
const route = useRoute()
const router = useRouter()
const agent = computed(() => agentStore.agents.find(item => String(item.id) === String(route.params.agentId)))
const alias = ref('')
const savingAlias = ref(false)
watch([() => agent.value?.id, () => agent.value?.alias], () => {
  alias.value = agent.value?.alias || ''
}, { immediate: true })
const loading = ref(false)
const loadError = ref('')
const connectionDialogOpen = ref(false)
const editingConnection = ref(null)
const removingConnection = ref(null)
const connections = computed(() => getAgentConnections(agent.value))
const availableConnectionTypes = computed(() => getAvailableConnectionTypes(agent.value))
const connectionColumns = [
  { name: 'type', label: 'Type', field: 'label', align: 'left' },
  { name: 'configured', label: 'Configuration', field: 'configured', align: 'left' },
  { name: 'available', label: 'Availability', field: 'available', align: 'left' },
  { name: 'actions', label: '', field: 'value', align: 'right' },
]

const activeTab = computed({
  get: () => ['info', 'repositories', 'connections', 'actions'].includes(route.query.tab) ? route.query.tab : 'info',
  set: tab => router.replace({ query: { ...route.query, tab } }),
})
const removingRepositoryId = ref(null)
const pendingActions = ref({})
const supportsConfiguration = computed(() => (agent.value?.protocol_version || 0) >= 1)
const supportsConnections = computed(() => (agent.value?.protocol_version || 0) >= 3)
const supportsUpdate = computed(() => supportsConfiguration.value
  && agent.value?.os?.toLowerCase() === 'linux'
  && (agent.value?.install_type === 'git'
    || (agent.value?.install_type === 'docker' && (agent.value?.protocol_version || 0) >= 4)))

const agentActions = computed(() => [
  ...(supportsUpdate.value ? [{
    name: 'update', title: 'Update Agent', icon: 'system_update', color: 'primary',
    description: 'Update from the saved Git repository and ref; tags and commits stay pinned.',
    run: () => runAction('update', 'Agent update started', 'Could not start agent update'),
  }] : []),
  {
    name: 'reset-known-hosts', title: 'Reset SSH host keys', icon: 'restart_alt', color: 'warning',
    description: "Clears this agent's known_hosts cache. The next SSH connection will trust hosts again on first use.",
    run: confirmResetKnownHosts,
  },
  {
    name: 'rotate-ssh-key', title: 'Rotate SSH key', icon: 'vpn_key', color: 'negative',
    description: 'Generates a new SSH identity on this agent. Install the new public key on all SSH/SFTP targets before running jobs again.',
    run: confirmRotateSshKey,
  },
])

async function loadAgent() {
  loading.value = true
  loadError.value = ''
  try {
    await agentStore.loadAgents()
    if (!agent.value) loadError.value = 'Agent not found'
  } catch (error) {
    if (!shouldIgnoreApiError(error)) loadError.value = getApiErrorMessage(error)
  } finally {
    loading.value = false
  }
}

async function saveAlias() {
  if (!agent.value || savingAlias.value) return
  savingAlias.value = true
  try {
    await agentStore.updateAgent(agent.value.id, alias.value)
    $q.notify({ message: 'Display name saved', color: 'positive' })
  } catch (error) {
    if (!shouldIgnoreApiError(error)) $q.notify({ message: getApiErrorMessage(error), color: 'negative' })
  } finally {
    savingAlias.value = false
  }
}

function openConnection(kind) {
  if (!agent.value?.online || removingConnection.value !== null) return
  editingConnection.value = kind
  connectionDialogOpen.value = true
}

function confirmRemoveConnection(connection) {
  if (!agent.value?.online || !supportsConnections.value || removingConnection.value !== null) return
  const agentId = agent.value.id
  $q.dialog({
    title: 'Remove Connection',
    message: `Remove the ${connection.label} connection from ${agent.value.display_name}? Jobs using it cannot run until it is configured again.`,
    cancel: { label: 'Cancel', flat: true, noCaps: true },
    persistent: true,
    ok: { label: 'Remove', color: 'negative', noCaps: true },
  }).onOk(() => removeConnection(agentId, connection.value))
}

async function removeConnection(agentId, kind) {
  if (agent.value?.id !== agentId || !agent.value.online || removingConnection.value !== null) return
  removingConnection.value = kind
  try {
    await agentStore.deleteConnection(agentId, kind)
    $q.notify({ message: 'Connection removed', color: 'positive' })
  } catch (error) {
    if (!shouldIgnoreApiError(error)) $q.notify({ message: getApiErrorMessage(error), color: 'negative' })
  } finally {
    removingConnection.value = null
  }
}

watch(() => route.params.agentId, loadAgent, { immediate: true })
watch(() => route.params.agentId, () => { connectionDialogOpen.value = false })

const agentRepositories = computed(() => agent.value?.repositories || [])

const repositoryColumns = [
  { name: 'name', label: 'Name', field: 'name', align: 'left' },
  { name: 'location', label: 'Location', field: 'location', align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : '-'
}

async function copySshPublicKey() {
  if (!agent.value?.ssh_public_key) return
  try {
    await copyToClipboard(agent.value.ssh_public_key)
    $q.notify({ message: 'SSH public key copied', color: 'green', position: 'top' })
  } catch {
    $q.notify({ message: 'Could not copy SSH public key', color: 'negative', position: 'top' })
  }
}

async function removeRepository(repository) {
  if (!agent.value) return

  removingRepositoryId.value = repository.id
  try {
    const repositoryIds = agentRepositories.value
      .filter(agentRepository => agentRepository.id !== repository.id)
      .map(agentRepository => agentRepository.id)
    await agentStore.updateAgentRepositories(agent.value.id, repositoryIds)
    $q.notify({ message: 'Repository assignment removed', color: 'green', position: 'top' })
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  } finally {
    removingRepositoryId.value = null
  }
}

async function runAction(action, message, failure) {
  if (!agent.value?.online || pendingActions.value[action]) return
  pendingActions.value[action] = true
  try {
    await agentStore.runAction(agent.value.id, action)
    $q.notify({ message, color: 'green', position: 'top' })
  } catch (error) {
    if (shouldIgnoreApiError(error)) return
    $q.notify({ message: getApiErrorMessage(error, failure), color: 'negative', position: 'top' })
  } finally {
    pendingActions.value[action] = false
  }
}

function confirmResetKnownHosts() {
  if (!agent.value) return
  $q.dialog({
    title: 'Reset SSH known_hosts',
    message: `Reset SSH known_hosts on ${agent.value.display_name}? SSH hosts will be trusted again on first use.`,
    cancel: true,
    persistent: true,
  }).onOk(() => runAction('reset-known-hosts', 'SSH known_hosts reset', 'Could not reset SSH known_hosts'))
}

function confirmRotateSshKey() {
  if (!agent.value) return
  $q.dialog({
    title: 'Rotate Agent SSH key',
    message: `Rotate the SSH key on ${agent.value.display_name}? Existing SSH/SFTP targets must be updated with the new public key.`,
    cancel: true,
    persistent: true,
  }).onOk(() => runAction('rotate-ssh-key', 'Agent SSH key rotated', 'Could not rotate SSH key'))
}

defineOptions({ name: 'AgentDetailPage' })
</script>
