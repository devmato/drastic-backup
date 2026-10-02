<template>
  <q-dialog v-model="dialogVisible" persistent>
    <q-card class="db-dialog-card-lg">
      <q-card-section>
        <div class="text-h6">{{ isEdit ? 'Edit Repository' : 'Add Repository' }}</div>
      </q-card-section>

      <q-form @submit="submitForm">
        <q-card-section class="q-gutter-sm">
          <q-input outlined v-model="form.name" label="Name" :rules="[val => !!val || 'Required']" />
          <q-select
            outlined
            v-model="form.kind"
            :options="kindOptions"
            label="Type"
            emit-value
            map-options
            :disable="isEdit"
            :rules="[val => !!val || 'Required']"
          />
          <q-input
            v-if="form.kind === 'custom'"
            outlined
            v-model="form.location"
            label="Location"
            :rules="[val => !!val || 'Required']"
          />

          <template v-if="form.kind === 'custom'">
            <q-banner v-if="isSshLocation" dense rounded class="bg-blue-1 text-blue-10">
              SSH/SFTP repositories use the executing agent's SSH public key. Install that key on the target host before running jobs.
            </q-banner>
            <div class="text-subtitle2 q-mt-md">Environment Variables</div>
            <div v-for="(env, idx) in form.envVars" :key="idx" class="row q-gutter-sm items-center">
              <q-input outlined dense v-model="env.name" label="Name" class="col" />
              <q-input outlined dense v-model="env.value" label="Value" class="col" />
              <q-btn flat dense icon="remove_circle" color="red" @click="form.envVars.splice(idx, 1)" />
            </div>
            <q-btn flat dense icon="add" label="Add Variable" @click="form.envVars.push({ name: '', value: '' })" />
          </template>

          <q-checkbox v-if="!isEdit" v-model="form.setCustomPassword" label="Set custom password" />
          <template v-if="!isEdit && form.setCustomPassword">
            <q-input
              outlined
              v-model="form.password"
              type="password"
              label="Repository password"
              :rules="[val => !!val || 'Required']"
            />
            <q-input
              outlined
              v-model="form.password_confirm"
              type="password"
              label="Confirm Password"
              :rules="[val => val === form.password || 'Passwords do not match']"
            />
          </template>
        </q-card-section>

        <q-card-actions align="right">
          <q-btn flat label="Cancel" color="red" :disable="submitting" v-close-popup />
          <q-btn :label="isEdit ? 'Save' : 'Create'" type="submit" color="primary" :loading="submitting" :disable="submitting" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'

const props = defineProps({
  repository: { type: Object, default: null },
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits(['submit'])

const kindOptions = [
  { label: 'Custom', value: 'custom' },
  { label: 'Native (integrated rest-server)', value: 'native' },
]

const form = reactive({
  name: '',
  kind: 'native',
  location: '',
  setCustomPassword: false,
  password: '',
  password_confirm: '',
  envVars: [],
})

const isEdit = computed(() => Boolean(props.repository))
const isSshLocation = computed(() => {
  const location = String(form.location || '').trim().toLowerCase()
  return form.kind === 'custom' && (location.startsWith('sftp:') || location.startsWith('ssh:'))
})
const dialogVisible = defineModel({ type: Boolean, required: true })

function resetForm() {
  const repository = props.repository
  form.name = repository?.name || ''
  form.kind = repository?.kind || 'native'
  form.location = repository?.kind === 'custom' ? repository.location || '' : ''
  form.setCustomPassword = false
  form.password = ''
  form.password_confirm = ''
  form.envVars = Object.entries(repository?.environment || {}).map(([name, value]) => ({
    name,
    value: String(value ?? ''),
  }))
}

function submitForm() {
  const environment = {}
  for (const env of form.envVars) {
    if (env.name && env.value) {
      environment[env.name] = env.value
    }
  }

  const payload = {
    name: form.name,
    kind: form.kind,
  }

  if (form.kind === 'custom') {
    payload.location = form.location
    payload.environment = environment
  }

  if (!isEdit.value && form.setCustomPassword) {
    payload.password = form.password
  }

  emit('submit', payload)
}

watch(dialogVisible, value => {
  if (value) {
    resetForm()
  }
})

watch(() => props.repository, () => {
  if (dialogVisible.value) {
    resetForm()
  }
})

defineOptions({ name: 'RepositoryManageDialog' })
</script>
