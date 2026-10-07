<template>
  <q-dialog v-model="dialogVisible" persistent>
    <q-card class="db-dialog-card-md">
      <q-card-section>
        <div class="text-h6">{{ isEdit ? 'Edit Schedule' : 'Add Schedule' }}</div>
      </q-card-section>

      <q-form @submit="submitForm">
        <q-card-section>
          <div class="row q-col-gutter-md">
            <div class="col-12"><q-select v-model="form.type" :options="typeOptions" label="Schedule Type" emit-value map-options outlined dense /></div>
            <div v-if="form.type === 'once'" class="col-12"><q-input v-model="form.date" type="date" label="Date" outlined dense stack-label hide-bottom-space :rules="[value => !!value || 'Required']" /></div>
            <div v-if="['monthly', 'yearly'].includes(form.type)" class="col-12"><q-input v-model.number="form.day" type="number" label="Day of Month" outlined dense hide-bottom-space :rules="[value => validInt(value, 1, 31) || 'Use 1–31']" /></div>
            <div v-if="form.type === 'yearly'" class="col-12">
              <div class="text-caption">Months</div>
              <div class="row"><q-checkbox v-for="month in monthOptions" :key="month.value" v-model="form.months" :val="month.value" :label="month.label" dense class="q-mr-md q-mb-sm" /></div>
            </div>
            <template v-if="form.type === 'periodic'">
              <div class="col-6"><q-input v-model.number="form.interval" type="number" label="Every (minutes)" outlined dense hide-bottom-space :rules="[value => validInt(value, 1, 525600) || 'Use 1–525600']" /></div>
              <div class="col-6"><q-input v-model.number="form.offset" type="number" label="Offset (minutes)" outlined dense hide-bottom-space :rules="[value => validInt(value, 0, form.interval - 1) || 'Use 0 up to interval minus 1']" /></div>
            </template>
            <template v-else-if="form.type !== 'cron'">
              <div v-if="form.type !== 'hourly'" class="col-6"><q-input v-model.number="form.hour" type="number" label="Hour" outlined dense hide-bottom-space :rules="[value => validInt(value, 0, 23) || 'Use 0–23']" /></div>
              <div :class="form.type === 'hourly' ? 'col-12' : 'col-6'"><q-input v-model.number="form.minute" type="number" label="Minute" outlined dense hide-bottom-space :rules="[value => validInt(value, 0, 59) || 'Use 0–59']" /></div>
            </template>
            <div v-else class="col-12 text-body2">Existing schedule: {{ form.expression }}</div>
            <div v-if="form.type === 'hourly'" class="col-12"><q-checkbox v-model="form.restrict_days" label="Limit weekdays" dense /></div>
            <div v-if="form.type === 'weekly' || (form.type === 'hourly' && form.restrict_days)" class="col-12">
              <div class="text-caption">Weekdays</div>
              <div class="row"><q-checkbox v-for="day in dayOptions" :key="day.value" v-model="form.weekdays" :val="day.value" :label="day.label" dense class="q-mr-md q-mb-sm" /></div>
            </div>
            <div v-if="form.type === 'hourly'" class="col-12"><q-checkbox v-model="form.restrict_hours" label="Limit hours" dense /></div>
            <template v-if="form.type === 'hourly' && form.restrict_hours">
              <div class="col-6"><q-input v-model.number="form.from_hour" type="number" label="From hour (inclusive)" outlined dense hide-bottom-space :rules="[value => validInt(value, 0, 23) || 'Use 0–23']" /></div>
              <div class="col-6"><q-input v-model.number="form.to_hour" type="number" label="Through hour (inclusive)" outlined dense hide-bottom-space :rules="[value => validInt(value, 0, 23) || 'Use 0–23']" /></div>
            </template>
            <div class="col-12 text-caption" role="status">
              <div>{{ timezone || 'Agent local time' }}</div>
              <div>{{ previewLoading ? 'Calculating next execution…' : previewError || `Next execution: ${previewData?.next_run_text || 'None (date passed or no matching date)'}` }}</div>
            </div>
            <template v-if="!timeOnly">
              <div class="col-12"><q-separator /></div>
              <div class="col-12"><q-checkbox v-model="showAllRepositories" dense label="Show all repositories" /></div>
              <div class="col-12"><q-select outlined dense hide-bottom-space v-model="form.repository_id" :options="repositoryOptions" label="Repository" emit-value map-options :rules="[val => !!val || 'Required']" /></div>
              <div class="col-12"><q-select outlined dense v-model="form.retention_id" :options="retentionOptions" label="Retention Policy" emit-value map-options /></div>
              <div class="col-12"><q-toggle v-model="form.repository_check.enabled" label="Run repository check after successful backup" /></div>
              <div v-if="form.repository_check.enabled" class="col-12"><q-input outlined dense v-model="form.repository_check.read_data" label="Read data" hint="Optional: 1/10, 5% or 100%. Leave empty for a basic check." /></div>
            </template>
            <div class="col-12"><q-toggle v-model="form.enabled" label="Enabled" /></div>
          </div>
        </q-card-section>

        <q-card-actions align="right" class="q-pa-md">
          <q-btn flat no-caps label="Cancel" v-close-popup />
          <q-btn unelevated no-caps label="Save" type="submit" color="primary" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { getApiErrorMessage } from 'src/utils/api-error'
