<template>
  <SelectionLayout class="q-mt-md" available-title="Available Guests"
    :selected-title="includeMode ? 'Selected Guests' : 'Exclusions'"
    :advanced-invalid="configModel.backup_mode !== 'snapshot' && !/^(?:[A-Za-z][A-Za-z0-9_.-]*)?$/.test(configModel.fleecing_storage)">
    <template #header>
      <div class="text-subtitle1">Guest Selection</div>
      <q-space />
      <q-badge color="grey-7" :label="`${selectedIds.length} ${includeMode ? 'selected' : 'excluded'}`" />
      <div class="row q-gutter-xs">
        <q-btn v-if="(selectedAgent?.protocol_version || 0) >= 1" flat dense no-caps no-wrap icon="settings" label="Connection" :to="`/agents/${agentId}?tab=connections`" target="_blank">
          <q-tooltip>Opens in a new tab; your job draft stays here.</q-tooltip>
        </q-btn>
        <q-btn flat dense no-caps no-wrap icon="refresh" label="Refresh" :loading="loadingGuests" :disable="!agentOnline" @click="loadGuests" />
      </div>
    </template>
    <template #options>
      <div class="row q-col-gutter-sm q-mb-sm">
        <div class="col-12 col-lg-6">
          <q-select outlined dense :model-value="configModel.backup_mode" :options="backupModeOptions"
            label="Backup Mode" emit-value map-options
            @update:model-value="value => updateConfig({ backup_mode: value })" />
        </div>
        <div class="col-12 col-lg-6">
          <q-select outlined dense :model-value="configModel.selection_mode" :options="selectionModeOptions"
            label="Selection Mode" emit-value map-options
            @update:model-value="value => updateConfig({ selection_mode: value })" />
        </div>
      </div>
      <div class="text-caption">
        {{ includeMode ? 'Only selected guests are backed up. Select at least one guest.' : 'All supported guests are backed up except exclusions. New guests are included automatically.' }}
      </div>
      <q-banner v-if="configModel.backup_mode !== 'snapshot' && nativeError" class="bg-negative text-white q-mt-sm">
        {{ nativeError }}
      </q-banner>
    </template>
    <template #available>
      <q-card flat bordered>
        <q-scroll-area style="height: 320px">
          <q-banner v-if="!agentOnline">
            Agent must be online on the Proxmox host to discover guests.
          </q-banner>
          <q-banner v-else-if="loadError" class="bg-negative text-white">
            {{ loadError }}
          </q-banner>
          <div v-else-if="loadingGuests" class="text-grey q-pa-md">Loading guests…</div>
          <q-list v-else-if="availableGuests.length > 0" separator>
            <q-item v-for="guest in availableGuests" :key="guest.vmid">
              <q-item-section>
                <q-item-label class="db-break-word">{{ guest.name || `VM ${guest.vmid}` }}</q-item-label>
                <q-item-label caption>
                  VMID {{ guest.vmid }} | {{ guest.type }} | {{ guest.status || 'unknown' }}
                </q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn flat dense round :icon="includeMode ? 'add' : 'remove'" :color="includeMode ? 'positive' : 'negative'" :aria-label="`${includeMode ? 'Select' : 'Exclude'} VM ${guest.vmid}`" @click="updateSelectedIds([...selectedIds, guest.vmid])">
                  <q-tooltip>{{ includeMode ? 'Select guest' : 'Exclude guest' }}</q-tooltip>
                </q-btn>
              </q-item-section>
            </q-item>
          </q-list>
          <div v-else class="text-grey q-pa-md">
            {{ guests.length ? 'No remaining guests available.' : 'No supported Proxmox guests found.' }}
          </div>
        </q-scroll-area>
      </q-card>
    </template>
    <template #selected>
      <q-card flat bordered>
        <q-scroll-area style="height: 320px">
          <q-list v-if="selectedGuests.length > 0" separator>
            <q-item v-for="guest in selectedGuests" :key="guest.vmid">
              <q-item-section>
                <q-item-label class="db-break-word">{{ guest.name || `VM ${guest.vmid}` }}</q-item-label>
                <q-item-label caption>
                  VMID {{ guest.vmid }}
                  <template v-if="guest.type"> | {{ guest.type }} | {{ guest.status || 'unknown' }}</template>
                  <template v-else> | Details unavailable</template>
                </q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn flat dense round icon="delete" color="grey-7" :aria-label="`Remove VM ${guest.vmid} from ${includeMode ? 'selection' : 'exclusions'}`" @click="updateSelectedIds(selectedIds.filter(id => id !== guest.vmid))">
                  <q-tooltip>{{ includeMode ? 'Remove from selection' : 'Remove exclusion' }}</q-tooltip>
                </q-btn>
              </q-item-section>
            </q-item>
          </q-list>
          <div v-else class="text-grey q-pa-md">
            {{ includeMode ? 'No guests selected yet.' : 'No exclusions. All supported guests will be backed up.' }}
          </div>
        </q-scroll-area>
      </q-card>
    </template>
    <template v-if="configModel.backup_mode !== 'snapshot'" #advanced>
      <q-input outlined dense :model-value="configModel.fleecing_storage" label="Temporary backup storage (optional)"
        hint="Leave empty to select a suitable local LVM-thin storage automatically."
        :rules="[value => /^(?:[A-Za-z][A-Za-z0-9_.-]*)?$/.test(value || '') || 'Enter a valid storage ID']"
        @update:model-value="value => updateConfig({ fleecing_storage: value })" />
    </template>
  </SelectionLayout>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import SelectionLayout from 'components/SelectionLayout.vue'
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
const selectedAgent = computed(() => agentStore.agents.find(agent => String(agent.id) === String(props.agentId)))
const nativeError = computed(() => selectedAgent.value?.connections?.proxmox?.native_backups_error
  || (!configModel.value.fleecing_storage && (selectedAgent.value?.protocol_version || 0) < 9
    ? 'Update the agent to select temporary backup storage automatically (protocol 9 required).' : ''))
let loadVersion = 0

const selectionModeOptions = [
  { label: 'All guests', value: 'all' },
  { label: 'Specific guests', value: 'include' },
]
const backupModeOptions = computed(() => [
  { label: 'Snapshot — existing method', value: 'snapshot' },
  { label: 'Native — read all disks', value: 'native', disable: (selectedAgent.value?.protocol_version || 0) < 8 || !!nativeError.value },
  { label: 'Native — with CBT', value: 'native_cbt', disable: (selectedAgent.value?.protocol_version || 0) < 8 || !!nativeError.value },
])

const configModel = computed(() => createConfig(props.modelValue))
const includeMode = computed(() => configModel.value.selection_mode === 'include')
const selectedIds = computed(() => includeMode.value ? configModel.value.guest_ids : configModel.value.exclude_guest_ids)
const availableGuests = computed(() => guests.value.filter(guest => !selectedIds.value.includes(guest.vmid)))
const selectedGuests = computed(() =>
  selectedIds.value.map(vmid => guests.value.find(guest => guest.vmid === vmid) || { vmid })
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
    exclude_guest_ids: [...(config.exclude_guest_ids || [])],
    backup_mode: config.backup_mode || 'snapshot',
    fleecing_storage: config.fleecing_storage || '',
  }
}

function updateConfig(patch) {
  emit('update:modelValue', createConfig({
    ...configModel.value,
    ...patch,
  }))
}

function updateSelectedIds(ids) {
  updateConfig({ [includeMode.value ? 'guest_ids' : 'exclude_guest_ids']: ids })
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
