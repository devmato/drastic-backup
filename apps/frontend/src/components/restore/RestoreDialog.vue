<template>
  <q-dialog v-model="dialogVisible" persistent :maximized="$q.screen.lt.sm">
    <q-card class="app-dialog-wide db-job-form-dialog column no-wrap overflow-hidden">
      <q-card-section class="col-auto row items-start q-col-gutter-sm q-pa-md">
        <div class="col">
          <div class="text-h6">Restore {{ job?.name }}</div>
          <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">{{ job?.type === 'proxmox' ? 'Restore a VM or export files from its backup.' : 'Restore selected files or directories to a local path on the target agent.' }}</div>
        </div>
      </q-card-section>

      <q-form ref="restoreForm" class="column no-wrap col" @submit="onFormSubmit">
        <q-card-section class="col-shrink scroll q-px-md q-pt-none q-pb-md">
          <div v-if="dialogVisible" class="row q-col-gutter-md">
            <div class="col-12 col-sm-auto">
              <q-list dense separator>
                <q-item
                  v-for="section in sections"
                  :key="section.name"
                  clickable
                  dense
                  :active="activeSection === section.name"
                  :active-class="$q.dark.isActive ? 'bg-grey-9 text-blue-3 text-weight-medium' : 'bg-grey-2 text-primary text-weight-medium'"
                  :disable="submitting || !canOpenSection(section.name)"
                  @click="activeSection = section.name"
                >
                  <q-item-section side><q-icon :name="section.icon" size="xs" /></q-item-section>
                  <q-item-section>{{ section.label }}</q-item-section>
                </q-item>
              </q-list>
            </div>

            <!-- Keep browsers mounted across steps; unmount on close or source changes to clean up sessions. -->
            <div class="col-12 col-sm">
              <div v-show="activeSection === 'source'">
                <div class="q-gutter-sm">
                  <q-select outlined :dense="!$q.platform.has.touch" hide-bottom-space lazy-rules="ondemand" v-model="selectedAgentId" :options="agentOptions" label="Restore agent" emit-value map-options :rules="[val => !!val || 'Required']" :disable="submitting || activeSection !== 'source'" />
                  <q-select outlined :dense="!$q.platform.has.touch" hide-bottom-space lazy-rules="ondemand" v-model="selectedRepositoryId" :options="repositoryOptions" label="Source repository" emit-value map-options :rules="[val => !!val || 'Required']" :disable="submitting || activeSection !== 'source'" />
                  <q-select v-if="modeOptions.length > 1" outlined :dense="!$q.platform.has.touch" hide-bottom-space lazy-rules="ondemand" v-model="selectedMode" :options="modeOptions" label="Restore mode" emit-value map-options :rules="[val => !!val || 'Required']" :disable="submitting || activeSection !== 'source'" />
                  <q-select
                    :dense="!$q.platform.has.touch" hide-bottom-space
                    outlined
                    lazy-rules="ondemand"
                    v-model="selectedSnapshotId"
                    :options="snapshotOptions"
                    label="Snapshot"
                    emit-value
                    map-options
                    :loading="loadingSnapshots"
                    :disable="!canLoadSnapshots || loadingSnapshots || submitting || activeSection !== 'source'"
                    :rules="[val => !!val || 'Required']"
                  >
                    <template #append>
                      <q-btn flat dense round icon="refresh" aria-label="Reload snapshots" :disable="!canLoadSnapshots || loadingSnapshots || submitting" @click.stop="loadSnapshots">
                        <q-tooltip>Reload snapshots</q-tooltip>
                      </q-btn>
                    </template>
                    <template v-slot:no-option>
                      <q-item><q-item-section>No compatible snapshots found.</q-item-section></q-item>
                    </template>
                  </q-select>
                </div>
              </div>

              <ProxmoxRestorePanel
                v-if="isProxmoxMode && selectedSnapshotId"
                v-show="activeSection === proxmoxSection"
                :key="sourceKey"
                ref="proxmoxPanel"
                v-model="proxmoxSelection"
                :source="restoreSource"
                :mode="selectedMode"
                :disabled="submitting || activeSection !== proxmoxSection"
              />
              <div v-if="!isProxmoxMode && selectedSnapshotId" v-show="activeSection === 'selection'">
                <PathSelectionPanel
                  :key="sourceKey"
                  mode="include-only"
                  :load-entries="loadSnapshotEntries"
                  v-model:selected-paths="includePaths"
                  :reload-key="sourceKey"
                  browser-title="Snapshot Browser"
                  selected-title="Selected Restore Paths"
                  selected-caption="Restore"
                  empty-selected-label="No restore paths selected yet."
                  empty-label="No entries found."
                  error-message="Could not load snapshot entries"
                />
              </div>

              <div v-show="activeSection === 'target'">
                <template v-if="isFileMode">
                  <q-input outlined :dense="!$q.platform.has.touch" hide-bottom-space lazy-rules="ondemand" v-model="restoreLocation" label="Restore location on target agent" :disable="submitting || activeSection !== 'target'" :rules="[val => !!val?.trim() || 'Required']">
                    <template #append>
                      <q-btn flat dense round icon="folder_open" aria-label="Browse target agent directories" :disable="!selectedAgentId || submitting" @click.stop="showTargetBrowserDialog = true">
                        <q-tooltip>Browse target agent directories</q-tooltip>
                      </q-btn>
                    </template>
                  </q-input>
                  <q-option-group class="q-mt-md" v-model="overwritePolicy" type="radio" :options="overwriteOptions" :disable="submitting" />
                  <q-checkbox v-if="overwritePolicy === 'overwrite'" v-model="overwriteConfirmed" :disable="submitting" label="I understand that existing files at the restore destination may be replaced" />
                </template>
                <q-banner v-if="isCrossAgentRestore" class="q-mt-md bg-orange-1 text-orange-10">
                  Cross-agent restore: this snapshot was created by a different agent.
                  <q-checkbox v-model="crossAgentConfirmed" :disable="submitting" label="Restore it to the selected agent" />
                </q-banner>
              </div>
            </div>
          </div>
        </q-card-section>

        <q-card-actions class="col-auto q-mt-auto q-px-md q-py-sm" align="right">
          <q-btn flat no-caps label="Cancel" :disable="submitting" v-close-popup />
          <q-btn v-if="previousSection" flat no-caps icon="chevron_left" label="Prev" color="primary" :disable="submitting" @click="activeSection = previousSection.name" />
          <q-btn v-if="nextSection" unelevated no-caps label="Next" icon-right="chevron_right" type="submit" color="primary" :disable="!canGoNext || submitting" />
          <q-btn v-else unelevated no-caps label="Start Restore" type="submit" color="primary" :loading="submitting" :disable="!canSubmit" />
        </q-card-actions>
      </q-form>
    </q-card>

    <RestoreTargetBrowserDialog
      v-model="showTargetBrowserDialog"
      :agent-id="selectedAgentId"
      @pick="restoreLocation = $event"
    />
  </q-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import PathSelectionPanel from 'components/PathSelectionPanel.vue'