import { describeTiming, scheduleTiming } from 'src/utils/schedule'
import { api } from 'boot/axios'

const props = defineProps({
  schedule: { type: Object, default: null },
  repositories: { type: Array, default: () => [] },
  allRepositories: { type: Array, default: () => [] },
  retentions: { type: Array, default: () => [] },
  timeOnly: { type: Boolean, default: false },
  timezone: { type: String, default: '' },
  agentId: { type: [Number, String], default: null },
})

const emit = defineEmits(['submit'])

const $q = useQuasar()

const showAllRepositories = ref(false)
const form = reactive({
  type: 'daily', hour: 0, minute: 0, weekdays: [0, 1, 2, 3, 4, 5, 6], day: 1, months: [1],
  date: '', interval: 60, offset: 0, restrict_days: false, restrict_hours: false, from_hour: 0, to_hour: 23, expression: '',
  repository_id: null,
  retention_id: null,
  enabled: true,
  repository_check: { enabled: false, read_data: null },
})

const dayOptions = [
  { label: 'Sun', value: 0 }, { label: 'Mon', value: 1 }, { label: 'Tue', value: 2 },
  { label: 'Wed', value: 3 }, { label: 'Thu', value: 4 }, { label: 'Fri', value: 5 },
  { label: 'Sat', value: 6 },
]
const monthOptions = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'].map((label, index) => ({ label, value: index + 1 }))
const typeOptions = computed(() => [
  ...['Hourly', 'Daily', 'Weekly', 'Monthly', 'Yearly'].map(label => ({ label, value: label.toLowerCase() })),
  { label: 'Once', value: 'once' }, { label: 'Periodic', value: 'periodic' },
  ...(form.type === 'cron' ? [{ label: 'Existing schedule', value: 'cron' }] : []),
])
const validInt = (value, min, max) => value !== null && value !== '' && Number.isInteger(Number(value)) && Number(value) >= min && Number(value) <= max
const timing = computed(() => {
  const value = { type: form.type }
  if (!['periodic', 'cron'].includes(form.type)) value.minute = Number(form.minute)
  if (['daily', 'weekly', 'monthly', 'yearly', 'once'].includes(form.type)) value.hour = Number(form.hour)
  if (['monthly', 'yearly'].includes(form.type)) value.day = Number(form.day)
  if (form.type === 'yearly') value.months = [...form.months]
  if (form.type === 'weekly' || (form.type === 'hourly' && form.restrict_days)) value.weekdays = [...form.weekdays]
  if (form.type === 'hourly' && form.restrict_hours) Object.assign(value, { from_hour: Number(form.from_hour), to_hour: Number(form.to_hour) })
  if (form.type === 'once') value.date = form.date
  if (form.type === 'periodic') Object.assign(value, { interval: Number(form.interval), offset: Number(form.offset) })
  if (form.type === 'cron') value.expression = form.expression
  return value
})
const previewData = ref(null)
const previewError = ref('')
const previewLoading = ref(false)
let previewTimer
let previewRequest = 0

