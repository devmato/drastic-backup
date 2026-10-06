<template>
  <div class="q-mt-md">
    <q-linear-progress v-if="loadingOptions" indeterminate aria-label="Loading restore host options" />
    <q-banner v-if="error" class="q-mb-md text-negative" role="alert">{{ error }}</q-banner>
    <template v-if="options && mode === 'proxmox_vm'">
      <div class="row q-col-gutter-sm">
        <div class="col-12 col-md-4">
          <q-input v-model.number="selection.vmid" outlined type="number" label="Target VMID" min="100" max="999999999" :rules="[validateVmid]" />
        </div>
        <div class="col-12 col-md-8">
          <q-select v-model="selection.storage" outlined :options="storageOptions" label="Target storage" emit-value map-options :rules="[val => !!val || 'Required']" />
        </div>
      </div>
      <q-checkbox v-model="selection.unique" label="Generate new MAC addresses" />
      <div class="text-caption q-mt-sm">Target node: {{ options.node }}. The restored VM stays stopped. Existing VMs cannot be replaced.</div>
    </template>
    <template v-if="options && mode === 'proxmox_files'">
      <q-banner v-if="options.guest_files_error" class="text-negative">{{ options.guest_files_error }}</q-banner>
      <template v-else>
        <div class="text-caption q-mb-sm">{{ options.guest_files_on_demand ? 'TAR and Native/CBT backups are read on demand without storing complete guest disks locally. Allow space for working data and exported files. Legacy VMA backups still require archive download and disk extraction.' : 'Preparation downloads and extracts the guest disks on this agent. Allow space for the backup data, extracted disks and exported files.' }} Browser sessions expire after one hour of inactivity.</div>
        <div v-if="!selection.session_id" class="row items-center q-gutter-sm">
          <q-btn color="primary" label="Prepare file browser" :loading="preparing" :disable="preparing" @click="prepare" />
          <q-btn v-if="preparing && operationId" flat label="Cancel preparation" @click="cancelPreparation" />
        </div>
        <q-btn v-else flat label="Prepare again" :disable="disabled || browsing" @click="prepareAgain" />
        <div v-if="phase" class="text-caption q-mt-sm" role="status">{{ phase }}</div>
        <q-linear-progress v-if="preparing" class="q-mt-sm" indeterminate aria-label="Preparing guest files" />
        <template v-if="selection.session_id">
          <q-select v-model="selection.volume" class="q-mt-md" outlined :options="volumeOptions" label="Guest filesystem / volume" emit-value map-options :disable="disabled || browsing" @update:model-value="selection.include_paths = []" />
          <div v-for="volume in unavailableVolumes" :key="volume.device" class="text-caption text-warning q-mt-xs">{{ volume.device }}: {{ volume.error }}</div>
          <PathSelectionPanel
            v-if="selection.volume"
            class="q-mt-md"
            mode="include-only"
            :load-entries="loadEntries"
            v-model:selected-paths="selection.include_paths"
            :reload-key="selection.volume"
            browser-title="Guest Files"
            selected-title="Selected Restore Paths"
            selected-caption="Restore"
            empty-selected-label="Select files or directories to export."
          />
        </template>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import PathSelectionPanel from 'components/PathSelectionPanel.vue'
import { useRestoreStore } from 'stores/restore'
import { useOperationStore } from 'stores/operation'
import { useAgentStore } from 'stores/agent'
import { getApiErrorMessage } from 'src/utils/api-error'

const props = defineProps({ source: { type: Object, required: true }, mode: { type: String, required: true }, disabled: Boolean })
const selection = defineModel({ type: Object, required: true })
const source = { ...props.source }
const restoreStore = useRestoreStore()
const operationStore = useOperationStore()
const agentStore = useAgentStore()
const options = ref(null)
const loadingOptions = ref(true)
const preparing = ref(false)
const browsing = ref(false)
const operationId = ref(null)
const phase = ref('')
const error = ref('')
const volumes = ref([])
let timer = null
let disposed = false
let handedOff = false
let sessionId = null

const storageOptions = computed(() => (options.value?.storages || []).map(s => ({
  label: `${s.storage} (${Math.floor(Number(s.avail) / 1024 ** 3)} GiB free)`, value: s.storage,
})))
const volumeOptions = computed(() => volumes.value.filter(v => !v.error).map(v => ({ label: `${v.device} (${v.filesystem})`, value: v.device })))
const unavailableVolumes = computed(() => volumes.value.filter(v => v.error))

function validateVmid(value) {
  return (Number.isInteger(value) && value >= 100 && value <= 999999999 && !options.value?.used_vmids.includes(value)) || 'Enter a free VMID (100–999999999)'
}

async function cleanup() {
  try {
    if (preparing.value && operationId.value) await operationStore.cancelRestore(operationId.value)
    if (sessionId) await restoreStore.proxmoxAction({ ...source, action: 'close', session_id: sessionId })
  } catch {
    // The agent also expires idle workspaces when a browser disconnects.
  }
}

async function pollPreparation() {
  try {
    const operation = await agentStore.getOperation(operationId.value)
    if (disposed) return
    phase.value = operation.data?.restore_phase || 'Preparing guest file access…'
    if (operation.state !== 'running') {
      preparing.value = false
      if (['success', 'warning'].includes(operation.state) && operation.data?.session_id) {
        phase.value = operation.data.restore_phase || 'Ready to browse'
        sessionId = operation.data.session_id
        volumes.value = operation.data.volumes || []
        selection.value = { session_id: sessionId, volume: volumeOptions.value[0]?.value || null, include_paths: [] }
      } else {
        error.value = operation.logs?.filter(log => log.level === 'error').slice(-1)[0]?.message || `Preparation ${operation.state}`
      }
      return
    }
  } catch (e) {
    if (disposed) return
    error.value = getApiErrorMessage(e, 'Could not refresh preparation status; retrying…')
  }
  timer = setTimeout(pollPreparation, 2000)
}

async function prepare() {
  error.value = ''
  preparing.value = true
  try {
    const result = await operationStore.startRestore({ ...source, mode: 'proxmox_prepare' })
    operationId.value = result.operation_id
    if (disposed) return await cleanup()
    await pollPreparation()
  } catch (e) {
    preparing.value = false
    error.value = getApiErrorMessage(e)
  }
}

async function prepareAgain() {
  await cleanup()
  sessionId = null
  selection.value = {}
  volumes.value = []
  await prepare()
}

async function cancelPreparation() {
  try {
    await operationStore.cancelRestore(operationId.value)
    phase.value = 'Cancellation requested…'
  } catch (e) {
    error.value = getApiErrorMessage(e)
  }
}

async function loadEntries(path) {
  browsing.value = true
  try {
    return { path, ...await restoreStore.proxmoxAction({ ...source, action: 'entries', session_id: sessionId, volume: selection.value.volume, path }) }
  } finally {
    browsing.value = false
  }
}

onMounted(async () => {
  try {
    const result = await restoreStore.proxmoxAction({ action: 'options', mode: props.mode, agent_id: source.agent_id })
    if (disposed) return
    options.value = result
    if (props.mode === 'proxmox_vm') {
      selection.value = { vmid: result.next_vmid, storage: storageOptions.value[0]?.value || null, unique: true }
    }
  } catch (e) {
    if (!disposed) error.value = getApiErrorMessage(e)
  } finally {
    loadingOptions.value = false
  }
})

onBeforeUnmount(() => {
  disposed = true
  clearTimeout(timer)
  if (!handedOff) void cleanup()
})

defineExpose({ handOff: () => { handedOff = true }, busy: computed(() => preparing.value || browsing.value) })
</script>
