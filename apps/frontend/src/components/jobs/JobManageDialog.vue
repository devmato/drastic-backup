<template>
  <q-dialog v-model="dialogVisible" persistent :maximized="$q.screen.lt.sm">
    <q-card class="app-dialog-wide db-job-form-dialog column no-wrap overflow-hidden">
      <q-card-section class="col-auto"><div class="text-h6">{{ editingJob ? 'Edit Job' : 'Add Job' }}</div></q-card-section>
      <q-form class="column no-wrap col" @submit="submitForm">
        <q-card-section class="col-shrink scroll q-pt-none">
          <div class="row q-col-gutter-md">
            <div class="col-12 col-sm-auto">
              <q-list dense separator>
                <q-item
                  v-for="section in sections"
                  :key="section.name"
                  clickable
                  dense
                  :active="activeSection === section.name"
                  :active-class="$q.dark.isActive ? 'bg-grey-9 text-blue-3 text-weight-medium' : 'bg-grey-2 text-primary text-weight-medium'"
                  :disable="section.requiresEntries && !entriesConfigured"
                  @click="activeSection = section.name"
                >
                  <q-item-section side><q-icon :name="section.icon" size="xs" /></q-item-section>
                  <q-item-section>{{ section.label }}</q-item-section>
                </q-item>
              </q-list>
            </div>

            <div class="col-12 col-sm">
              <q-tab-panels v-model="activeSection" class="bg-transparent">
                <q-tab-panel name="general" class="q-pa-none">
                  <div class="q-gutter-sm">
                    <q-input outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model="jobForm.name" label="Job Name" :rules="[val => !!val?.trim() || 'Required']" />
                    <q-select outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model="jobForm.type" :options="jobTypeOptions" label="Job Type" emit-value map-options :disable="!!editingJob" :rules="[val => !!val || 'Required']" />
                    <q-banner v-if="!supportsJob(selectedAgent, jobForm.type)" class="bg-warning text-black">This job requires a configured connection and its local prerequisites. Existing job settings are retained.</q-banner>
                    <q-btn flat no-caps dense icon="settings" label="Configure connections" :to="`/agents/${agentId}?tab=connections`" target="_blank">
                      <q-tooltip>Opens in a new tab; your job draft stays here.</q-tooltip>
                    </q-btn>
                  </div>
                </q-tab-panel>

                <q-tab-panel name="entries" class="q-pa-none">
                  <component
                    :is="currentTypeComponent"
                    v-if="currentTypeComponent"
                    v-model="jobForm.config"
                    :agent-id="agentId"
                    :agent-online="agentOnline"
                  />
                </q-tab-panel>

                <q-tab-panel name="actions" class="q-pa-none">
                  <JobActionsPanel
                    v-model="draftActions"
                    :disabled="!entriesConfigured || !agentOnline"
                  />
                </q-tab-panel>

                <q-tab-panel name="schedules" class="q-pa-none">
                  <JobSchedulesPanel
                    v-model="draftSchedules"
                    :repositories="dialogRepositories"
                    :all-repositories="allRepositories"
                    :disabled="!entriesConfigured"
                  />
                </q-tab-panel>
              </q-tab-panels>
            </div>
          </div>
        </q-card-section>
        <q-card-actions align="right" class="col-auto q-mt-auto q-px-md q-py-sm">
          <q-btn flat no-caps label="Cancel" :disable="submitting" @click="dialogVisible = false" />
          <q-btn v-if="previousSection" flat no-caps icon="chevron_left" label="Prev" type="button" color="primary" :disable="submitting" @click="goPrevious" />
          <q-btn v-if="nextSection" flat no-caps label="Next" icon-right="chevron_right" type="button" color="primary" :disable="!canGoNext || submitting" @click="goNext" />
          <q-btn unelevated no-caps label="Save" type="submit" color="primary" :loading="submitting" :disable="submitting" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import FileBackupJobForm from 'components/jobs/forms/FileBackupJobForm.vue'
import ProxmoxBackupJobForm from 'components/jobs/forms/ProxmoxBackupJobForm.vue'
import TrueNASBackupJobForm from 'components/jobs/forms/TrueNASBackupJobForm.vue'
import { useAgentStore } from 'stores/agent'
import { supportsJob } from 'src/utils/agent-connections'
import JobActionsPanel from 'components/jobs/panels/JobActionsPanel.vue'
import JobSchedulesPanel from 'components/jobs/panels/JobSchedulesPanel.vue'

