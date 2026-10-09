<template>
  <q-page class="q-pa-md">
    <PageHeader title="Backup Jobs" description="Configure, run and monitor backups for each agent." />

    <q-tabs v-model="activeTab" align="left" active-color="primary" indicator-color="primary" no-caps class="q-mb-md">
      <q-tab name="jobs" label="Jobs" icon="backup" />
      <q-tab name="chains" label="Backup Chains" icon="playlist_play" />
    </q-tabs>
    <BackupChainsPanel v-if="activeTab === 'chains'" :agent-jobs="jobStore.agentJobs" :agents="agentStore.agents" :repositories="repositoryStore.repositories" @changed="jobStore.loadJobs()" />

    <template v-if="activeTab === 'jobs'">

    <div class="row items-center q-col-gutter-sm q-mb-md">
      <div class="col-12 col-sm-6 col-md-3">
        <q-select v-model="lastState" :options="stateOptions" label="Latest backup status" outlined :dense="!$q.platform.has.touch" clearable emit-value map-options />
      </div>
      <div v-if="lastState" class="col-12 col-sm">
        <div class="row items-center q-gutter-sm">
          <span class="text-body2">{{ visibleAgentJobs.reduce((total, agent) => total + agent.jobs.length, 0) }} matching jobs</span>
          <q-btn flat no-caps color="primary" label="Clear filter" @click="lastState = null" />
        </div>
      </div>
    </div>

    <q-card v-if="!lastState && jobStore.agentJobs.length === 0 && !jobStore.loading" flat bordered>
      <EmptyState icon="backup" title="No agents available for backups" description="Connect an agent first, then add a backup job for that system.">
        <q-btn outline no-caps no-wrap color="primary" label="View Agents" to="/agents" />
      </EmptyState>
    </q-card>

    <q-card v-if="lastState && visibleAgentJobs.length === 0 && !jobStore.loading" flat bordered>
      <EmptyState icon="filter_list" title="No matching backup jobs" description="No jobs currently have this latest backup status.">
        <q-btn outline no-caps color="primary" label="Show All Jobs" @click="lastState = null" />
      </EmptyState>
    </q-card>

    <q-inner-loading :showing="jobStore.loading" />

    <div v-for="agentData in visibleAgentJobs" :key="agentData.id" class="q-mb-md">
      <q-card flat bordered>
        <q-card-section class="q-py-sm">
          <div class="row items-center q-col-gutter-md">
            <div class="col-12 col-sm">
              <div class="row items-center no-wrap">
                <q-icon :name="agentData.online ? 'desktop_windows' : 'desktop_access_disabled'" :color="agentData.online ? 'positive' : 'negative'" size="sm" class="q-mr-sm" />
                <h2 class="db-page-title text-subtitle1 text-weight-medium q-my-none">{{ agentData.display_name }}</h2>
              </div>
            </div>
            <div class="col-12 col-sm-auto">
              <q-btn unelevated no-caps no-wrap :disable="!agentData.online" color="primary" icon="add" label="Add Job" @click="showAddJob(agentData)" />
            </div>
          </div>
        </q-card-section>
        <q-separator />

        <q-card-section v-if="agentData.jobs.length === 0" class="q-py-sm" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">No backup jobs configured yet.</q-card-section>

        <q-table
          v-if="agentData.jobs.length > 0"
          :rows="agentData.jobs"
          :columns="jobColumns"
          :table-header-class="$q.dark.isActive ? 'bg-dark text-grey-4' : 'bg-grey-1 text-grey-7'"
          row-key="id"
          flat
          :rows-per-page-options="[0]"
          hide-bottom
        >
          <template v-slot:body-cell-name="props">
            <q-td :props="props">
              <div>{{ props.row.name }}</div>
              <div v-for="chain in props.row.chains || []" :key="chain.id">
                <q-btn flat dense no-caps size="sm" color="primary" icon="playlist_play" :label="`${chain.name} · ${chain.position}/${chain.step_count}`" @click="openChain(chain.id)" />
              </div>
            </q-td>
          </template>
          <template v-slot:body-cell-type="props">
            <q-td :props="props">
              <q-badge color="primary" :label="props.row.type_text || props.row.type" />
            </q-td>
          </template>
          <template v-slot:body-cell-targets="props">
            <q-td :props="props">
              <q-badge v-for="repo in getJobTargetRepositories(props.row)" :key="repo.id" class="q-mr-xs" color="grey-7" :label="repo.name" />
              <span v-if="getJobTargetRepositories(props.row).length === 0" class="text-grey">No schedules</span>
            </q-td>
          </template>
          <template v-slot:body-cell-last_run="props">
            <q-td :props="props">
              <router-link v-if="props.row.last_operation" :to="`/agents/${props.row.agent_id}/operations/${props.row.last_operation.id}`" class="row items-center no-wrap q-gutter-x-sm text-primary" style="text-decoration: none" aria-label="Show latest backup operation">
                <q-spinner v-if="props.row.last_operation.state === 'running'" :color="stateColor(props.row.last_operation.state)" size="sm" />
                <q-icon v-else :name="backupStates.find(state => state.value === props.row.last_operation.state)?.icon || 'help_outline'" :color="stateColor(props.row.last_operation.state)" />
                <span>{{ formatDate(props.row.last_operation.started) }}</span>
                <q-tooltip>Show latest operation</q-tooltip>
              </router-link>
              <span v-else class="text-grey">Never</span>
            </q-td>
          </template>
          <template v-slot:body-cell-actions="props">
            <q-td :props="props">
              <TableActionButton icon="receipt_long" label="Protocol history" @click="openJobReports(props.row)" />
              <TableActionButton v-if="props.row.last_operation && props.row.last_operation.state === 'running'" icon="cancel" label="Cancel job" color="negative" @click="confirmCancel(props.row)" />
              <TableActionButton v-else-if="props.row.agent_online" icon="play_arrow" label="Run job" @click="showRunDialog(props.row)" />
              <TableActionButton icon="restore" label="Restore backup" @click="showRestore(props.row)" />
              <TableActionButton v-if="props.row.agent_online" icon="edit" label="Edit job" @click="showEditJob(props.row)" />
              <TableActionButton icon="delete" label="Delete job" color="negative" @click="confirmDeleteJob(props.row)" />
            </q-td>
          </template>
        </q-table>

      </q-card>
    </div>

    </template>

    <JobManageDialog
      v-model="showJobDialog"
      :editing-job="editingJob"
      :agent-id="currentAgentId"
      :agent-online="currentAgentOnline"
      :repositories="currentAgentRepositories"
      :all-repositories="repositoryStore.repositories"
      :submitting="jobDialogSubmitting"
      @save="onJobSubmit"
      @open-chain="openChain"
    />

    <RestoreDialog
      v-model="showRestoreDialog"
      :job="restoringJob"
      :agents="agentStore.agents"
      :repositories="restoringRepositories"
      @started="onRestoreStarted"
    />

    <!-- Run Job Dialog -->
    <q-dialog v-model="showRunJobDialog">
      <q-card class="db-dialog-card-sm">
        <q-card-section><div class="text-h6">Run Backup Job</div></q-card-section>
        <q-form @submit="onRunSubmit">
          <q-card-section class="q-gutter-sm">
            <q-checkbox v-model="runShowAllRepositories" dense label="Show all repositories" />
            <q-select outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model="runJobRepoId" :options="runRepoOptions" label="Target Repository" emit-value map-options :rules="[val => !!val || 'Required']" />
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Cancel" v-close-popup />
            <q-btn unelevated no-caps label="Start Backup" type="submit" color="primary" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

  </q-page>