import RestoreTargetBrowserDialog from 'components/restore/RestoreTargetBrowserDialog.vue'
import ProxmoxRestorePanel from 'components/restore/ProxmoxRestorePanel.vue'
import { useRestoreStore } from 'stores/restore'
import { useOperationStore } from 'stores/operation'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const props = defineProps({
  job: { type: Object, default: null },
  agents: { type: Array, default: () => [] },
  repositories: { type: Array, default: () => [] },
})
const emit = defineEmits(['started'])

const $q = useQuasar()
const restoreStore = useRestoreStore()
const operationStore = useOperationStore()

const selectedAgentId = ref(null)
const selectedRepositoryId = ref(null)
const selectedMode = ref('plain_file')
const selectedSnapshotId = ref(null)
const snapshots = ref([])
const includePaths = ref([])
const restoreLocation = ref('/tmp/drastic-restore')
const loadingSnapshots = ref(false)
const submitting = ref(false)
const showTargetBrowserDialog = ref(false)
const overwritePolicy = ref('fail_if_exists')
const overwriteConfirmed = ref(false)
const crossAgentConfirmed = ref(false)
const proxmoxSelection = ref({})
const proxmoxPanel = ref(null)
const restoreForm = ref(null)
const activeSection = ref('source')
let snapshotsRequest = 0

const dialogVisible = defineModel({ type: Boolean, required: true })

