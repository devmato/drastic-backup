<template>
  <q-dialog v-model="dialogVisible" persistent :maximized="$q.screen.lt.sm">
    <q-card class="app-dialog-wide db-job-form-dialog column no-wrap overflow-hidden">
      <q-card-section class="col-auto row items-start q-col-gutter-sm q-pa-md">
        <div class="col">
          <div class="text-h6">Restore {{ showAllRestorePoints ? '' : job?.name }}</div>
          <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Browse, restore or delete backup points in an assigned repository, including backups without a Drastic job.</div>
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
                  <q-checkbox v-model="showAllRestorePoints" label="Show all restore points"
                    :disable="submitting || activeSection !== 'source'" aria-describedby="restore-points-help" />
                  <div id="restore-points-help" class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
                    {{ showAllRestorePoints ? 'Showing all restore points in this repository, including other agents and jobs.' : 'Showing restore points backed up by the selected agent. Enable this option for other agents, jobs or older backups without an agent UUID.' }}
                  </div>
                  <q-select
                    :dense="!$q.platform.has.touch" hide-bottom-space
                    outlined
                    lazy-rules="ondemand"
                    v-model="selectedPointId"
                    :options="pointOptions"
                    label="Restore point"
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
                      <q-btn flat dense round icon="delete" color="negative" aria-label="Delete restore point"
                        :disable="!selectedPoint || !supportsRestorePoints || loadingSnapshots || submitting" @click.stop="confirmDeletePoint">
                        <q-tooltip>{{ supportsRestorePoints ? 'Delete this restore point and all its snapshots' : 'Update the agent to delete restore points (protocol 22)' }}</q-tooltip>
                      </q-btn>
                    </template>
                    <template v-slot:no-option>
                      <q-item><q-item-section>No compatible snapshots found.</q-item-section></q-item>
                    </template>
                  </q-select>
                  <q-select v-if="selectedPoint?.snapshots.length > 1" outlined :dense="!$q.platform.has.touch" hide-bottom-space
                    v-model="selectedSnapshotId" :options="componentOptions" label="Backup component" emit-value map-options :disable="submitting" />
                  <q-select v-if="modeOptions.length > 1" outlined :dense="!$q.platform.has.touch" hide-bottom-space lazy-rules="ondemand" v-model="selectedMode" :options="modeOptions" label="Restore mode" emit-value map-options :rules="[val => !!val || 'Required']" :disable="submitting || activeSection !== 'source'" />
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
                  <div v-if="selectedMode === 'plain_file'" class="row justify-end q-mt-sm">
                    <q-btn flat dense no-caps label="Advanced" :icon-right="advancedOpen ? 'expand_less' : 'expand_more'"
                      :aria-expanded="advancedOpen" aria-controls="restore-advanced" @click="advancedOpen = !advancedOpen" />
                  </div>
                  <div v-if="selectedMode === 'plain_file'" v-show="advancedOpen" id="restore-advanced" class="q-mt-sm">
                    <q-checkbox v-model="ownershipByName" :disable="submitting || !supportsRestoreMetadataOptions"
                      label="Match ownership by user and group names" aria-describedby="restore-ownership-help" />
                    <div id="restore-ownership-help" class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
                      Matches backed-up owners to local accounts by name instead of numeric IDs. File permissions and ACLs remain unchanged.
                    </div>
                    <q-checkbox class="q-mt-sm" v-model="skipXattrs" :disable="submitting || !supportsRestoreMetadataOptions"
                      label="Skip restoring extended attributes" aria-describedby="restore-xattrs-help" />
                    <div id="restore-xattrs-help" class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
                      Does not restore extended attributes, including ACLs stored as extended attributes. Other extended metadata is also omitted. Existing attributes are not removed.
                    </div>
                    <div v-if="!supportsRestoreMetadataOptions" class="text-caption q-mt-xs">Update the agent to use these options (protocol 17 required).</div>
                  </div>
                </template>
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
  agentId: { type: Number, default: null },
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
const showAllRestorePoints = ref(false)
const includePaths = ref([])
const restoreLocation = ref('/tmp/drastic-restore')
const loadingSnapshots = ref(false)
const submitting = ref(false)
const showTargetBrowserDialog = ref(false)
const overwritePolicy = ref('fail_if_exists')
const overwriteConfirmed = ref(false)
const advancedOpen = ref(false)
const ownershipByName = ref(false)
const skipXattrs = ref(false)
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
  ...(selectedSnapshot.value?.proxmox_archive && !selectedSnapshot.value?.restore_error ? [{ label: 'Whole VM', value: 'proxmox_vm' }, { label: 'Files from VM', value: 'proxmox_files' }] : []),
  { label: selectedSnapshot.value?.proxmox_archive ? 'Archive files' : 'Plain file restore', value: 'plain_file' },
])
const selectedSnapshot = computed(() => selectedPoint.value?.snapshots.find(snapshot => snapshot.id === selectedSnapshotId.value))
const isProxmoxMode = computed(() => selectedMode.value !== 'plain_file')
const isFileMode = computed(() => selectedMode.value !== 'proxmox_vm')
const supportsRestoreMetadataOptions = computed(() => (props.agents.find(agent => agent.id === selectedAgentId.value)?.protocol_version || 0) >= 17)
const supportsRestorePoints = computed(() => (props.agents.find(agent => agent.id === selectedAgentId.value)?.protocol_version || 0) >= 22)
const proxmoxSection = computed(() => isFileMode.value ? 'selection' : 'target')
const sections = computed(() => [
  { name: 'source', label: 'Source', icon: 'backup' },
  ...(isFileMode.value ? [{ name: 'selection', label: 'Selection', icon: 'checklist' }] : []),
  { name: 'target', label: 'Target', icon: 'restore' },
])
const activeSectionIndex = computed(() => sections.value.findIndex(section => section.name === activeSection.value))
const previousSection = computed(() => sections.value[activeSectionIndex.value - 1])
const nextSection = computed(() => sections.value[activeSectionIndex.value + 1])
const sourceJobId = computed(() => showAllRestorePoints.value ? undefined : props.job?.id)
const restoreSource = computed(() => ({ job_id: sourceJobId.value,
  job_uuid: selectedSnapshot.value?.tags?.find(tag => tag.startsWith('job_uuid:'))?.slice(9),
  agent_id: selectedAgentId.value, repository_id: selectedRepositoryId.value, snapshot_id: selectedSnapshotId.value }))
