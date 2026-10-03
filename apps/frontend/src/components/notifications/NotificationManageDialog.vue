<template>
  <q-dialog v-model="dialogVisible" persistent>
    <q-card class="db-dialog-card-lg">
      <q-card-section>
        <div class="text-h6">{{ isEdit ? 'Edit Notification Config' : 'Add Notification Config' }}</div>
      </q-card-section>

      <q-form @submit="submitForm">
        <q-card-section class="q-gutter-sm">
          <q-input outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model="form.url" label="Apprise URL" :rules="[val => !!val || 'Required']" />
          <q-select outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model="form.operation_types" :options="options.operation_types" label="Operation Types" multiple emit-value map-options option-label="label" option-value="value" :rules="[val => val.length > 0 || 'Select at least one']" />
          <q-select outlined :dense="!$q.platform.has.touch" hide-bottom-space v-model="form.operation_states" :options="options.operation_states" label="Operation States" multiple emit-value map-options option-label="label" option-value="value" :rules="[val => val.length > 0 || 'Select at least one']" />

          <q-btn flat no-caps icon="send" label="Test notification" color="primary" @click="emit('test', form.url)" :loading="testing" :disable="testing || !form.url" />
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

const props = defineProps({
  config: { type: Object, default: null },
  options: { type: Object, default: () => ({ operation_types: [], operation_states: [] }) },
  submitting: { type: Boolean, default: false },
  testing: { type: Boolean, default: false },
})

const emit = defineEmits(['submit', 'test'])

const form = reactive({ url: '', operation_types: [], operation_states: [] })

const isEdit = computed(() => Boolean(props.config))

const dialogVisible = defineModel({ type: Boolean, required: true })

function resetForm() {
  const config = props.config
  form.url = config?.url || ''
  form.operation_types = [...(config?.operation_types || [])]
  form.operation_states = [...(config?.operation_states || [])]
}

function submitForm() {
  emit('submit', {
    url: form.url,
    operation_types: [...form.operation_types],
    operation_states: [...form.operation_states],
  })
}

watch(dialogVisible, value => {
  if (value) resetForm()
})

watch(() => props.config, () => {
  if (dialogVisible.value) resetForm()
})

defineOptions({ name: 'NotificationManageDialog' })
</script>