</template>

<script setup>
import PageHeader from 'components/PageHeader.vue'
import EmptyState from 'components/EmptyState.vue'
import TableActionButton from 'components/TableActionButton.vue'
import { ref, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuasar } from 'quasar'
import { useJobStore } from 'stores/job'
import { useAgentStore } from 'stores/agent'
import { useRepositoryStore } from 'stores/repository'
import { useUserStore } from 'stores/user'
import { useOperationStore } from 'stores/operation'
import JobManageDialog from 'components/jobs/JobManageDialog.vue'
import BackupChainsPanel from 'components/jobs/BackupChainsPanel.vue'
import RestoreDialog from 'components/restore/RestoreDialog.vue'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import { useJobEditor } from 'src/composables/useJobEditor'
import { backupStates, filterAgentJobs, getBackupStateColor as stateColor } from 'src/utils/backup-results'

const router = useRouter()
const route = useRoute()
const $q = useQuasar()
const jobStore = useJobStore()
const agentStore = useAgentStore()
const repositoryStore = useRepositoryStore()
const userStore = useUserStore()
const operationStore = useOperationStore()
const activeTab = computed({
  get: () => route.query.tab === 'chains' ? 'chains' : 'jobs',
  set: tab => router.push({ query: { ...route.query, tab: tab === 'chains' ? 'chains' : undefined, chain_id: undefined } }),
})

