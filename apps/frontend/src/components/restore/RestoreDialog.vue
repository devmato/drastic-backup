<template>
  <q-dialog v-model="dialogVisible" persistent :maximized="$q.screen.lt.md">
    <q-card class="app-dialog-wide">
      <q-card-section class="row items-start q-col-gutter-sm q-pa-lg">
        <div class="col">
          <div class="text-h6">Restore {{ job?.name }}</div>
          <div class="text-caption text-grey-7">Restore selected files or directories to a local path on the target agent.</div>
        </div>
      </q-card-section>

      <q-form @submit="submitRestore">
        <q-card-section class="q-px-lg q-pt-none q-pb-md">
          <div class="row q-col-gutter-sm items-start">
            <div class="col-12 col-md-4">
              <q-select outlined v-model="selectedAgentId" :options="agentOptions" label="Restore agent" emit-value map-options :rules="[val => !!val || 'Required']" />
            </div>
            <div class="col-12 col-md-4">
              <q-select outlined v-model="selectedRepositoryId" :options="repositoryOptions" label="Source repository" emit-value map-options :rules="[val => !!val || 'Required']" />
            </div>
            <div class="col-12 col-md-4">
              <q-select outlined v-model="selectedMode" :options="modeOptions" label="Restore mode" emit-value map-options :rules="[val => !!val || 'Required']" disable />
            </div>
          </div>

          <q-select
            class="q-mt-md"
            outlined
            v-model="selectedSnapshotId"
            :options="snapshotOptions"
            label="Snapshot"
            emit-value
            map-options
            :loading="loadingSnapshots"
            :disable="!canLoadSnapshots"
            :rules="[val => !!val || 'Required']"
          >
            <template v-slot:after>
              <q-btn flat dense icon="refresh" :disable="!canLoadSnapshots" :loading="loadingSnapshots" @click="loadSnapshots">
                <q-tooltip>Reload snapshots</q-tooltip>
              </q-btn>
            </template>
          </q-select>

          <div class="q-mt-md">
            <PathSelectionPanel
              v-if="selectedSnapshotId"
              mode="include-only"
              :load-entries="loadSnapshotEntries"
              v-model:selected-paths="includePaths"
              :reload-key="selectedSnapshotId"
              browser-title="Snapshot Browser"
              selected-title="Selected Restore Paths"
              selected-caption="Restore"
              empty-selected-label="No restore paths selected yet."
              height="320px"
              empty-label="No entries found."
              error-message="Could not load snapshot entries"
            />
            <q-banner v-else class="bg-grey-2 text-grey-8">Select a snapshot to browse files.</q-banner>
          </div>

          <q-input class="q-mt-md" outlined v-model="restoreLocation" label="Restore location on target agent" :rules="[val => !!val || 'Required']">
            <template v-slot:after>
              <q-btn flat dense icon="folder_open" :disable="!selectedAgentId" @click="showTargetBrowserDialog = true">
                <q-tooltip>Browse target agent directories</q-tooltip>
              </q-btn>
            </template>
          </q-input>

          <q-option-group
            class="q-mt-md"
            v-model="overwritePolicy"
            type="radio"
            :options="overwriteOptions"
          />
          <q-checkbox
            v-if="overwritePolicy === 'overwrite'"
            v-model="overwriteConfirmed"
            label="I understand that existing files at the restore destination may be replaced"
          />
          <q-banner v-if="isCrossAgentRestore" class="q-mt-md bg-orange-1 text-orange-10">
            Cross-agent restore: this snapshot was created by a different agent.
            <q-checkbox v-model="crossAgentConfirmed" label="Restore it to the selected agent" />
          </q-banner>
        </q-card-section>

        <q-card-actions class="q-px-lg q-pb-lg q-pt-none" align="right">
          <q-btn flat label="Cancel" :disable="submitting" v-close-popup />
          <q-btn label="Start Restore" type="submit" color="primary" :loading="submitting" :disable="!canSubmit" />
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

const dialogVisible = defineModel({ type: Boolean, required: true })

const agentOptions = computed(() => props.agents.filter(agent => agent.online).map(agent => ({ label: agent.hostname || `Agent #${agent.id}`, value: agent.id })))
const repositoryOptions = computed(() => props.repositories.map(repo => ({ label: `${repo.name} (${repo.location})`, value: repo.id })))
const modeOptions = [{ label: 'Plain file restore', value: 'plain_file' }]
const canLoadSnapshots = computed(() => Boolean(props.job?.id && selectedAgentId.value && selectedRepositoryId.value))
const snapshotOptions = computed(() => snapshots.value.map(snapshot => ({ label: snapshotLabel(snapshot), value: snapshot.id })))
const isCrossAgentRestore = computed(() => Boolean(selectedAgentId.value && props.job?.agent_id && selectedAgentId.value !== props.job.agent_id))
const canSubmit = computed(() => includePaths.value.length > 0
  && (overwritePolicy.value !== 'overwrite' || overwriteConfirmed.value)
  && (!isCrossAgentRestore.value || crossAgentConfirmed.value))
const overwriteOptions = [
  { label: 'Fail if selected files already exist (recommended)', value: 'fail_if_exists' },
  { label: 'Overwrite existing files', value: 'overwrite' },
]

function resetDialog() {
  selectedAgentId.value = props.job?.agent_id || null
  selectedRepositoryId.value = props.repositories[0]?.id || null
  selectedMode.value = 'plain_file'
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
  if (!canLoadSnapshots.value) return
  loadingSnapshots.value = true
  try {
    snapshots.value = await restoreStore.getSnapshots({ jobId: props.job.id, agentId: selectedAgentId.value, repositoryId: selectedRepositoryId.value })
    selectedSnapshotId.value = snapshots.value[0]?.id || null
  } catch (e) {
    snapshots.value = []
    selectedSnapshotId.value = null
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Could not load snapshots'), color: 'red', position: 'top' })
  } finally {
    loadingSnapshots.value = false
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
  submitting.value = true
  try {
    const result = await operationStore.startRestore({
      job_id: props.job.id,
      agent_id: selectedAgentId.value,
      repository_id: selectedRepositoryId.value,
      mode: selectedMode.value,
      snapshot_id: selectedSnapshotId.value,
      restore_location: restoreLocation.value,
      include_paths: includePaths.value,
      overwrite_policy: overwritePolicy.value,
    })
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
  return `${id} - ${time}`
}

watch(dialogVisible, async value => {
  if (!value) return
  resetDialog()
  await loadSnapshots()
})

watch(selectedSnapshotId, () => {
  includePaths.value = []
})

watch([selectedAgentId, selectedRepositoryId], async () => {
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