const isEdit = computed(() => Boolean(props.schedule))

const selectableRepositories = computed(() => showAllRepositories.value ? props.allRepositories : props.repositories)
const repositoryOptions = computed(() => selectableRepositories.value.map(repository => ({
  label: getRepositoryLabel(repository),
  value: repository.id,
})))
const retentionOptions = computed(() => [
  { label: 'None', value: null },
  ...props.retentions.map(retention => ({ label: retention.name, value: retention.id })),
])

const dialogVisible = defineModel({ type: Boolean, required: true })

function getRepositoryLabel(repository) {
  const name = repository.repository_name || repository.name
  const location = repository.repository_location || repository.location
  return location ? `${name} (${location})` : name
}

function resetForm() {
  const schedule = props.schedule
  const savedTiming = scheduleTiming(schedule || {})
  Object.assign(form, { type: 'daily', hour: 0, minute: 0, weekdays: [0, 1, 2, 3, 4, 5, 6], day: 1, months: [1],
    date: '', interval: 60, offset: 0, from_hour: 0, to_hour: 23, expression: '', ...savedTiming,
    restrict_days: Boolean(savedTiming.weekdays?.length), restrict_hours: savedTiming.from_hour !== undefined })
  previewData.value = null
  previewError.value = ''
  showAllRepositories.value = false
  if (schedule) {
    const assignedRepositoryIds = new Set(props.repositories.map(repository => repository.id))
    showAllRepositories.value = !assignedRepositoryIds.has(schedule.repository_id)
    form.repository_id = schedule.repository_id
    form.retention_id = schedule.retention_id || null
    form.enabled = schedule.enabled
    form.repository_check = cloneRepositoryCheck(schedule.config?.repository_check)
    return
  }

  form.repository_id = repositoryOptions.value.length > 0 ? repositoryOptions.value[0].value : null
  form.retention_id = null
  form.enabled = true
  form.repository_check = cloneRepositoryCheck()
}

function cloneRepositoryCheck(config = {}) {
  return {
    enabled: Boolean(config.enabled),
    read_data: config.read_data || null,
  }
}

function submitForm() {
  try {
    if (timing.value.weekdays && !timing.value.weekdays.length) throw new Error('Select at least one weekday')
    if (form.type === 'yearly' && !form.months.length) throw new Error('Select at least one month')

    emit('submit', {
      timing: timing.value,
      cron_description: describeTiming(timing.value),
      enabled: form.enabled,
      ...(!props.timeOnly ? {
        repository_id: form.repository_id,
        retention_id: form.retention_id,
        config: { repository_check: cloneRepositoryCheck(form.repository_check) },
      } : {}),
    })
  } catch (e) {
    $q.notify({ message: getApiErrorMessage(e, e.message || 'Error'), color: 'red', position: 'top' })
  }
}

watch(dialogVisible, value => {
  if (value) resetForm()
})

watch(() => props.schedule, () => {
  if (dialogVisible.value) resetForm()
})

watch(repositoryOptions, (options) => {
  if (!dialogVisible.value || props.timeOnly) return
  if (options.some(option => option.value === form.repository_id)) return
  form.repository_id = options[0]?.value || null
})

watch([dialogVisible, timing, () => props.agentId], () => {
  clearTimeout(previewTimer)
  const request = ++previewRequest
  if (!dialogVisible.value) return
  previewLoading.value = true
  previewData.value = null
  previewError.value = ''
  previewTimer = setTimeout(async () => {
    try {
      const { data } = await api.post('/jobs/schedules/preview', { timing: timing.value, agent_id: props.timeOnly ? null : Number(props.agentId) })
      if (request === previewRequest) previewData.value = data
    } catch (error) {
      if (request === previewRequest) previewError.value = getApiErrorMessage(error, 'Preview unavailable')
    } finally {
      if (request === previewRequest) previewLoading.value = false
    }
  }, 300)
}, { deep: true })
onBeforeUnmount(() => { clearTimeout(previewTimer); previewRequest++ })

defineOptions({ name: 'ScheduleManageDialog' })
</script>
