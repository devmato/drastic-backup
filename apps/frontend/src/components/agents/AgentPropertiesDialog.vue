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
      </q-tabs>

      <q-separator />

      <q-tab-panels v-model="activeTab" animated>
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
              <div class="text-caption text-grey-7">Last connection</div>
              <div>{{ formatDate(agent?.last_connection) }}</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption text-grey-7">Created</div>
              <div>{{ formatDate(agent?.created) }}</div>
            </div>
          </div>
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
      </q-tab-panels>

      <q-card-actions align="right">
        <q-btn flat label="Close" v-close-popup />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  agent: { type: Object, default: null },
})

const emit = defineEmits(['update:modelValue', 'updated'])

const $q = useQuasar()
const agentStore = useAgentStore()

const activeTab = ref('info')
const removingRepositoryId = ref(null)

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

defineOptions({ name: 'AgentPropertiesDialog' })
</script>
