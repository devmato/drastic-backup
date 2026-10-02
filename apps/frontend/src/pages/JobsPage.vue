<template>
  <q-page class="q-pa-md">
    <div class="text-h5 q-mb-md">Backup Jobs</div>

    <div v-if="jobStore.agentJobs.length === 0 && !jobStore.loading" class="q-pa-md">
      <q-banner class="bg-grey-2">
        <template v-slot:avatar><q-icon name="info" color="primary" /></template>
        No jobs configured. Start by adding an <router-link to="/agents">agent</router-link> to configure a new job.
      </q-banner>
    </div>

    <q-inner-loading :showing="jobStore.loading" />

    <div v-for="agentData in jobStore.agentJobs" :key="agentData.id" class="q-mb-lg">
      <q-card flat bordered>
        <q-card-section>
          <div class="row items-center">
            <q-icon :name="agentData.online ? 'desktop_windows' : 'desktop_access_disabled'" :color="agentData.online ? 'green' : 'red'" size="sm" class="q-mr-sm" />
            <span class="text-h6">{{ agentData.hostname }}</span>
          </div>
        </q-card-section>

        <q-card-section v-if="agentData.jobs.length === 0">
          No backup jobs configured
        </q-card-section>

        <q-table
          v-if="agentData.jobs.length > 0"
          :rows="agentData.jobs"
          :columns="jobColumns"
          row-key="id"
          flat
          :rows-per-page-options="[0]"
          hide-bottom
        >
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
              <span v-if="props.row.last_operation" class="row items-center no-wrap q-gutter-x-sm cursor-pointer text-primary" @click="openLatestOperation(props.row)">
                <q-spinner v-if="props.row.last_operation.state === 'running'" color="blue" size="sm" />
                <q-icon v-else-if="props.row.last_operation.state === 'success'" name="check_circle" color="green" />
                <q-icon v-else-if="props.row.last_operation.state === 'warning'" name="warning" color="orange" />
                <q-icon v-else-if="props.row.last_operation.state === 'failed'" name="error" color="red" />
                <span>{{ formatDate(props.row.last_operation.started) }}</span>
                <q-tooltip>Show latest operation</q-tooltip>
              </span>
              <span v-else class="text-grey">Never</span>
            </q-td>
          </template>
          <template v-slot:body-cell-actions="props">
            <q-td :props="props">
              <q-btn flat dense icon="receipt_long" color="primary" @click="openJobReports(props.row)">
                <q-tooltip>Protocol history</q-tooltip>
              </q-btn>
              <q-btn v-if="props.row.last_operation && props.row.last_operation.state === 'running'" flat dense icon="cancel" color="red" @click="confirmCancel(props.row)">
                <q-tooltip>Cancel job</q-tooltip>
              </q-btn>
              <q-btn v-else-if="props.row.agent_online" flat dense icon="play_arrow" color="blue" @click="showRunDialog(props.row)">
                <q-tooltip>Run job</q-tooltip>
              </q-btn>
              <q-btn flat dense icon="restore" color="purple" aria-label="Restore backup" @click="showRestore(props.row)">
                <q-tooltip>Restore</q-tooltip>
              </q-btn>
              <q-btn v-if="props.row.agent_online" flat dense icon="edit" @click="showEditJob(props.row)">
                <q-tooltip>Edit job</q-tooltip>
              </q-btn>
              <q-btn flat dense icon="delete" color="red" @click="confirmDeleteJob(props.row)">
                <q-tooltip>Delete job</q-tooltip>
              </q-btn>
            </q-td>
          </template>
        </q-table>

        <q-card-actions>
          <q-space />
          <q-btn :disable="!agentData.online" color="primary" icon="add" label="Add job" @click="showAddJob(agentData)" />
        </q-card-actions>
      </q-card>
    </div>

    <JobManageDialog
      v-model="showJobDialog"
      :editing-job="editingJob"
      :agent-id="currentAgentId"
      :agent-online="currentAgentOnline"
      :repositories="currentAgentRepositories"
      :all-repositories="repositoryStore.repositories"
      :submitting="jobDialogSubmitting"
      @save="onJobSubmit"
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
            <q-select outlined v-model="runJobRepoId" :options="runRepoOptions" label="Target Repository" emit-value map-options :rules="[val => !!val || 'Required']" />
          </q-card-section>
          <q-card-actions align="right">
            <q-btn flat label="Cancel" v-close-popup />
            <q-btn label="Start Backup" type="submit" color="primary" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

  </q-page>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useQuasar } from 'quasar'
import { useJobStore } from 'stores/job'
import { useAgentStore } from 'stores/agent'
import { useRepositoryStore } from 'stores/repository'
import { useUserStore } from 'stores/user'
import { useOperationStore } from 'stores/operation'
import { subscribeToSocketEvents } from 'src/utils/socket'
import JobManageDialog from 'components/jobs/JobManageDialog.vue'
import RestoreDialog from 'components/restore/RestoreDialog.vue'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const router = useRouter()
const $q = useQuasar()
const jobStore = useJobStore()
const agentStore = useAgentStore()
const repositoryStore = useRepositoryStore()
const userStore = useUserStore()
const operationStore = useOperationStore()

