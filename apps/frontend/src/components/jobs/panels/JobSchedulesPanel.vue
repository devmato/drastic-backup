<template>
  <div>
    <q-table
      :rows="modelSchedules"
      :columns="scheduleColumns"
      row-key="id"
      flat
      dense
      :rows-per-page-options="[0]"
      hide-bottom
      no-data-label="No schedules configured"
    >
      <template v-slot:body-cell-enabled="props">
        <q-td :props="props">
          <q-icon :name="props.value ? 'check_circle' : 'cancel'" :color="props.value ? 'green' : 'red'" />
        </q-td>
      </template>
      <template v-slot:body-cell-actions="props">
        <q-td :props="props">
          <q-btn flat dense icon="edit" @click="showScheduleDialog(props.row)" />
          <q-btn flat dense icon="delete" color="red" @click="confirmDeleteSchedule(props.row)" />
        </q-td>
      </template>
    </q-table>

    <div v-if="modelSchedules.length === 0" class="text-grey q-pa-sm">No schedules configured</div>
    <div class="q-mt-sm">
      <q-btn :disable="disabled || !hasAnyRepositories" color="green" icon="add" label="Add schedule" size="sm" @click="showScheduleDialog(null)" />
    </div>

    <JobScheduleManageDialog
      v-model="scheduleDialogVisible"
      :schedule="editingSchedule"
      :repositories="repositories"
      :all-repositories="allRepositories"
      :retentions="retentionStore.retentions"
      @submit="onScheduleSubmit"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRetentionStore } from 'stores/retention'
import JobScheduleManageDialog from 'components/jobs/JobScheduleManageDialog.vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  repositories: { type: Array, default: () => [] },
  allRepositories: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const $q = useQuasar()
const retentionStore = useRetentionStore()

const scheduleDialogVisible = ref(false)
const editingSchedule = ref(null)

const modelSchedules = computed(() => props.modelValue)

const scheduleColumns = [
  { name: 'enabled', label: 'Enabled', field: 'enabled', align: 'left' },
  { name: 'cron_description', label: 'Description', field: row => row.cron_description || getCronDescription(row), align: 'left' },
  { name: 'cron_string', label: 'Cron', field: row => row.cron_string || getCronString(row), align: 'left' },
  { name: 'repository_name', label: 'Repository', field: row => row.repository_name || getRepositoryName(row.repository_id), align: 'left' },
  { name: 'retention_name', label: 'Retention', field: row => row.retention_name || getRetentionName(row.retention_id), align: 'left' },
  { name: 'repository_check', label: 'Check', field: getRepositoryCheckLabel, align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

const hasAnyRepositories = computed(() => props.allRepositories.length > 0)

function getRepositoryName(repositoryId) {
  const repository = props.allRepositories.find(item => item.id === repositoryId) || props.repositories.find(item => item.id === repositoryId)
  return repository?.repository_name || repository?.name || '-'
}

function getRetentionName(retentionId) {
  if (!retentionId) return 'None'
  return retentionStore.retentions.find(retention => retention.id === retentionId)?.name || '-'
}

function getRepositoryCheckLabel(schedule) {
  const repositoryCheck = schedule.config?.repository_check
  if (!repositoryCheck?.enabled) return 'Off'
  return repositoryCheck.read_data ? `Read ${repositoryCheck.read_data}` : 'Basic'
}

function getCronString(schedule) {
  const dowStr = schedule.day_of_week?.length === 7 ? '*' : [...(schedule.day_of_week || [])].join(',')
  return `${schedule.minute} ${schedule.hour} * * ${dowStr}`
}

function getCronDescription(schedule) {
  return `At ${String(schedule.hour).padStart(2, '0')}:${String(schedule.minute).padStart(2, '0')}`
}

function cloneSchedule(schedule) {
  return {
    ...schedule,
    repository_id: schedule.repository_id,
    retention_id: schedule.retention_id || null,
    day_of_week: [...(schedule.day_of_week || parseDayOfWeek(schedule.cron_string))],
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

function showScheduleDialog(schedule) {
  editingSchedule.value = schedule
  scheduleDialogVisible.value = true
}

function onScheduleSubmit(data) {
  if (editingSchedule.value) {
    emit('update:modelValue', props.modelValue.map(schedule => schedule.id === editingSchedule.value.id ? { ...cloneSchedule(schedule), ...data } : cloneSchedule(schedule)))
  } else {
    emit('update:modelValue', [
      ...props.modelValue.map(cloneSchedule),
      { id: `draft-${Date.now()}`, ...data },
    ])
  }

  scheduleDialogVisible.value = false
}

function confirmDeleteSchedule(schedule) {
  $q.dialog({ title: 'Delete Schedule', message: 'Delete this schedule?', cancel: true }).onOk(() => {
    emit('update:modelValue', props.modelValue.filter(item => item.id !== schedule.id).map(cloneSchedule))
  })
}

onMounted(() => retentionStore.loadRetentions())

defineOptions({ name: 'JobSchedulesPanel' })
</script>
