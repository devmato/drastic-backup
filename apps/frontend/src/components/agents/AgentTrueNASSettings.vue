<template>
  <div>
    <h2 class="text-subtitle1 text-weight-medium q-my-none">TrueNAS</h2>
    <p class="text-caption q-mb-md" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">TrueNAS 25.10 or newer. Run this agent as an app on the NAS and mount the datasets into it.</p>
    <q-banner v-if="!agentOnline">Bring the agent online to load, test or save its connection.</q-banner>
    <q-banner v-if="error" class="bg-negative text-white q-mb-md">
      {{ error }}
      <template v-if="!loaded && agentOnline" #action><q-btn flat no-caps label="Retry" @click="loadSettings" /></template>
    </q-banner>
    <q-spinner v-if="loading" color="primary" />
    <q-form v-if="loaded && agentOnline" ref="formRef" class="q-gutter-sm" @submit="run('save')">
      <q-input v-model="form.api_url" outlined :dense="!$q.platform.has.touch" label="NAS HTTPS address" hint="https://truenas.example.net — reachable from the agent container" :disable="busy" :rules="[value => value.startsWith('https://') || 'Use HTTPS']" />
      <q-input v-model="form.username" outlined :dense="!$q.platform.has.touch" hide-bottom-space label="API key owner (username)" :disable="busy" :rules="[value => !!value.trim() || 'Required']" />
      <q-input v-model="form.api_key" outlined :dense="!$q.platform.has.touch" label="API key" type="password" autocomplete="new-password" :disable="busy" :hint="canKeepKey ? 'Key stored. Leave empty to keep it.' : 'Enter the API key created in TrueNAS.'" reactive-rules :rules="[value => !!value || canKeepKey || 'Required']" />
      <q-expansion-item label="Advanced">
        <div class="q-gutter-md q-pt-md">
          <q-toggle v-model="form.verify_tls" label="Verify TLS certificate" :disable="busy" />
          <q-input v-model="form.host_root" outlined :dense="!$q.platform.has.touch" label="Host root inside agent" hint="Default /mnt/host maps /mnt/POOL/DATASET to /mnt/host/mnt/POOL/DATASET." :disable="busy" :rules="[value => value.startsWith('/') || 'Use an absolute path']" />
        </div>
      </q-expansion-item>
      <q-banner v-if="loaded.pending_snapshots" class="bg-warning text-black">
        {{ loaded.pending_snapshots }} temporary snapshot(s) need cleanup. Keep this NAS address until cleanup succeeds; API keys can be replaced.
        <template #action><q-btn flat no-caps label="Retry cleanup" :disable="busy" @click="run('cleanup')" /></template>
      </q-banner>
      <q-banner v-if="testResult" class="bg-positive text-white">
        Connected to {{ testResult.version }} · {{ testResult.dataset_count }} filesystem dataset(s) found. This test does not save settings or create snapshots.
      </q-banner>
      <div class="row justify-end q-gutter-sm">
        <slot name="actions" />
        <q-btn flat no-caps no-wrap label="Test connection" :loading="pending === 'test'" :disable="busy" @click="run('test')" />
        <q-btn unelevated no-caps color="primary" label="Save" type="submit" :loading="pending === 'save'" :disable="busy" />
      </div>
    </q-form>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const props = defineProps({ agentId: { type: [Number, String], required: true }, agentOnline: Boolean })
const emit = defineEmits(['saved', 'cleaned-up'])
const $q = useQuasar()
const agentStore = useAgentStore()
const formRef = ref(null)
const loaded = ref(null)
const loading = ref(false)
const pending = ref('')
const error = ref('')
const testResult = ref(null)
const form = reactive({ api_url: '', username: '', api_key: '', verify_tls: true, host_root: '/mnt/host' })
const busy = computed(() => loading.value || !!pending.value)
defineExpose({ busy })
const canKeepKey = computed(() => loaded.value?.api_key_configured
  && form.api_url.trim().replace(/\/+$/, '') === loaded.value.api_url && form.username.trim() === loaded.value.username)
let loadVersion = 0

function apply(settings) {
  loaded.value = settings
  for (const key of ['api_url', 'username', 'verify_tls', 'host_root']) form[key] = settings[key]
  form.api_key = ''
}

async function loadSettings() {
  const version = ++loadVersion
  loaded.value = null
  form.api_key = ''
  error.value = ''
  testResult.value = null
  loading.value = props.agentOnline
  if (!props.agentOnline) return
  try {
    const settings = await agentStore.getTrueNASSettings(props.agentId)
    if (version === loadVersion) apply(settings)
  } catch (e) {
    if (version === loadVersion && !shouldIgnoreApiError(e)) error.value = getApiErrorMessage(e)
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

async function run(action) {
  if (busy.value || !props.agentOnline || (action !== 'cleanup' && !await formRef.value.validate())) return
  pending.value = action
  error.value = ''
  testResult.value = null
  const payload = { ...form, api_url: form.api_url.trim(), username: form.username.trim(), host_root: form.host_root.trim() }
  if (!payload.api_key) delete payload.api_key
  try {
    if (action === 'test') testResult.value = await agentStore.testTrueNASSettings(props.agentId, payload)
    else {
      apply(action === 'cleanup' ? await agentStore.cleanupTrueNAS(props.agentId) : await agentStore.updateTrueNASSettings(props.agentId, payload))
      emit(action === 'cleanup' ? 'cleaned-up' : 'saved')
      $q.notify({ message: action === 'cleanup' ? 'Snapshot cleanup completed' : 'TrueNAS connection saved on the agent', color: 'positive' })
    }
  } catch (e) {
    if (!shouldIgnoreApiError(e)) error.value = getApiErrorMessage(e)
  } finally {
    pending.value = ''
  }
}

watch(() => [props.agentId, props.agentOnline], loadSettings, { immediate: true })
watch(form, () => { testResult.value = null })
</script>
