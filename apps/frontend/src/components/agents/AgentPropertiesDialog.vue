<template>
  <q-dialog v-model="dialogVisible" :maximized="$q.screen.lt.md">
    <q-card class="db-dialog-card-md">
      <q-card-section>
        <div class="text-h6">Agent Properties</div>
        <div class="text-caption text-grey-7">{{ agent?.hostname || `Agent #${agent?.id}` }}</div>
      </q-card-section>

      <q-tabs v-model="activeTab" dense align="left" class="text-primary">
        <q-tab name="info" label="Info" />
        <q-tab name="repositories" label="Repositories" />
        <q-tab name="configuration" label="Configuration" />
        <q-tab name="actions" label="Actions" />
      </q-tabs>

      <q-separator />

      <q-tab-panels v-model="activeTab">
        <q-tab-panel name="info" class="q-gutter-sm">
          <div class="row q-col-gutter-sm">
            <div class="col-12 col-sm-6">
              <div class="text-caption text-grey-7">Hostname</div>
              <div>{{ agent?.hostname || '-' }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption text-grey-7">Status</div>
              <q-badge :color="agent?.online ? 'green' : 'red'" :label="agent?.online ? 'Online' : 'Offline'" />
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption text-grey-7">OS</div>
              <div>{{ agent?.os || '-' }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption text-grey-7">Version</div>
              <div>{{ agent?.version || '-' }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption text-grey-7">Install type</div>
              <div>{{ agent?.install_type || 'manual' }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption text-grey-7">Last connection</div>
              <div>{{ formatDate(agent?.last_connection) }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption text-grey-7">Created</div>
              <div>{{ formatDate(agent?.created) }}</div>
            </div>
          </div>
          <q-separator class="q-my-md" />
          <div class="text-subtitle2">SSH Identity</div>
          <div class="text-caption text-grey-7 q-mb-sm">
            Install this public key on SSH/SFTP repository targets used by this agent.
          </div>
          <div class="row q-col-gutter-sm q-mb-sm">
            <div class="col-12 col-sm-6">
              <div class="text-caption text-grey-7">Algorithm</div>
              <div>{{ agent?.ssh_key_algorithm || '-' }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption text-grey-7">Fingerprint</div>
              <div class="text-break">{{ agent?.ssh_key_fingerprint || '-' }}</div>
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
          <q-table
            :rows="agentRepositories"
            :columns="repositoryColumns"
            row-key="id"
            flat
            dense
            :rows-per-page-options="[0]"
            hide-bottom
            no-data-label="No repositories assigned"
          >
            <template #body-cell-actions="props">
              <q-td :props="props">
                <q-btn flat dense icon="link_off" color="red" :loading="removingRepositoryId === props.row.id" @click="removeRepository(props.row)">
                  <q-tooltip>Remove repository assignment</q-tooltip>
                </q-btn>
              </q-td>
            </template>
          </q-table>
          <q-banner class="bg-grey-2 text-grey-8 q-mt-md">
            Repositories are assigned automatically when they are used by schedules or direct backup runs.
          </q-banner>
        </q-tab-panel>

        <q-tab-panel name="configuration">
          <AgentProxmoxSettings
            v-if="agent"
            :key="agent.id"
            :agent-id="agent.id"
            :agent-online="agent.online"
            @saved="emit('proxmox-updated')"
          />
        </q-tab-panel>

        <q-tab-panel name="actions" class="q-gutter-md">
          <q-card flat bordered>
            <q-card-section>
              <div class="text-subtitle2">Reset SSH known_hosts</div>
              <div class="text-caption text-grey-7 q-mt-xs">
                Clears this agent's local SSH host key cache. The next SSH connection will trust hosts again on first use.
              </div>
            </q-card-section>
            <q-card-actions align="right">
              <q-btn
                color="warning"
                icon="restart_alt"
                label="Reset known_hosts"
                :disable="!agent?.online"
                :loading="resettingKnownHosts"
                @click="confirmResetKnownHosts"
              />
            </q-card-actions>
          </q-card>

          <q-card flat bordered>
            <q-card-section>
              <div class="text-subtitle2">Rotate Agent SSH key</div>
              <div class="text-caption text-grey-7 q-mt-xs">
                Generates a new SSH identity on this agent. Install the new public key on all SSH/SFTP targets before running jobs again.
              </div>
            </q-card-section>
            <q-card-actions align="right">
              <q-btn
                color="negative"
                icon="vpn_key"
                label="Rotate SSH key"
                :disable="!agent?.online"
                :loading="rotatingSshKey"
                @click="confirmRotateSshKey"
              />
            </q-card-actions>
          </q-card>
        </q-tab-panel>
      </q-tab-panels>

      <q-card-actions align="right">
        <q-btn flat label="Close" v-close-popup />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { copyToClipboard, useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import AgentProxmoxSettings from 'components/agents/AgentProxmoxSettings.vue'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  agent: { type: Object, default: null },
  initialTab: { type: String, default: 'info' },
})

const emit = defineEmits(['update:modelValue', 'updated', 'proxmox-updated'])

const $q = useQuasar()
const agentStore = useAgentStore()

const activeTab = ref(props.initialTab)
const removingRepositoryId = ref(null)
const resettingKnownHosts = ref(false)
const rotatingSshKey = ref(false)

watch(() => props.modelValue, visible => {
  if (visible) activeTab.value = props.initialTab
})

const dialogVisible = computed({
  get: () => props.modelValue,
  set: value => emit('update:modelValue', value),
})

const agentRepositories = computed(() => props.agent?.repositories || [])

const repositoryColumns = [
  { name: 'name', label: 'Name', field: 'name', align: 'left' },
  { name: 'location', label: 'Location', field: 'location', align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : '-'
}

async function copySshPublicKey() {
  if (!props.agent?.ssh_public_key) return
  try {
    await copyToClipboard(props.agent.ssh_public_key)
    $q.notify({ message: 'SSH public key copied', color: 'green', position: 'top' })
  } catch {
    $q.notify({ message: 'Could not copy SSH public key', color: 'negative', position: 'top' })
  }
}

async function removeRepository(repository) {
  if (!props.agent) return

  removingRepositoryId.value = repository.id
  try {
    const repositoryIds = agentRepositories.value
      .filter(agentRepository => agentRepository.id !== repository.id)
      .map(agentRepository => agentRepository.id)
    await agentStore.updateAgentRepositories(props.agent.id, repositoryIds)
    emit('updated')
    $q.notify({ message: 'Repository assignment removed', color: 'green', position: 'top' })
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  } finally {
    removingRepositoryId.value = null
  }
}

function confirmResetKnownHosts() {
  if (!props.agent) return
  $q.dialog({
    title: 'Reset SSH known_hosts',
    message: `Reset SSH known_hosts on ${props.agent.hostname || `Agent #${props.agent.id}`}? SSH hosts will be trusted again on first use.`,
    cancel: true,
    persistent: true,
  }).onOk(resetKnownHosts)
}

async function resetKnownHosts() {
  if (!props.agent) return
  resettingKnownHosts.value = true
  try {
    await agentStore.resetKnownHosts(props.agent.id)
    $q.notify({ message: 'SSH known_hosts reset', color: 'green', position: 'top' })
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Could not reset SSH known_hosts'), color: 'red', position: 'top' })
  } finally {
    resettingKnownHosts.value = false
  }
}

function confirmRotateSshKey() {
  if (!props.agent) return
  $q.dialog({
    title: 'Rotate Agent SSH key',
    message: `Rotate the SSH key on ${props.agent.hostname || `Agent #${props.agent.id}`}? Existing SSH/SFTP targets must be updated with the new public key.`,
    cancel: true,
    persistent: true,
  }).onOk(rotateSshKey)
}

async function rotateSshKey() {
  if (!props.agent) return
  rotatingSshKey.value = true
  try {
    await agentStore.rotateSshKey(props.agent.id)
    emit('updated')
    $q.notify({ message: 'Agent SSH key rotated', color: 'green', position: 'top' })
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Could not rotate SSH key'), color: 'red', position: 'top' })
  } finally {
    rotatingSshKey.value = false
  }
}

defineOptions({ name: 'AgentPropertiesDialog' })
</script>