const sourceKey = computed(() => `${JSON.stringify(restoreSource.value)}:${selectedMode.value}`)
const canLoadSnapshots = computed(() => Boolean(selectedAgentId.value && selectedRepositoryId.value))
const restorePoints = computed(() => {
  const groups = new Map()
  for (const snapshot of snapshots.value) {
    const id = snapshot.restore_point_id || snapshot.id
    if (!groups.has(id)) groups.set(id, { id, snapshots: [] })
    groups.get(id).snapshots.push(snapshot)
  }
  const agentUuid = props.agents.find(agent => agent.id === selectedAgentId.value)?.uuid
  // Filter whole points so deletion still confirms every component of the run.
  return [...groups.values()].filter(point => showAllRestorePoints.value
    || (agentUuid && point.snapshots.some(snapshot => snapshot.hostname === `drastic-${agentUuid}`)))
})
const selectedPoint = computed(() => restorePoints.value.find(point => point.snapshots.some(snapshot => snapshot.id === selectedSnapshotId.value)))
const selectedPointId = computed({
  get: () => selectedPoint.value?.id,
  set: id => { selectedSnapshotId.value = restorePoints.value.find(point => point.id === id)?.snapshots[0]?.id || null },
})
const pointOptions = computed(() => restorePoints.value.map(point => {
  const snapshot = point.snapshots[0]
  const label = String(point.id).startsWith('run:')
    ? `${snapshot.job_name || 'Backup run'} — ${snapshot.time ? new Date(snapshot.time).toLocaleString() : 'unknown time'} — ${point.snapshots.length} component(s) — ${String(point.id).slice(-8)}`
    : snapshotLabel(snapshot)
  return { value: point.id, label }
}))
const componentOptions = computed(() => (selectedPoint.value?.snapshots || []).map(snapshot => ({ value: snapshot.id, label: snapshotLabel(snapshot) })))
const sourceReady = computed(() => canLoadSnapshots.value && !loadingSnapshots.value
  && agentOptions.value.some(agent => agent.value === selectedAgentId.value)
  && repositoryOptions.value.some(repository => repository.value === selectedRepositoryId.value)
  && Boolean(selectedSnapshot.value)
  && modeOptions.value.some(mode => mode.value === selectedMode.value))
const selectionReady = computed(() => !isFileMode.value || (selectedMode.value === 'proxmox_files'
  ? Boolean(proxmoxSelection.value.session_id && proxmoxSelection.value.volume && proxmoxSelection.value.include_paths?.length) && !proxmoxPanel.value?.busy
  : includePaths.value.length > 0))
const canGoNext = computed(() => sourceReady.value && (activeSection.value === 'source' || selectionReady.value))
const canSubmit = computed(() => sourceReady.value && selectionReady.value
  && (isFileMode.value ? Boolean(restoreLocation.value.trim()) : Boolean(proxmoxPanel.value?.canSubmit))
  && (!isFileMode.value || overwritePolicy.value !== 'overwrite' || overwriteConfirmed.value))
