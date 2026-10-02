<template>
  <q-dialog v-model="dialogVisible" persistent>
    <q-card class="db-dialog-card-md">
      <q-card-section>
        <div class="text-h6">{{ isEdit ? 'Edit Action' : 'Add Action' }}</div>
      </q-card-section>

      <q-form @submit="submitForm">
        <q-card-section class="q-gutter-sm">
          <q-select outlined dense v-model="form.module" :options="moduleOptions" label="Module" emit-value map-options :rules="[val => !!val || 'Required']" />
          <q-select outlined dense v-model="form.hook" :options="hookOptions" label="Hook" emit-value map-options :rules="[val => !!val || 'Required']" />
          <q-input v-if="form.module === 'command'" outlined dense v-model="form.command" label="Command" :rules="[val => !!String(val || '').trim() || 'Required']" />
          <template v-if="form.module === 'docker'">
            <q-input outlined dense v-model="form.container" label="Container name" :rules="[val => !!String(val || '').trim() || 'Required']" />
            <q-select outlined dense v-model="form.action" :options="dockerActionOptions" label="Container action" emit-value map-options :rules="[val => !!val || 'Required']" />
            <q-input v-if="form.action === 'command'" outlined dense v-model="form.command" label="Command" :rules="[val => !!String(val || '').trim() || 'Required']" />
          </template>
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
import { computed, reactive, watch } from 'vue'

const props = defineProps({
  action: { type: Object, default: null },
})

const emit = defineEmits(['submit'])

const hookOptions = [
  { label: 'Before Start', value: 'start' },
  { label: 'On Error', value: 'error' },
  { label: 'On Success', value: 'success' },
  { label: 'After End', value: 'end' },
]

const moduleOptions = [
  { label: 'Execute command on agent', value: 'command' },
  { label: 'Control docker container', value: 'docker' },
]

const dockerActionOptions = [
  { label: 'Stop container', value: 'stop' },
  { label: 'Start container', value: 'start' },
  { label: 'Execute command', value: 'command' },
]

const form = reactive({ module: 'command', hook: 'start', command: '', container: '', action: 'stop' })

const isEdit = computed(() => Boolean(props.action))

const dialogVisible = defineModel({ type: Boolean, required: true })

function resetForm() {
  const action = props.action
  form.module = action?.module || 'command'
  form.hook = action?.hook || 'start'
  form.command = action?.data?.command || ''
  form.container = action?.data?.container || ''
  form.action = action?.data?.action || 'stop'
}

function submitForm() {
  const actionData = {}
  if (form.module === 'command') {
    actionData.command = form.command.trim()
  } else if (form.module === 'docker') {
    actionData.container = form.container.trim()
    actionData.action = form.action
    if (form.action === 'command') actionData.command = form.command.trim()
  }

  emit('submit', { module: form.module, hook: form.hook, data: actionData })
}

watch(dialogVisible, value => {
  if (value) resetForm()
})

watch(() => props.action, () => {
  if (dialogVisible.value) resetForm()
})

defineOptions({ name: 'JobActionManageDialog' })
</script>
