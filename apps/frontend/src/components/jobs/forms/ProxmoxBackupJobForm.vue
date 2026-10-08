<template>
  <PathSelectionPanel class="q-mt-md" browser-title="Hosts and VMs" selected-title="Selection"
    :selection="selection" :load-entries="loadEntries" :entry-options="entryOptions" :reload-key="reloadKey"
    :available-message="agentOnline ? '' : 'Agent must be online on the Proxmox host to discover VMs.'"
    empty-label="No supported VMs found." empty-selected-label="No host or VMs selected yet."
    error-message="Failed to load Proxmox VMs" @update:selection="updateSelection"
    :advanced-invalid="configModel.backup_mode !== 'snapshot' && !/^(?:[A-Za-z][A-Za-z0-9_.-]*)?$/.test(configModel.fleecing_storage)">
    <template #header>
      <div class="text-subtitle1">Proxmox Selection</div>
      <q-space />
      <q-btn v-if="(selectedAgent?.protocol_version || 0) >= 1" flat dense no-caps no-wrap icon="settings" label="Connection" :to="`/agents/${agentId}?tab=connections`" target="_blank">
        <q-tooltip>Opens in a new tab; your job draft stays here.</q-tooltip>
      </q-btn>
      <q-btn flat dense no-caps no-wrap icon="refresh" label="Refresh" :disable="!agentOnline" @click="refresh" />
    </template>
    <template #options>
      <q-select outlined dense :model-value="configModel.backup_mode" :options="backupModeOptions"
        label="Backup Mode" emit-value map-options
        @update:model-value="value => updateConfig({ backup_mode: value })" />
      <q-banner v-if="configModel.backup_mode !== 'snapshot' && nativeError" class="bg-negative text-white q-mt-sm">
        {{ nativeError }}
      </q-banner>
    </template>
    <template v-if="configModel.backup_mode !== 'snapshot'" #advanced>
      <q-input outlined dense :model-value="configModel.fleecing_storage" label="Temporary backup storage (optional)"
        hint="Leave empty to select a suitable local LVM-thin storage automatically."
        :rules="[value => /^(?:[A-Za-z][A-Za-z0-9_.-]*)?$/.test(value || '') || 'Enter a valid storage ID']"
        @update:model-value="value => updateConfig({ fleecing_storage: value })" />
    </template>
  </PathSelectionPanel>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import PathSelectionPanel from 'components/PathSelectionPanel.vue'
import { useAgentStore } from 'stores/agent'
import { useJobStore } from 'stores/job'

const props = defineProps({
  modelValue: { type: Object, required: true },
  agentId: { type: [Number, String], default: null },
  agentOnline: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const agentStore = useAgentStore()
const jobStore = useJobStore()
const guests = ref(null)
const reloadKey = ref(0)
const selectedAgent = computed(() => agentStore.agents.find(agent => String(agent.id) === String(props.agentId)))
const hostName = computed(() => guests.value?.[0]?.node || selectedAgent.value?.hostname || 'Proxmox host')
const configModel = computed(() => ({
  selection_mode: props.modelValue.selection_mode || 'all',
  guest_ids: props.modelValue.guest_ids || [],
  exclude_guest_ids: props.modelValue.exclude_guest_ids || [],
  backup_mode: props.modelValue.backup_mode || 'snapshot',
  fleecing_storage: props.modelValue.fleecing_storage || '',
}))
const hostSelected = computed(() => configModel.value.selection_mode === 'all')
const nativeError = computed(() => selectedAgent.value?.connections?.proxmox?.native_backups_error
  || (!configModel.value.fleecing_storage && (selectedAgent.value?.protocol_version || 0) < 9
    ? 'Update the agent to select temporary backup storage automatically (protocol 9 required).' : ''))
const backupModeOptions = computed(() => [
  { label: 'Snapshot — existing method', value: 'snapshot' },
  { label: 'Native — read all disks', value: 'native', disable: (selectedAgent.value?.protocol_version || 0) < 8 || !!nativeError.value },
  { label: 'Native — with CBT', value: 'native_cbt', disable: (selectedAgent.value?.protocol_version || 0) < 8 || !!nativeError.value },
])

function hostEntry() {
  return { path: '/host', name: hostName.value, label: hostName.value, group: 'host', icon: 'dns',
    file: false, caption: 'Includes all supported VMs, including newly created ones' }
}
function guestEntry(vmid) {
  const guest = guests.value?.find(guest => guest.vmid === vmid)
  const label = guest?.name ? `${guest.name} (VM ${vmid})` : `VM ${vmid}`
  return { path: `/host/${vmid}`, name: label, label, vmid, group: 'vm', icon: 'computer', file: true,
    caption: guest ? `${guest.type || 'qemu'} | ${guest.status || 'unknown'}` : 'Details unavailable',
    selectionEntry: { vmid } }
}
const selection = computed(() => ({
  paths: hostSelected.value ? [hostEntry()] : configModel.value.guest_ids.map(guestEntry),
  exclude_patterns: hostSelected.value ? configModel.value.exclude_guest_ids.map(guestEntry) : [],
}))
function entryOptions(entry) {
  if (entry.group === 'host') return {
    state: hostSelected.value ? 'include' : null,
    includeDisabled: hostSelected.value, excludeDisabled: !hostSelected.value,
  }
  const excluded = hostSelected.value && configModel.value.exclude_guest_ids.includes(entry.vmid)
  const included = hostSelected.value || configModel.value.guest_ids.includes(entry.vmid)
  return {
    state: excluded ? 'exclude' : included ? 'include' : null,
    note: excluded ? 'Excluded' : hostSelected.value ? `Included via ${hostName.value}` : '',
    includeDisabled: included && !excluded,
  }
}
function updateConfig(patch) { emit('update:modelValue', { ...configModel.value, ...patch }) }
function updateSelection(value) {
  updateConfig({
    selection_mode: value.paths.some(entry => entry.group === 'host') ? 'all' : 'include',
    guest_ids: value.paths.filter(entry => entry.group === 'vm').map(entry => entry.vmid),
    exclude_guest_ids: value.exclude_patterns.filter(entry => entry.group === 'vm').map(entry => entry.vmid),
  })
}
function refresh() { guests.value = null; reloadKey.value++ }
watch(() => [props.agentId, props.agentOnline], refresh)

async function loadEntries(path) {
  const version = reloadKey.value
  const result = guests.value || await jobStore.getProxmoxGuests(props.agentId)
  if (version !== reloadKey.value) return { path, entries: [] }
  guests.value = result
  return { path, parent_directory: '/', path_label: path === '/' ? '/' : hostName.value,
    entries: path === '/' ? [hostEntry()] : result.map(guest => guestEntry(guest.vmid)) }
}
</script>
