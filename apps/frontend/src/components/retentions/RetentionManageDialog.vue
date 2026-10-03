<template>
  <q-dialog v-model="dialogVisible" persistent>
    <q-card class="db-dialog-card-md">
      <q-card-section>
        <div class="text-h6">{{ isEdit ? 'Edit Retention Policy' : 'Add Retention Policy' }}</div>
      </q-card-section>

      <q-form @submit="submitForm">
        <q-card-section class="q-gutter-sm">
          <q-input outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model="form.name" label="Name" :rules="[val => !!val || 'Required']" />
          <q-btn-toggle v-model="form.rtype" :options="typeOptions" class="q-mb-sm" />

          <q-input v-if="form.rtype === 'count'" outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model.number="form.keep_last" label="Keep last snapshots" type="number" :rules="[val => val >= 1 || 'Must be at least 1']" />

          <template v-if="form.rtype === 'date'">
            <q-input outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model.number="form.keep_hourly" label="Keep hourly" type="number" :rules="[val => val >= 0 || 'Must be >= 0']" />
            <q-input outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model.number="form.keep_weekly" label="Keep weekly" type="number" :rules="[val => val >= 0 || 'Must be >= 0']" />
            <q-input outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model.number="form.keep_monthly" label="Keep monthly" type="number" :rules="[val => val >= 0 || 'Must be >= 0']" />
            <q-input outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model.number="form.keep_yearly" label="Keep yearly" type="number" :rules="[val => val >= 0 || 'Must be >= 0']" />
          </template>
        </q-card-section>

        <q-card-actions align="right" class="q-pa-md">
          <q-btn flat no-caps label="Cancel" :disable="submitting" v-close-popup />
          <q-btn unelevated no-caps label="Save" type="submit" color="primary" :loading="submitting" :disable="submitting" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { useQuasar } from 'quasar'

const props = defineProps({
  retention: { type: Object, default: null },
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits(['submit'])

const $q = useQuasar()

const typeOptions = [
  { label: 'Count', value: 'count' },
  { label: 'Date', value: 'date' },
]

const form = reactive({
  name: '',
  rtype: 'count',
  keep_last: 1,
  keep_hourly: 0,
  keep_weekly: 0,
  keep_monthly: 0,
  keep_yearly: 0,
})

const isEdit = computed(() => Boolean(props.retention))

const dialogVisible = defineModel({ type: Boolean, required: true })

function resetForm() {
  const retention = props.retention
  form.name = retention?.name || ''
  form.rtype = retention?.rtype || 'count'
  form.keep_last = retention?.keep_last || 1
  form.keep_hourly = retention?.keep_hourly || 0
  form.keep_weekly = retention?.keep_weekly || 0
  form.keep_monthly = retention?.keep_monthly || 0
  form.keep_yearly = retention?.keep_yearly || 0
}

function submitForm() {
  if (form.rtype === 'date' && ![form.keep_hourly, form.keep_weekly, form.keep_monthly, form.keep_yearly].some(value => Number(value) > 0)) {
    $q.notify({ message: 'For date retentions, at least one keep_* value must be greater than 0', color: 'red', position: 'top' })
    return
  }

  emit('submit', { ...form })
}

watch(dialogVisible, value => {
  if (value) resetForm()
})

watch(() => props.retention, () => {
  if (dialogVisible.value) resetForm()
})

defineOptions({ name: 'RetentionManageDialog' })
</script>