const $q = useQuasar()
const agentStore = useAgentStore()

const props = defineProps({
  editingJob: { type: Object, default: null },
  agentId: { type: [Number, String], default: null },
  agentOnline: { type: Boolean, default: false },
  repositories: { type: Array, default: () => [] },
  allRepositories: { type: Array, default: () => [] },
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits(['save'])

const sections = [
  { name: 'general', label: 'General', icon: 'settings' },
  { name: 'entries', label: 'Entries', icon: 'description' },
  { name: 'actions', label: 'Commands', icon: 'terminal', requiresEntries: true },
  { name: 'schedules', label: 'Schedule', icon: 'event', requiresEntries: true },
]

const selectedAgent = computed(() => agentStore.agents.find(agent => String(agent.id) === String(props.agentId)))
const jobTypeOptions = computed(() => [
  { label: 'File-Backup', value: 'file' },
  { label: 'Proxmox-Backup', value: 'proxmox' },
  { label: 'TrueNAS-Backup', value: 'truenas' },
].filter(option => supportsJob(selectedAgent.value, option.value) || props.editingJob?.type === option.value))

const jobForm = reactive(createEmptyJobForm())
const activeSection = ref('general')
const draftActions = ref([])
const draftSchedules = ref([])

const dialogVisible = defineModel({ type: Boolean, required: true })

const activeSectionIndex = computed(() => sections.findIndex(section => section.name === activeSection.value))

const previousSection = computed(() => sections[activeSectionIndex.value - 1] || null)

const nextSection = computed(() => sections[activeSectionIndex.value + 1] || null)

const canLeaveGeneral = computed(() => {
  return Boolean(jobForm.name.trim() && jobForm.type)
})

const canGoNext = computed(() => {
  if (!nextSection.value) {
    return false
  }

  if (activeSection.value === 'general') {
    return canLeaveGeneral.value
  }

  if (activeSection.value === 'entries') {
    return entriesConfigured.value
  }

  return !nextSection.value.requiresEntries || entriesConfigured.value
})

const currentTypeComponent = computed(() => {
  if (jobForm.type === 'truenas') return TrueNASBackupJobForm
  if (jobForm.type === 'file') {
    return FileBackupJobForm
  }

  if (jobForm.type === 'proxmox') {
    return ProxmoxBackupJobForm
  }

  return null
})

const dialogRepositories = computed(() => props.repositories)
const allRepositories = computed(() => props.allRepositories)

const entriesConfigured = computed(() => {
  if (jobForm.type === 'truenas') return (jobForm.config?.datasets || []).length > 0
  if (jobForm.type === 'file') {
    return (jobForm.config?.paths || []).length > 0
  }

  if (jobForm.type === 'proxmox') {
    return jobForm.config?.selection_mode !== 'include' || (jobForm.config?.guest_ids || []).length > 0
  }

  return false
})

watch(
  dialogVisible,
  value => {
    if (value) {
      resetForm()
    }
  }
)

watch(
  () => jobForm.type,
  type => {
    if (!props.editingJob) {
      jobForm.config = cloneConfig(type)
    }
  }
)

watch(entriesConfigured, value => {
  if (!value && ['actions', 'schedules'].includes(activeSection.value)) {
    activeSection.value = 'entries'
  }
})

function createEmptyJobForm() {
  return {
    name: '',
    type: 'file',
    config: {
      paths: [],
      exclude_patterns: [],
    },
  }
}

function cloneFileConfig(config = {}) {
  return {
    paths: (config.paths || []).map(path => ({ ...path })),
    exclude_patterns: (config.exclude_patterns || []).map(pattern => ({ ...pattern })),
  }
}

function cloneProxmoxConfig(config = {}) {
  return {
    selection_mode: config.selection_mode || 'all',
    guest_ids: [...(config.guest_ids || [])],
    exclude_guest_ids: [...(config.exclude_guest_ids || [])],
  }
}

function cloneAction(action) {
  return {
    ...action,
    data: { ...(action.data || {}) },
  }
}

function cloneSchedule(schedule) {
  const dayOfWeek = schedule.day_of_week || parseDayOfWeek(schedule.cron_string)
  const cronParts = schedule.cron_string?.split(' ') || []

  return {
    ...schedule,
    repository_id: schedule.repository_id,
    retention_id: schedule.retention_id || null,
    hour: schedule.hour || cronParts[1] || '0',
    minute: schedule.minute || cronParts[0] || '0',
    day_of_week: [...dayOfWeek],
    config: cloneScheduleConfig(schedule.config),
  }
}

function cloneScheduleConfig(config = {}) {
  return {
    repository_check: {
      enabled: Boolean(config.repository_check?.enabled),
      read_data: config.repository_check?.read_data || null,
    },
  }
}

function parseDayOfWeek(cronString) {
  const dowPart = cronString?.split(' ')[4]
  return dowPart === '*' || !dowPart ? [0, 1, 2, 3, 4, 5, 6] : dowPart.split(',').map(Number)
}

function cloneConfig(type, config = {}) {
  if (type === 'truenas') return {
    datasets: [...(config.datasets || [])],
    include_children: config.include_children ?? !props.editingJob,
    exclude_patterns: [...(config.exclude_patterns || [])],
  }
  if (type === 'proxmox') {
    return cloneProxmoxConfig(config)
  }

  return cloneFileConfig(config)
}

function resetForm() {
  const form = createEmptyJobForm()
  activeSection.value = 'general'

  if (props.editingJob) {
    form.name = props.editingJob.name
    form.type = props.editingJob.type || 'file'
    form.config = cloneConfig(form.type, props.editingJob.config)
    draftActions.value = props.editingJob.actions.map(cloneAction)
    draftSchedules.value = props.editingJob.schedules.map(cloneSchedule)
  } else if (form.type === 'proxmox') {
    form.config = cloneProxmoxConfig()
    draftActions.value = []
    draftSchedules.value = []
  } else {
    draftActions.value = []
    draftSchedules.value = []
  }

  jobForm.name = form.name
  jobForm.type = form.type
  jobForm.config = form.config
}

function normalizeScheduleRepositoryId(schedule) {
  return schedule.repository_id
}

function goNext() {
  if (!canGoNext.value || !nextSection.value) {
    return
  }

  activeSection.value = nextSection.value.name
}

function goPrevious() {
  if (!previousSection.value) {
    return
  }

  activeSection.value = previousSection.value.name
}

function submitForm() {
  if (!canLeaveGeneral.value) {
    activeSection.value = 'general'
    $q.notify({ message: 'Enter a job name and select a job type', color: 'red', position: 'top' })
    return
  }

  if (jobForm.type === 'truenas' && !(jobForm.config?.datasets || []).length) {
    $q.notify({ message: 'Select at least one TrueNAS dataset', color: 'negative' })
    return
  }
  if (jobForm.type === 'file' && (jobForm.config?.paths || []).length === 0) {
    $q.notify({ message: 'Select at least one include path', color: 'red', position: 'top' })
    return
  }

  if (jobForm.type === 'proxmox' && jobForm.config?.selection_mode === 'include' && (jobForm.config?.guest_ids || []).length === 0) {
    $q.notify({ message: 'Select at least one Proxmox guest', color: 'red', position: 'top' })
    return
  }

  const payload = {
    name: jobForm.name,
    config: cloneConfig(jobForm.type, jobForm.config),
    actions: draftActions.value.map(action => ({
      id: action.id,
      module: action.module,
      hook: action.hook,
      data: { ...(action.data || {}) },
    })),
    schedules: draftSchedules.value.map(schedule => ({
      hour: schedule.hour,
      minute: schedule.minute,
      day_of_week: [...(schedule.day_of_week || [])],
      repository_id: normalizeScheduleRepositoryId(schedule),
      retention_id: schedule.retention_id || null,
      enabled: schedule.enabled,
      id: schedule.id,
      config: cloneScheduleConfig(schedule.config),
    })),
  }

  if (!props.editingJob) {
    payload.type = jobForm.type
  }

  emit('save', payload)
}

defineOptions({ name: 'JobManageDialog' })
</script>