const overwriteOptions = [
  { label: 'Fail if selected files already exist (recommended)', value: 'fail_if_exists' },
  { label: 'Overwrite existing files', value: 'overwrite' },
]

function canOpenSection(name) {
  return name === 'source' || (sourceReady.value && (name === 'selection' || selectionReady.value))
}

function confirmDeletePoint() {
  if (!selectedPoint.value || !supportsRestorePoints.value || submitting.value) return
  const point = selectedPoint.value
  const agentId = selectedAgentId.value, repositoryId = selectedRepositoryId.value
  $q.dialog({ title: 'Delete restore point',
    message: `Permanently delete ${pointOptions.value.find(option => option.value === point.id)?.label}? This removes all ${point.snapshots.length} snapshot(s) in this restore point. It cannot be undone.`,
    cancel: true, ok: { label: 'Delete', color: 'negative', noCaps: true },
  }).onOk(async () => {
    if (submitting.value || !dialogVisible.value || agentId !== selectedAgentId.value
      || repositoryId !== selectedRepositoryId.value || point.id !== selectedPoint.value?.id) return
    submitting.value = true
    try {
      const result = await restoreStore.deleteRestorePoint({ agentId, repositoryId,
        snapshotId: point.snapshots[0].id, snapshotIds: point.snapshots.map(snapshot => snapshot.id) })
      // Use the existing operation page for progress, dispatch uncertainty and partial failures.
      emit('started', { ...result, agent_id: agentId })
      dialogVisible.value = false
    } catch (e) {
      if (!shouldIgnoreApiError(e)) $q.notify({ message: getApiErrorMessage(e), color: 'negative' })
    } finally {
      submitting.value = false
    }
  })
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
  showAllRestorePoints.value = false
  restoreForm.value?.resetValidation()
  loadingSnapshots.value = false
  selectedAgentId.value = props.agents.find(agent => agent.id === (props.agentId || props.job?.agent_id) && agent.online)?.id || props.agents.find(agent => agent.online)?.id || null
  selectedRepositoryId.value = assignedRepositories.value[0]?.id || null
  selectedMode.value = props.job?.type === 'proxmox' ? 'proxmox_vm' : 'plain_file'
  selectedSnapshotId.value = null
  snapshots.value = []
  includePaths.value = []
  restoreLocation.value = '/tmp/drastic-restore'
  showTargetBrowserDialog.value = false
  overwritePolicy.value = 'fail_if_exists'
  overwriteConfirmed.value = false
  advancedOpen.value = false
  ownershipByName.value = false
  skipXattrs.value = false
}

async function loadSnapshots() {
  const request = ++snapshotsRequest
  snapshots.value = []
  selectedSnapshotId.value = null
  if (!canLoadSnapshots.value) { loadingSnapshots.value = false; return }
  activeSection.value = 'source'
  restoreForm.value?.resetValidation()
  loadingSnapshots.value = true
  try {
    const result = await restoreStore.getSnapshots({ jobId: sourceJobId.value, agentId: selectedAgentId.value, repositoryId: selectedRepositoryId.value })
    if (request !== snapshotsRequest || !dialogVisible.value) return
    snapshots.value = result
    selectedSnapshotId.value = restorePoints.value[0]?.snapshots[0]?.id || null
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
      ...(selectedMode.value === 'plain_file' && supportsRestoreMetadataOptions.value
        ? { ownership_by_name: ownershipByName.value, skip_xattrs: skipXattrs.value } : {}),
    })
    proxmoxPanel.value?.handOff()
    emit('started', { ...result, agent_id: selectedAgentId.value, job_id: sourceJobId.value || null })
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
  const paths = !guest && !dataset ? (snapshot.paths || []).join(', ') : ''
  return `${dataset ? `${dataset} — ` : guest}${paths ? `${paths} — ` : ''}${time} — ${id}`
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
  overwriteConfirmed.value = false
})

watch(selectedMode, () => {
  ownershipByName.value = false
  skipXattrs.value = false
})

watch(selectedSnapshotId, () => {
  selectedMode.value = selectedSnapshot.value?.proxmox_archive && !selectedSnapshot.value?.restore_error ? 'proxmox_vm' : 'plain_file'
})

watch(selectedAgentId, () => {
  ownershipByName.value = false
  skipXattrs.value = false
  if (!assignedRepositories.value.some(repo => repo.id === selectedRepositoryId.value)) {
    selectedRepositoryId.value = assignedRepositories.value[0]?.id || null
  }
})

watch([dialogVisible, selectedAgentId, selectedRepositoryId, showAllRestorePoints], async () => {
  if (!dialogVisible.value) return
  includePaths.value = []
  await loadSnapshots()
})

watch(overwritePolicy, () => {
  overwriteConfirmed.value = false
})

defineOptions({ name: 'RestoreDialog' })
</script>