const agentOptions = computed(() => props.agents.filter(agent => agent.online).map(agent => ({ label: agent.display_name, value: agent.id })))
const assignedRepositories = computed(() => props.agents.find(agent => agent.id === selectedAgentId.value)?.repositories || props.repositories)
const repositoryOptions = computed(() => assignedRepositories.value.map(repo => ({ label: `${repo.name} (${repo.location})`, value: repo.id })))
const modeOptions = computed(() => [
  ...(props.job?.type === 'proxmox' ? [{ label: 'Whole VM', value: 'proxmox_vm' }, { label: 'Files from VM', value: 'proxmox_files' }] : []),
  { label: props.job?.type === 'proxmox' ? 'Archive files' : 'Plain file restore', value: 'plain_file' },
])
const isProxmoxMode = computed(() => selectedMode.value !== 'plain_file')
const isFileMode = computed(() => selectedMode.value !== 'proxmox_vm')
const proxmoxSection = computed(() => isFileMode.value ? 'selection' : 'target')
const sections = computed(() => [
  { name: 'source', label: 'Source', icon: 'backup' },
  ...(isFileMode.value ? [{ name: 'selection', label: 'Selection', icon: 'checklist' }] : []),
  { name: 'target', label: 'Target', icon: 'restore' },
])
const activeSectionIndex = computed(() => sections.value.findIndex(section => section.name === activeSection.value))
const previousSection = computed(() => sections.value[activeSectionIndex.value - 1])
const nextSection = computed(() => sections.value[activeSectionIndex.value + 1])
const restoreSource = computed(() => ({ job_id: props.job?.id, agent_id: selectedAgentId.value, repository_id: selectedRepositoryId.value, snapshot_id: selectedSnapshotId.value }))
const sourceKey = computed(() => `${JSON.stringify(restoreSource.value)}:${selectedMode.value}`)
const canLoadSnapshots = computed(() => Boolean(props.job?.id && selectedAgentId.value && selectedRepositoryId.value))
const snapshotOptions = computed(() => snapshots.value.filter(snapshot => !isProxmoxMode.value || (snapshot.proxmox_archive && !snapshot.restore_error)).map(snapshot => ({ label: snapshotLabel(snapshot), value: snapshot.id })))
const isCrossAgentRestore = computed(() => Boolean(selectedAgentId.value && props.job?.agent_id && selectedAgentId.value !== props.job.agent_id))
const sourceReady = computed(() => canLoadSnapshots.value && !loadingSnapshots.value
  && agentOptions.value.some(agent => agent.value === selectedAgentId.value)
  && repositoryOptions.value.some(repository => repository.value === selectedRepositoryId.value)
  && snapshotOptions.value.some(snapshot => snapshot.value === selectedSnapshotId.value))
const selectionReady = computed(() => !isFileMode.value || (selectedMode.value === 'proxmox_files'
  ? Boolean(proxmoxSelection.value.session_id && proxmoxSelection.value.volume && proxmoxSelection.value.include_paths?.length) && !proxmoxPanel.value?.busy
  : includePaths.value.length > 0))
const canGoNext = computed(() => sourceReady.value && (activeSection.value === 'source' || selectionReady.value))
const canSubmit = computed(() => sourceReady.value && selectionReady.value
  && (isFileMode.value ? Boolean(restoreLocation.value.trim()) : Boolean(proxmoxPanel.value?.canSubmit))
  && (!isFileMode.value || overwritePolicy.value !== 'overwrite' || overwriteConfirmed.value)
  && (!isCrossAgentRestore.value || crossAgentConfirmed.value))
const overwriteOptions = [
  { label: 'Fail if selected files already exist (recommended)', value: 'fail_if_exists' },
  { label: 'Overwrite existing files', value: 'overwrite' },
]

function canOpenSection(name) {
  return name === 'source' || (sourceReady.value && (name === 'selection' || selectionReady.value))
}

function onFormSubmit() {
  if (submitting.value) return
  if (nextSection.value) {
    if (canGoNext.value) activeSection.value = nextSection.value.name
    return
  }
  return submitRestore()
}

