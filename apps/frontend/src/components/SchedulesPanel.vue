<template>
  <div>
    <q-table
      :rows="modelSchedules"
      :columns="scheduleColumns"
      :table-header-class="$q.dark.isActive ? 'bg-dark text-grey-4' : 'bg-grey-1 text-grey-7'"
      row-key="id"
      flat
      :wrap-cells="timeOnly"
      :rows-per-page-options="[0]"
      hide-bottom
      no-data-label="No schedules configured"
    >
      <template v-slot:body-cell-enabled="props">
        <q-td :props="props">
          <q-icon :name="props.value ? 'check_circle' : 'cancel'" :color="props.value ? 'green' : 'red'" role="img" aria-hidden="false" :aria-label="props.value ? 'Enabled' : 'Disabled'" />
        </q-td>
      </template>
      <template v-slot:body-cell-actions="props">
        <q-td :props="props">
          <TableActionButton icon="edit" label="Edit schedule" @click="showScheduleDialog(props.row)" />
          <TableActionButton icon="delete" label="Delete schedule" color="negative" @click="confirmDeleteSchedule(props.row)" />
        </q-td>
      </template>
    </q-table>

    <div class="row justify-end q-mt-md">
      <q-btn unelevated no-caps no-wrap :disable="disabled" color="primary" icon="add" label="Add Schedule" @click="showScheduleDialog(null)" />
    </div>

    <template v-if="chains.length">
      <div class="text-subtitle1 q-mt-lg q-mb-sm">Backup Chains</div>
      <q-list bordered separator>
        <q-item v-for="chain in chains" :key="chain.id">
          <q-item-section>
            <q-item-label>{{ chain.name }} <q-badge :color="chain.enabled ? 'positive' : 'grey'" text-color="black" :label="chain.enabled ? 'Scheduled' : 'Manual only'" /></q-item-label>
            <q-item-label caption>Step {{ chain.position }} of {{ chain.step_count }}{{ chain.previous_job_name ? ` · after ${chain.previous_job_name}` : '' }}</q-item-label>
            <q-item-label v-for="(schedule, index) in chain.schedules" :key="index" caption>Chain starts: {{ schedule.cron_description }} UTC{{ schedule.enabled ? '' : ' · Disabled' }}</q-item-label>
            <q-item-label caption>This job starts when its turn arrives.</q-item-label>
            <q-item-label caption>{{ getRepositoryName(chain.repository_id) }} · Retention: {{ getRetentionName(chain.retention_id) }}</q-item-label>
          </q-item-section>
          <q-item-section side><q-btn flat no-caps color="primary" label="Open Chain" @click="emit('open-chain', chain.id)" /></q-item-section>
        </q-item>
      </q-list>
    </template>

    <ScheduleManageDialog
      v-model="scheduleDialogVisible"
      :schedule="editingSchedule"
      :repositories="repositories"
      :all-repositories="allRepositories"
      :retentions="retentionStore.retentions"
      :time-only="timeOnly"
      :timezone="timezone"
      :agent-id="agentId"
      @submit="onScheduleSubmit"
    />
  </div>
</template>

<script setup>
import TableActionButton from 'components/TableActionButton.vue'
import { computed, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRetentionStore } from 'stores/retention'
import ScheduleManageDialog from 'components/ScheduleManageDialog.vue'
import { describeTiming, scheduleTiming } from 'src/utils/schedule'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  repositories: { type: Array, default: () => [] },
  allRepositories: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  chains: { type: Array, default: () => [] },
  timeOnly: { type: Boolean, default: false },
  timezone: { type: String, default: '' },
  agentId: { type: [Number, String], default: null },
})

const emit = defineEmits(['update:modelValue', 'open-chain'])

const $q = useQuasar()
const retentionStore = useRetentionStore()

const scheduleDialogVisible = ref(false)
const editingSchedule = ref(null)

const modelSchedules = computed(() => props.modelValue)

const scheduleColumns = computed(() => [
  { name: 'enabled', label: 'Enabled', field: 'enabled', align: 'left' },
  { name: 'cron_description', label: props.timezone ? `Description (${props.timezone})` : 'Description', field: row => row.cron_description || getCronDescription(row), align: 'left' },
  ...(!props.timeOnly ? [
    { name: 'repository_name', label: 'Repository', field: row => row.repository_name || getRepositoryName(row.repository_id), align: 'left' },
    { name: 'retention_name', label: 'Retention', field: row => row.retention_name || getRetentionName(row.retention_id), align: 'left' },
    { name: 'repository_check', label: 'Check', field: getRepositoryCheckLabel, align: 'left' },
  ] : []),
  { name: 'actions', label: '', field: 'id', align: 'right' },
])

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

function getCronDescription(schedule) {
  return describeTiming(scheduleTiming(schedule))
}

function cloneSchedule(schedule) {
  return {
    ...schedule,
    timing: scheduleTiming(schedule),
    repository_id: schedule.repository_id,
    retention_id: schedule.retention_id || null,
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

function showScheduleDialog(schedule) {
  editingSchedule.value = schedule
  scheduleDialogVisible.value = true
}

function onScheduleSubmit(data) {
  if (editingSchedule.value) {
    const updated = { ...cloneSchedule(editingSchedule.value), ...data }
    delete updated.cron_string
    if (!data.cron_description) delete updated.cron_description
    emit('update:modelValue', props.modelValue.map(schedule => schedule.id === editingSchedule.value.id ? updated : cloneSchedule(schedule)))
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

onMounted(() => {
  if (!props.timeOnly) retentionStore.loadRetentions()
})

defineOptions({ name: 'SchedulesPanel' })
</script>
