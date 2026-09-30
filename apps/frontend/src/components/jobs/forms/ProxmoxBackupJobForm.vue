<template>
  <div class="q-mt-md">
    <div class="row items-center q-mb-sm">
      <div class="text-subtitle1">Guest Selection</div>
    </div>

    <div class="row q-col-gutter-md q-mb-md">
      <div class="col-12 col-lg-6">
        <q-select
          outlined
          dense
          :model-value="configModel.selection_mode"
          :options="selectionModeOptions"
          label="Selection Mode"
          emit-value
          map-options
          @update:model-value="value => updateConfig({ selection_mode: value })"
        />
      </div>

      <div v-if="configModel.selection_mode === 'include'" class="col-12 col-lg-6">
        <q-select
          outlined
          dense
          :model-value="configModel.guest_ids"
          :options="guestOptions"
          label="Guests"
          multiple
          emit-value
          map-options
          use-chips
          :loading="loadingGuests"
          @update:model-value="value => updateConfig({ guest_ids: value })"
        />
      </div>
    </div>

    <q-banner v-if="!agentOnline" class="bg-grey-2 text-grey-8">
      Agent must be online on the Proxmox host to discover guests.
    </q-banner>

    <template v-else>
      <q-banner v-if="loadError" class="bg-negative text-white q-mb-md">
        {{ loadError }}
      </q-banner>
      <div class="row items-center q-mb-sm">
        <div class="text-subtitle1">Supported Guests</div>
        <q-space />
        <q-btn flat dense icon="settings" label="Configure Proxmox" :disable="!selectedAgent" @click="showAgentSettings = true" />
        <q-btn flat dense icon="refresh" label="Refresh" :loading="loadingGuests" @click="loadGuests" />
      </div>

      <q-list v-if="guests.length > 0" bordered separator>
        <q-item v-for="guest in guests" :key="guest.vmid">
          <q-item-section>
            <q-item-label>{{ guest.name || `VM ${guest.vmid}` }}</q-item-label>
            <q-item-label caption>
              VMID {{ guest.vmid }} | {{ guest.type }} | {{ guest.status || 'unknown' }}
            </q-item-label>
          </q-item-section>
        </q-item>
      </q-list>
      <div v-else-if="!loadingGuests && !loadError" class="text-grey">No supported Proxmox guests found.</div>
    </template>

    <AgentPropertiesDialog
      v-model="showAgentSettings"
      :agent="selectedAgent"
      initial-tab="configuration"
      @proxmox-updated="loadGuests"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import AgentPropertiesDialog from 'components/agents/AgentPropertiesDialog.vue'
import { useAgentStore } from 'stores/agent'
import { useJobStore } from 'stores/job'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const props = defineProps({
  modelValue: { type: Object, required: true },
  agentId: { type: [Number, String], default: null },
  agentOnline: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const agentStore = useAgentStore()
const jobStore = useJobStore()

const guests = ref([])
const loadingGuests = ref(false)
const loadError = ref('')
const showAgentSettings = ref(false)
const selectedAgent = computed(() => agentStore.agents.find(agent => String(agent.id) === String(props.agentId)))
let loadVersion = 0

const selectionModeOptions = [
  { label: 'All supported guests', value: 'all' },
  { label: 'Select specific guests', value: 'include' },
]

const configModel = computed(() => createConfig(props.modelValue))
const guestOptions = computed(() =>
  guests.value.map(guest => ({
    label: `${guest.name || `VM ${guest.vmid}`} (${guest.vmid})`,
    value: guest.vmid,
  }))
)

watch(
  () => [props.agentId, props.agentOnline],
  loadGuests,
  { immediate: true }
)

function createConfig(config = {}) {
  return {
    selection_mode: config.selection_mode || 'all',
    guest_ids: [...(config.guest_ids || [])],
  }
}

function updateConfig(patch) {
  const nextConfig = {
    ...configModel.value,
    ...patch,
  }

  if (nextConfig.selection_mode !== 'include') {
    nextConfig.guest_ids = []
  }

  emit('update:modelValue', createConfig(nextConfig))
}

async function loadGuests() {
  const version = ++loadVersion
  loadError.value = ''
  guests.value = []
  if (!props.agentId || !props.agentOnline) {
    loadingGuests.value = false
    return
  }

  loadingGuests.value = true
  try {
    const result = await jobStore.getProxmoxGuests(props.agentId)
    if (version === loadVersion) guests.value = result
  } catch (error) {
    if (version === loadVersion && !shouldIgnoreApiError(error)) {
      loadError.value = getApiErrorMessage(error, 'Failed to load Proxmox guests')
    }
  } finally {
    if (version === loadVersion) loadingGuests.value = false
  }
}

defineOptions({ name: 'ProxmoxBackupJobForm' })
</script>