function resetDialog() {
  activeSection.value = 'source'
  restoreForm.value?.resetValidation()
  loadingSnapshots.value = false
  selectedAgentId.value = props.agents.find(agent => agent.id === props.job?.agent_id && agent.online)?.id || props.agents.find(agent => agent.online)?.id || null
  selectedRepositoryId.value = assignedRepositories.value[0]?.id || null
  selectedMode.value = props.job?.type === 'proxmox' ? 'proxmox_vm' : 'plain_file'
  selectedSnapshotId.value = null
  snapshots.value = []
  includePaths.value = []
  restoreLocation.value = '/tmp/drastic-restore'
  showTargetBrowserDialog.value = false
  overwritePolicy.value = 'fail_if_exists'
  overwriteConfirmed.value = false
  crossAgentConfirmed.value = false
}

async function loadSnapshots() {
  const request = ++snapshotsRequest
  if (!canLoadSnapshots.value) { loadingSnapshots.value = false; return }
  activeSection.value = 'source'
  restoreForm.value?.resetValidation()
  loadingSnapshots.value = true
  try {
    const result = await restoreStore.getSnapshots({ jobId: props.job.id, agentId: selectedAgentId.value, repositoryId: selectedRepositoryId.value })
    if (request !== snapshotsRequest || !dialogVisible.value) return
    snapshots.value = result
    selectedSnapshotId.value = snapshotOptions.value[0]?.value || null
  } catch (e) {
    if (request !== snapshotsRequest) return
    snapshots.value = []
    selectedSnapshotId.value = null
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Could not load snapshots'), color: 'red', position: 'top' })
  } finally {
    if (request === snapshotsRequest) loadingSnapshots.value = false
  }
}

async function loadSnapshotEntries(path = '/') {
  if (!selectedSnapshotId.value || !selectedAgentId.value || !selectedRepositoryId.value) return
  return {
    path,
    entries: await restoreStore.getEntries({ agentId: selectedAgentId.value, repositoryId: selectedRepositoryId.value, snapshotId: selectedSnapshotId.value, path }),
  }
}

async function submitRestore() {
  // Enter and direct calls must obey the same readiness and confirmation gates as the button.
  if (activeSection.value !== 'target' || !canSubmit.value || submitting.value) return
  submitting.value = true
  try {
    const result = await operationStore.startRestore({
      ...restoreSource.value,
      mode: selectedMode.value,
      restore_location: isFileMode.value ? restoreLocation.value : null,
      include_paths: selectedMode.value === 'plain_file' ? includePaths.value : [],
      overwrite_policy: overwritePolicy.value,
      ...(isProxmoxMode.value ? proxmoxSelection.value : {}),
    })
    proxmoxPanel.value?.handOff()
    emit('started', { ...result, agent_id: selectedAgentId.value, job_id: props.job.id })
    dialogVisible.value = false
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  } finally {
    submitting.value = false
  }
}

function snapshotLabel(snapshot) {
  const id = snapshot.short_id || String(snapshot.id || '').slice(0, 8)
  const time = snapshot.time ? new Date(snapshot.time).toLocaleString() : 'unknown time'
  const vmid = snapshot.tags?.find(tag => tag.startsWith('vmid:'))?.slice(5)
  const guest = vmid ? `VM ${vmid}${snapshot.guest_name ? ` (${snapshot.guest_name})` : ''} — ` : ''
  const dataset = snapshot.tags?.find(tag => tag.startsWith('dataset:'))?.slice(8)
  return `${dataset ? `${dataset} — ` : guest}${time} — ${id}`
}

watch(dialogVisible, value => {
  if (!value) { snapshotsRequest++; return }
  resetDialog()
})

watch(sourceKey, () => {
  activeSection.value = 'source'
  restoreForm.value?.resetValidation()
  includePaths.value = []
  proxmoxSelection.value = {}
  crossAgentConfirmed.value = false
  overwriteConfirmed.value = false
})

watch(selectedMode, () => {
  selectedSnapshotId.value = snapshotOptions.value[0]?.value || null
})

watch(selectedAgentId, () => {
  if (!assignedRepositories.value.some(repo => repo.id === selectedRepositoryId.value)) {
    selectedRepositoryId.value = assignedRepositories.value[0]?.id || null
  }
})

watch([dialogVisible, selectedAgentId, selectedRepositoryId], async () => {
  if (!dialogVisible.value) return
  snapshots.value = []
  selectedSnapshotId.value = null
  includePaths.value = []
  crossAgentConfirmed.value = false
  await loadSnapshots()
})

watch(overwritePolicy, () => {
  overwriteConfirmed.value = false
})

defineOptions({ name: 'RestoreDialog' })
</script>