function openChain(id) {
  showJobDialog.value = false
  router.push({ query: { ...route.query, tab: 'chains', chain_id: String(id) } })
}

const stateOptions = [...backupStates, { value: 'attention', label: 'Warnings / failures' }]
const lastState = computed({
  get: () => stateOptions.some(state => state.value === route.query.last_state) ? route.query.last_state : null,
  set: value => router.push({ query: { ...route.query, last_state: value || undefined } }),
})
const visibleAgentJobs = computed(() => filterAgentJobs(jobStore.agentJobs, lastState.value))

const showJobDialog = ref(false)
const editingJob = ref(null)
const currentAgentId = ref(null)
const currentAgentOnline = ref(false)
const currentAgentRepositories = ref([])
const { submitting: jobDialogSubmitting, saveJob } = useJobEditor(jobStore, agentStore, userStore)

const showRunJobDialog = ref(false)
const showRestoreDialog = ref(false)
const runJobId = ref(null)
const runJobRepoId = ref(null)
const runShowAllRepositories = ref(false)
const runDefaultRepositories = ref([])
const restoringJob = ref(null)
const restoringRepositories = ref([])

const runRepoOptions = computed(() => (runShowAllRepositories.value ? repositoryStore.repositories : runDefaultRepositories.value).map(repository => ({
  label: `${repository.name} (${repository.location})`,
  value: repository.id,
})))

const jobColumns = [
  { name: 'name', label: 'Name', field: 'name', align: 'left', sortable: true, style: 'width: 14%', headerStyle: 'width: 14%' },
  { name: 'type', label: 'Type', field: 'type_text', align: 'left', sortable: true, style: 'width: 16%', headerStyle: 'width: 16%' },
  { name: 'targets', label: 'Targets', field: 'schedules', align: 'left', style: 'width: 22%', headerStyle: 'width: 22%' },
  { name: 'last_run', label: 'Last Run', field: 'last_operation', align: 'left', style: 'width: 24%', headerStyle: 'width: 24%' },
  { name: 'actions', label: '', field: 'id', align: 'right', style: 'width: 24%', headerStyle: 'width: 24%' },
]

function formatDate(isoStr) {
  if (!isoStr) return '-'
  return new Date(isoStr).toLocaleString()
}

function showAddJob(agentData) {
  editingJob.value = null
  currentAgentId.value = agentData.id
  currentAgentOnline.value = agentData.online
  currentAgentRepositories.value = getAgentRepositories(agentData)
  showJobDialog.value = true
}

function showEditJob(job) {
  const agentData = getAgentForJob(job)
  editingJob.value = job
  currentAgentId.value = job.agent_id
  currentAgentOnline.value = job.agent_online
  currentAgentRepositories.value = getAgentRepositories(agentData)
  showJobDialog.value = true
}

