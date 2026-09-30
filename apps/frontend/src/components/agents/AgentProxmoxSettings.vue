<template>
  <div>
    <div class="text-subtitle1">Proxmox</div>
    <div class="text-caption q-mb-md" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
      Used by all Proxmox jobs on this agent. The agent must run directly on the Proxmox node.
    </div>

    <q-banner v-if="!agentOnline" class="bg-grey-2 text-grey-8">
      Bring the agent online to load, test or save its Proxmox settings.
    </q-banner>
    <div v-else-if="loading" class="row justify-center q-pa-md">
      <q-spinner color="primary" size="32px" />
    </div>

    <q-banner v-if="error" class="bg-negative text-white q-mb-md">
      {{ error }}
      <template v-if="!loaded && agentOnline" #action>
        <q-btn flat label="Retry" @click="loadSettings" />
      </template>
    </q-banner>

    <q-form v-if="loaded && agentOnline" ref="formRef" class="q-gutter-md" @submit="saveSettings">
      <div v-if="loaded.configured" class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
        {{ loaded.source === 'agent' ? 'Settings saved on this agent.' : 'Using environment settings. Saving here replaces them for this agent.' }}
      </div>
      <q-input
        v-model="form.token_id"
        outlined
        label="Token ID"
        hint="user@realm!tokenname"
        :disable="busy"
        :rules="[value => !!value.trim() || 'Required']"
      />
      <q-input
        v-model="form.token_secret"
        outlined
        type="password"
        autocomplete="new-password"
        label="Token Secret"
        :hint="canKeepSecret ? 'Token stored. Leave empty to keep it, or enter a replacement.' : 'Enter the secret shown when creating the Proxmox API token.'"
        :disable="busy"
        reactive-rules
        :rules="[value => !!value || canKeepSecret || 'Required']"
      />

      <q-expansion-item label="Advanced" dense>
        <div class="q-gutter-md q-pt-md">
          <q-input v-model="form.api_url" outlined label="API URL" :disable="busy" :rules="[value => value.trim().startsWith('https://') || 'Use an HTTPS API URL']" />
          <q-input v-model="form.node" outlined label="Node (optional)" hint="Leave empty to detect the local node automatically." :disable="busy" />
          <q-toggle v-model="form.verify_tls" label="Verify TLS certificate" :disable="busy" />
          <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Disable verification for a self-signed Proxmox certificate.</div>
        </div>
      </q-expansion-item>

      <q-banner v-if="testResult" class="bg-positive text-white">
        Connected to {{ testResult.node }} · {{ testResult.guest_count }} supported VM(s) found.
        The test does not save these settings.
      </q-banner>

      <div class="row justify-end q-gutter-sm">
        <q-btn flat label="Test connection" :loading="testing" :disable="busy" @click="testConnection" />
        <q-btn color="primary" label="Save" type="submit" :loading="saving" :disable="busy" />
      </div>
    </q-form>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useAgentStore } from 'stores/agent'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const props = defineProps({
  agentId: { type: [Number, String], required: true },
  agentOnline: { type: Boolean, default: false },
})
const emit = defineEmits(['saved'])
const $q = useQuasar()
const agentStore = useAgentStore()
const formRef = ref(null)
const form = reactive({ api_url: '', token_id: '', token_secret: '', node: '', verify_tls: false })
const loaded = ref(null)
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const error = ref('')
const testResult = ref(null)
let loadVersion = 0

const busy = computed(() => loading.value || saving.value || testing.value)
const canKeepSecret = computed(() => loaded.value?.token_secret_configured
  && form.api_url.trim().replace(/\/+$/, '') === loaded.value.api_url
  && form.token_id.trim() === loaded.value.token_id)

watch(() => [props.agentId, props.agentOnline], loadSettings, { immediate: true })
watch(form, () => { testResult.value = null })

function applySettings(settings) {
  loaded.value = settings
  Object.assign(form, {
    api_url: settings.api_url,
    token_id: settings.token_id,
    token_secret: '',
    node: settings.node,
    verify_tls: settings.verify_tls,
  })
}

async function loadSettings() {
  const version = ++loadVersion
  loaded.value = null
  form.token_secret = ''
  error.value = ''
  testResult.value = null
  loading.value = props.agentOnline
  if (!props.agentOnline) return
  try {
    const settings = await agentStore.getProxmoxSettings(props.agentId)
    if (version === loadVersion) applySettings(settings)
  } catch (e) {
    if (version === loadVersion && !shouldIgnoreApiError(e)) {
      error.value = getApiErrorMessage(e, 'Could not load Proxmox settings. Update the agent if this feature is unsupported.')
    }
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

function payload() {
  const settings = {
    api_url: form.api_url.trim(),
    token_id: form.token_id.trim(),
    node: form.node.trim(),
    verify_tls: form.verify_tls,
  }
  if (form.token_secret) settings.token_secret = form.token_secret
  return settings
}

async function saveSettings() {
  if (busy.value || !props.agentOnline) return
  saving.value = true
  error.value = ''
  try {
    applySettings(await agentStore.updateProxmoxSettings(props.agentId, payload()))
    emit('saved')
    $q.notify({ message: 'Proxmox settings saved on the agent', color: 'positive', position: 'top' })
  } catch (e) {
    if (!shouldIgnoreApiError(e)) error.value = getApiErrorMessage(e, 'Could not save Proxmox settings')
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  if (busy.value || !props.agentOnline || !await formRef.value.validate()) return
  testing.value = true
  error.value = ''
  testResult.value = null
  try {
    testResult.value = await agentStore.testProxmoxSettings(props.agentId, payload())
  } catch (e) {
    if (!shouldIgnoreApiError(e)) error.value = getApiErrorMessage(e, 'Proxmox connection test failed')
  } finally {
    testing.value = false
  }
}

defineOptions({ name: 'AgentProxmoxSettings' })
</script>
