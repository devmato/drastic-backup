<template>
  <q-dialog v-model="dialogVisible" persistent>
    <q-card class="db-dialog-card-md">
      <q-card-section>
        <div class="text-h6">{{ isEdit ? 'Edit Schedule' : 'Add Schedule' }}</div>
      </q-card-section>

      <q-form @submit="submitForm">
        <q-card-section class="q-gutter-sm">
          <q-input outlined dense v-model="form.hour" label="Hour" :rules="[val => val !== '' || 'Required', val => /^(?:[01]?\d|2[0-3])$/.test(String(val)) || 'Hour must be between 0 and 23']" />
          <q-input outlined dense v-model="form.minute" label="Minute" :rules="[val => val !== '' || 'Required', val => /^(?:[0-5]?\d)$/.test(String(val)) || 'Minute must be between 0 and 59']" />
          <q-select outlined dense v-model="form.day_of_week" :options="dayOptions" label="Days of week" multiple emit-value map-options :rules="[val => Array.isArray(val) && val.length > 0 || 'Select at least one day']" />
          <q-checkbox v-model="showAllRepositories" dense label="Show all repositories" />
          <q-select outlined dense v-model="form.repository_id" :options="repositoryOptions" label="Repository" emit-value map-options :rules="[val => !!val || 'Required']" />
          <q-select outlined dense v-model="form.retention_id" :options="retentionOptions" label="Retention Policy" emit-value map-options />
          <q-separator />
          <q-toggle v-model="form.repository_check.enabled" label="Run repository check after successful backup" />
          <q-input
            v-if="form.repository_check.enabled"
            outlined
            dense
            v-model="form.repository_check.read_data"
            label="Read data"
            hint="Optional for basic check. Use e.g. 1/10, 5% or 100% for full data check."
          />
          <q-toggle v-model="form.enabled" label="Enabled" />
        </q-card-section>

        <q-card-actions align="right">
          <q-btn flat label="Cancel" v-close-popup />
          <q-btn label="Save" type="submit" color="primary" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { getApiErrorMessage } from 'src/utils/api-error'

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  schedule: { type: Object, default: null },
  repositories: { type: Array, default: () => [] },
  allRepositories: { type: Array, default: () => [] },
  retentions: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:modelValue', 'submit'])

const $q = useQuasar()

const showAllRepositories = ref(false)
const form = reactive({
  hour: '0',
  minute: '0',
  day_of_week: [0, 1, 2, 3, 4, 5, 6],
  repository_id: null,
  retention_id: null,
  enabled: true,
  repository_check: { enabled: false, read_data: null },
})

const dayOptions = [
  { label: 'Sunday', value: 0 }, { label: 'Monday', value: 1 }, { label: 'Tuesday', value: 2 },
  { label: 'Wednesday', value: 3 }, { label: 'Thursday', value: 4 }, { label: 'Friday', value: 5 },
  { label: 'Saturday', value: 6 },
]

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

const dialogVisible = computed({
  get: () => props.modelValue,
  set: value => emit('update:modelValue', value),
})

function getRepositoryLabel(repository) {
  const name = repository.repository_name || repository.name
  const location = repository.repository_location || repository.location
  return location ? `${name} (${location})` : name
}

function parseDayOfWeek(cronString) {
  const dowPart = cronString?.split(' ')[4]
  return dowPart === '*' || !dowPart ? [0, 1, 2, 3, 4, 5, 6] : dowPart.split(',').map(Number)
}

function resetForm() {
  const schedule = props.schedule
  showAllRepositories.value = false
  if (schedule) {
    const assignedRepositoryIds = new Set(props.repositories.map(repository => repository.id))
    showAllRepositories.value = !assignedRepositoryIds.has(schedule.repository_id)
    const parts = schedule.cron_string?.split(' ') || []
    form.hour = schedule.hour || parts[1] || '0'
    form.minute = schedule.minute || parts[0] || '0'
    form.day_of_week = [...(schedule.day_of_week || parseDayOfWeek(schedule.cron_string))]
    form.repository_id = schedule.repository_id
    form.retention_id = schedule.retention_id || null
    form.enabled = schedule.enabled
    form.repository_check = cloneRepositoryCheck(schedule.config?.repository_check)
    return
  }

  form.hour = '0'
  form.minute = '0'
  form.day_of_week = [0, 1, 2, 3, 4, 5, 6]
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
    if (new Set(form.day_of_week).size !== form.day_of_week.length) {
      throw new Error('Duplicate weekdays are not allowed')
    }

    emit('submit', {
      hour: form.hour,
      minute: form.minute,
      day_of_week: [...form.day_of_week],
      repository_id: form.repository_id,
      retention_id: form.retention_id,
      enabled: form.enabled,
      config: {
        repository_check: cloneRepositoryCheck(form.repository_check),
      },
    })
  } catch (e) {
    $q.notify({ message: getApiErrorMessage(e, e.message || 'Error'), color: 'red', position: 'top' })
  }
}

watch(() => props.modelValue, value => {
  if (value) resetForm()
})

watch(() => props.schedule, () => {
  if (props.modelValue) resetForm()
})

watch(repositoryOptions, (options) => {
  if (!props.modelValue) return
  if (options.some(option => option.value === form.repository_id)) return
  form.repository_id = options[0]?.value || null
})

defineOptions({ name: 'JobScheduleManageDialog' })
</script>