const showJobDialog = ref(false)
const editingJob = ref(null)
const currentAgentId = ref(null)
const currentAgentOnline = ref(false)
const currentAgentRepositories = ref([])
const jobDialogSubmitting = ref(false)

const showRunJobDialog = ref(false)
const showRestoreDialog = ref(false)
const runJobId = ref(null)
const runJobRepoId = ref(null)
const runShowAllRepositories = ref(false)
const runDefaultRepositories = ref([])
const restoringJob = ref(null)
const restoringRepositories = ref([])

let stopJobsSocketListener = null

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
  const targetIds = [...new Set((job.schedules || []).map(schedule => schedule.repository_id).filter(Boolean))]
  return targetIds.map(repositoryId => byId.get(repositoryId)).filter(Boolean)
}

async function onJobSubmit(data) {
  jobDialogSubmitting.value = true
  try {
    const relatedConfig = {
      actions: data.actions || [],
      schedules: data.schedules || [],
    }
    delete data.actions
    delete data.schedules

    if (editingJob.value) {
      await jobStore.updateJob(editingJob.value.id, data)
      const updatedJob = await reloadJob(editingJob.value.id)
      await syncJobRelatedConfig(updatedJob, relatedConfig)
      $q.notify({ message: 'Job updated', color: 'green', position: 'top' })
    } else {
      data.agent_id = currentAgentId.value
      const createdJob = await jobStore.createJob(data)
      const updatedJob = await reloadJob(createdJob.id)
      await syncJobRelatedConfig(updatedJob, relatedConfig)
      $q.notify({ message: 'Job created', color: 'green', position: 'top' })
    }
    await jobStore.loadJobs()
    await agentStore.loadAgents()
    showJobDialog.value = false
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  } finally {
    jobDialogSubmitting.value = false
  }
}

async function reloadJob(jobId) {
  return jobStore.getJob(jobId)
}

function mapSchedulePayload(schedule, recoveryKey = userStore.recoveryKey) {
  return {
    hour: schedule.hour,
    minute: schedule.minute,
    day_of_week: schedule.day_of_week,
    repository_id: schedule.repository_id,
    retention_id: schedule.retention_id || null,
    enabled: schedule.enabled,
    config: schedule.config || {},
    recovery_key: recoveryKey,
  }
}

async function createScheduleWithRecovery(jobId, schedule) {
  await userStore.withRecoveryKey(recoveryKey => jobStore.createSchedule(jobId, mapSchedulePayload(schedule, recoveryKey)))
}

async function updateScheduleWithRecovery(scheduleId, schedule) {
  await userStore.withRecoveryKey(recoveryKey => jobStore.updateSchedule(scheduleId, mapSchedulePayload(schedule, recoveryKey)))
}

async function syncJobRelatedConfig(job, config) {
  if (!editingJob.value) {
    for (const action of config.actions) {
      await jobStore.createAction(job.id, action)
    }

    for (const schedule of config.schedules) {
      await createScheduleWithRecovery(job.id, schedule)
    }

    return
  }

  const scheduleIds = new Set(config.schedules.map(schedule => schedule.id))
  const actionIds = new Set(config.actions.map(action => action.id))

  for (const action of config.actions) {
    if (String(action.id).startsWith('draft-')) {
      await jobStore.createAction(job.id, action)
    } else {
      await jobStore.updateAction(action.id, action)
    }
  }

  for (const action of job.actions) {
    if (!actionIds.has(action.id)) {
      await jobStore.deleteAction(action.id)
    }
  }

  for (const schedule of config.schedules) {
    if (String(schedule.id).startsWith('draft-')) {
      await createScheduleWithRecovery(job.id, schedule)
    } else {
      await updateScheduleWithRecovery(schedule.id, schedule)
    }
  }

  for (const schedule of job.schedules) {
    if (!scheduleIds.has(schedule.id)) {
      await jobStore.deleteSchedule(schedule.id)
    }
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

function openLatestOperation(job) {
  if (!job.last_operation) return
  router.push(`/agents/${job.agent_id}/operations/${job.last_operation.id}`)
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
    await jobStore.deleteJob(job.id)
    $q.notify({ message: 'Job deleted', color: 'green', position: 'top' })
  })
}

onMounted(async () => {
  await agentStore.loadAgents()
  await repositoryStore.loadRepositories()
  await jobStore.loadJobs()

  stopJobsSocketListener = await subscribeToSocketEvents(async (payload) => {
    const eventName = payload?.name || ''

    if (eventName.startsWith('jobstate')) {
      await jobStore.loadJobs()
    }
  })
})

watch(runRepoOptions, (options) => {
  if (!showRunJobDialog.value) return
  if (options.some(option => option.value === runJobRepoId.value)) return
  runJobRepoId.value = options[0]?.value || null
})

onBeforeUnmount(() => {
  if (stopJobsSocketListener) {
    stopJobsSocketListener()
  }
})

defineOptions({ name: 'JobsPage' })
</script>