function getAgentForJob(job) {
  return jobStore.agentJobs.find(agentData => agentData.id === job.agent_id)
}

function getAgentRepositories(agentData) {
  return agentData?.repositories || []
}

function getJobTargetRepositories(job) {
  const byId = new Map(repositoryStore.repositories.map(repository => [repository.id, repository]))
  const targetIds = [...new Set([...(job.schedules || []), ...(job.chains || [])].map(schedule => schedule.repository_id).filter(Boolean))]
  return targetIds.map(repositoryId => byId.get(repositoryId)).filter(Boolean)
}

async function onJobSubmit(data) {
  try {
    await saveJob(data, editingJob.value, currentAgentId.value)
    $q.notify({ message: editingJob.value ? 'Job updated' : 'Job created', color: 'green', position: 'top' })
    showJobDialog.value = false
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, e.message || 'Could not save job'), color: 'red', position: 'top' })
  }
}

function showRunDialog(job) {
  const agentData = getAgentForJob(job)
  runJobId.value = job.id
  runShowAllRepositories.value = false
  runDefaultRepositories.value = getAgentRepositories(agentData)
  runJobRepoId.value = runDefaultRepositories.value[0]?.id || null
  showRunJobDialog.value = true
}

function showRestore(job) {
  const agentData = getAgentForJob(job)
  restoringJob.value = job
  restoringRepositories.value = agentData?.repositories || []
  showRestoreDialog.value = true
}

function onRestoreStarted(result) {
  $q.notify({ message: result.msg || 'Restore started', color: 'green', position: 'top' })
  router.push(`/agents/${result.agent_id}/operations?type=restore&job_id=${result.job_id}`)
}

function openJobReports(job) {
  router.push(`/agents/${job.agent_id}/operations?type=backup&job_id=${job.id}`)
}

async function onRunSubmit() {
  try {
    await userStore.withRecoveryKey(recoveryKey => operationStore.startBackupJob({
        jobId: runJobId.value,
        repositoryId: runJobRepoId.value,
        recoveryKey,
      }))
    showRunJobDialog.value = false
    $q.notify({ message: 'Backup job started', color: 'green', position: 'top' })
    await jobStore.loadJobs()
    await agentStore.loadAgents()
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  }
}

function confirmCancel(job) {
  $q.dialog({
    title: 'Cancel Job',
    message: 'Cancel running backup job?',
    cancel: true,
  }).onOk(async () => {
    await jobStore.cancelJob(job.id)
    $q.notify({ message: 'Job canceled', color: 'green', position: 'top' })
  })
}

function confirmDeleteJob(job) {
  $q.dialog({
    title: 'Delete Job',
    message: `Delete backup job "${job.name}"?`,
    cancel: true,
  }).onOk(async () => {
    try {
      await jobStore.deleteJob(job.id)
      $q.notify({ message: 'Job deleted', color: 'green', position: 'top' })
    } catch (error) {
      if (shouldIgnoreApiError(error)) return
      $q.notify({ message: getApiErrorMessage(error), color: 'negative', position: 'top' })
    }
  })
}

onMounted(async () => {
  await agentStore.loadAgents()
  await repositoryStore.loadRepositories()
  await jobStore.loadJobs()

  if (typeof route.query.add_for_agent === 'string') {
    const agent = jobStore.agentJobs.find(agent => String(agent.id) === route.query.add_for_agent && agent.online)
    if (agent) showAddJob(agent)
    await router.replace({ query: { ...route.query, add_for_agent: undefined } })
  }
})

watch(runRepoOptions, (options) => {
  if (!showRunJobDialog.value) return
  if (options.some(option => option.value === runJobRepoId.value)) return
  runJobRepoId.value = options[0]?.value || null
})

defineOptions({ name: 'JobsPage' })
</script>
