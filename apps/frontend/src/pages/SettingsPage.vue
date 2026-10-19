<template>
  <q-page class="q-pa-md">
    <PageHeader title="Settings" description="Account preferences, security and diagnostic access." />
    <div class="column q-gutter-md">
      <q-card flat bordered>
        <q-form @submit="saveTimezone">
          <q-card-section>
            <h2 class="text-h6 q-mt-none q-mb-md">Preferences</h2>
            <q-select v-model="scheduleTimezone" outlined :dense="!$q.platform.has.touch" hide-bottom-space use-input hide-selected fill-input :input-debounce="0"
              :options="filteredTimezones" label="Zeitzone" :disable="timezoneBusy || !timezones.length"
              hint="Applies to job schedules, backup chains and retention rules. Until a time zone is set, UTC is used; login can initialize it from your browser. Offline agents receive changes on their next synchronization."
              :rules="[required]" @filter="filterTimezones" />
            <div v-if="!userStore.user?.schedule_timezone" class="text-caption q-mt-sm">Effective time zone: UTC (standard)</div>
            <q-banner v-if="timezoneError" class="q-mt-md bg-negative text-white">{{ timezoneError }}</q-banner>
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md">
            <q-btn unelevated no-caps color="primary" label="Save" type="submit" :loading="timezoneBusy"
              :disable="!timezones.length || scheduleTimezone === userStore.user?.schedule_timezone" />
          </q-card-actions>
        </q-form>
      </q-card>
      <q-card flat bordered>
        <q-card-section>
          <h2 class="text-h6 q-mt-none q-mb-md">Account &amp; Recovery</h2>
          <div class="row q-gutter-sm">
            <q-btn outline no-caps color="primary" icon="key" label="Change Password" @click="passwordOpen = true" />
            <q-btn outline no-caps color="primary" icon="folder_zip" label="Download Recovery Export" @click="recoveryOpen = true" />
          </div>
        </q-card-section>
      </q-card>
      <q-card flat bordered>
        <q-card-section class="q-gutter-y-md">
          <h2 class="text-h6 q-mb-none">Debug &amp; MCP</h2>
          <div class="text-body2">Enable read-only remote debugging and diagnostic recording for all agents in your account. Diagnostic data can contain hostnames, paths and operation logs.</div>
          <q-banner v-if="debugError" class="bg-negative text-white">{{ debugError }}</q-banner>
          <template v-if="debug">
            <q-toggle :model-value="debug.enabled" :disable="debugBusy" label="Enable diagnostic recording and MCP access" @update:model-value="changeDebug" />
            <div class="text-caption">Up to {{ debug.retention_days }} days or {{ debug.max_events.toLocaleString() }} events. Disabling immediately revokes access; agent recording stops within 60 seconds.</div>
            <q-input outlined :dense="!$q.platform.has.touch" readonly :model-value="debug.mcp_url" label="MCP endpoint (Streamable HTTP)">
              <template #append><q-btn flat round icon="content_copy" aria-label="Copy MCP endpoint" @click="copy(debug.mcp_url)" /></template>
            </q-input>
            <div v-if="debug.enabled" class="row items-center q-gutter-sm">
              <q-btn outline no-caps color="primary" label="Renew token" :disable="debugBusy" @click="changeDebug(true)" />
              <span class="text-caption">Use the token as a Bearer token. Renewing invalidates the previous token.</span>
            </div>
          </template>
        </q-card-section>
      </q-card>
    </div>

    <q-dialog v-model="passwordOpen" persistent @hide="resetPassword">
      <q-card class="db-dialog-card-sm">
        <q-card-section>
          <div class="text-h6">Change Password</div>
          <div class="text-caption">Enter your current password and choose a new one. You will be signed out afterwards.</div>
        </q-card-section>
        <q-form @submit="submitPassword">
          <q-card-section class="q-gutter-sm">
            <q-input v-model="currentPassword" outlined :dense="!$q.platform.has.touch" autofocus :type="showPasswords ? 'text' : 'password'" label="Current Password" autocomplete="current-password" :rules="[required]" :disable="passwordBusy" />
            <q-input v-model="newPassword" outlined :dense="!$q.platform.has.touch" :type="showPasswords ? 'text' : 'password'" label="New Password" autocomplete="new-password" :rules="[required, val => val.length >= 8 || 'Password must be at least 8 characters']" :disable="passwordBusy">
              <template #append><q-btn flat round :icon="showPasswords ? 'visibility_off' : 'visibility'" aria-label="Toggle password visibility" @click="showPasswords = !showPasswords" /></template>
            </q-input>
            <q-input v-model="confirmPassword" outlined :dense="!$q.platform.has.touch" :type="showPasswords ? 'text' : 'password'" label="Confirm New Password" autocomplete="new-password" :rules="[required, val => val === newPassword || 'Passwords do not match']" :disable="passwordBusy" />
            <q-banner v-if="passwordError" class="bg-negative text-white">{{ passwordError }}</q-banner>
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Cancel" :disable="passwordBusy" @click="passwordOpen = false" />
            <q-btn unelevated no-caps color="primary" label="Change Password" type="submit" :loading="passwordBusy" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
    <q-dialog v-model="recoveryOpen" persistent @hide="resetRecovery">
      <q-card class="db-dialog-card-sm">
        <q-card-section>
          <div class="text-h6">Download Recovery Export</div>
          <div class="text-caption">Repository access, configuration and offline restore instructions.</div>
        </q-card-section>
        <template v-if="archivePassword">
          <q-card-section class="q-gutter-md">
            <div role="status">The encrypted ZIP download has started.</div>
            <q-input outlined :dense="!$q.platform.has.touch" readonly type="textarea" autogrow rows="1" :model-value="archivePassword" label="Archive password" autocomplete="off" spellcheck="false">
              <template #append><q-btn flat round icon="content_copy" aria-label="Copy archive password" @click="copy(archivePassword)" /></template>
            </q-input>
            <div class="text-body2">Save this password now; it cannot be retrieved after closing. Your account password does not open the kit.</div>
            <div class="text-caption">Extract with an AES-ZIP-compatible tool such as 7-Zip, then open recovery.html. Extracted files contain plaintext credentials.</div>
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps color="primary" label="Done" @click="recoveryOpen = false" />
          </q-card-actions>
        </template>
        <q-form v-else @submit="submitRecovery">
          <q-card-section class="q-gutter-md">
            <q-banner rounded class="bg-orange-1 text-orange-10">
              Save the random archive password shown after download. It cannot be recovered later.
            </q-banner>
            <div class="text-body2">Includes repository credentials and restore instructions. Preserve backup data and SSH keys separately.</div>
            <q-select v-model="resticTargets" outlined :dense="!$q.platform.has.touch" multiple use-chips emit-value map-options hide-bottom-space bottom-slots
              :options="resticTargetOptions" :disable="recoveryBusy" label="Restic archives (optional)"
              aria-describedby="recovery-restic-help">
              <template #hint><span id="recovery-restic-help">Official, checksum-verified archives; unpack the matching one before recovery. Leave empty for a small kit.</span></template>
            </q-select>
            <q-input v-model="recoveryPassword" outlined :dense="!$q.platform.has.touch" autofocus :type="showRecoveryPassword ? 'text' : 'password'" label="Current Account Password" autocomplete="current-password" :rules="[required]" :disable="recoveryBusy">
              <template #append><q-btn flat round :disable="recoveryBusy" :icon="showRecoveryPassword ? 'visibility_off' : 'visibility'" aria-label="Toggle password visibility" @click="showRecoveryPassword = !showRecoveryPassword" /></template>
            </q-input>
            <q-banner v-if="recoveryError" class="bg-negative text-white">{{ recoveryError }}</q-banner>
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Cancel" :disable="recoveryBusy" @click="recoveryOpen = false" />
            <q-btn unelevated no-caps color="primary" label="Download ZIP" type="submit" :loading="recoveryBusy" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
    <q-dialog :model-value="Boolean(token)" persistent @update:model-value="token = ''">
      <q-card class="db-dialog-card-sm">
        <q-card-section class="q-gutter-md">
          <div class="text-h6">MCP token</div>
          <div>Copy this token now. It will not be shown again. Store it in your MCP client's secret storage.</div>
          <q-input outlined :dense="!$q.platform.has.touch" readonly :model-value="token" label="Bearer token" type="password">
            <template #append><q-btn flat round icon="content_copy" aria-label="Copy MCP token" @click="copy(token)" /></template>
          </q-input>
        </q-card-section>
        <q-card-actions align="right"><q-btn flat no-caps color="primary" label="Done" @click="token = ''" /></q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useQuasar, copyToClipboard } from 'quasar'
import PageHeader from 'components/PageHeader.vue'
import { useUserStore } from 'stores/user'
import { getApiErrorMessage } from 'src/utils/api-error'
import { getTimezones } from 'src/api/user'

const $q = useQuasar()
const router = useRouter()
const userStore = useUserStore()
const required = val => !!val || 'Required'
const debug = ref(null)
const debugBusy = ref(false)
const debugError = ref('')
const token = ref('')
let disposed = false

const scheduleTimezone = ref(userStore.user?.schedule_timezone || 'UTC')
const timezones = ref([])
const filteredTimezones = ref([])
const timezoneBusy = ref(false)
const timezoneError = ref('')
function filterTimezones(value, update) {
  update(() => { filteredTimezones.value = timezones.value.filter(zone => zone.toLowerCase().includes(value.toLowerCase())) })
}
async function loadTimezones() {
  try {
    const zones = await getTimezones()
    if (!disposed) timezones.value = filteredTimezones.value = zones
  } catch (error) {
    if (!disposed) timezoneError.value = getApiErrorMessage(error, 'Could not load time zones')
  }
}
async function saveTimezone() {
  timezoneBusy.value = true
  timezoneError.value = ''
  try {
    await userStore.updateSettings(scheduleTimezone.value)
    if (!disposed) $q.notify({ message: 'Time zone saved', color: 'positive' })
  } catch (error) {
    if (!disposed) timezoneError.value = getApiErrorMessage(error, 'Could not save time zone')
  } finally { timezoneBusy.value = false }
}

async function copy(value) {
  try {
    await copyToClipboard(value)
    $q.notify({ message: 'Copied', color: 'positive' })
  } catch { $q.notify({ message: 'Could not copy to clipboard', color: 'negative' }) }
}
async function loadDebug() {
  try {
    const result = await userStore.getDebugAccess()
    if (!disposed) debug.value = result
  } catch (error) { debugError.value = getApiErrorMessage(error, 'Could not load diagnostic settings') }
}
async function changeDebug(enabled) {
  debugBusy.value = true
  debugError.value = ''
  try {
    const { token: createdToken, ...status } = await userStore.setDebugAccess(enabled)
    if (disposed) return
    debug.value = status
    token.value = createdToken || ''
  } catch (error) { debugError.value = getApiErrorMessage(error, 'Could not update diagnostic settings') }
  finally { debugBusy.value = false }
}
const passwordOpen = ref(false)
const passwordBusy = ref(false)
const passwordError = ref('')
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const showPasswords = ref(false)
function resetPassword() {
  currentPassword.value = newPassword.value = confirmPassword.value = passwordError.value = ''
  showPasswords.value = false
}
async function submitPassword() {
  passwordBusy.value = true
  passwordError.value = ''
  try {
    await userStore.changePassword(currentPassword.value, newPassword.value)
    passwordOpen.value = false
    resetPassword()
    $q.notify({ message: 'Password changed. Please sign in again.', color: 'positive' })
    router.push('/login')
  } catch (error) {
    passwordError.value = error?.response?.status === 401 ? 'Current password is wrong' : getApiErrorMessage(error, 'Password change failed')
  } finally { passwordBusy.value = false }
}
const recoveryOpen = ref(false)
const recoveryBusy = ref(false)
const recoveryError = ref('')
const recoveryPassword = ref('')
const showRecoveryPassword = ref(false)
const archivePassword = ref('')
const resticTargets = ref([])
let recoveryController = null
const resticTargetOptions = [
  { value: 'linux_amd64', label: 'Linux — x64' },
  { value: 'linux_arm64', label: 'Linux — ARM64' },
  { value: 'darwin_amd64', label: 'macOS — Intel' },
  { value: 'darwin_arm64', label: 'macOS — Apple Silicon' },
  { value: 'windows_amd64', label: 'Windows — x64' },
]
function resetRecovery() {
  recoveryPassword.value = recoveryError.value = archivePassword.value = ''
  showRecoveryPassword.value = false
  resticTargets.value = []
}
async function submitRecovery() {
  if (recoveryBusy.value || archivePassword.value) return
  recoveryBusy.value = true
  recoveryError.value = ''
  recoveryController = new AbortController()
  try {
    // 24 random bytes produce a 32-character base64url password (192 bits).
    const bytes = crypto.getRandomValues(new Uint8Array(24))
    const generatedPassword = btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_')
    await userStore.downloadRecoveryExport(recoveryPassword.value, generatedPassword, [...resticTargets.value], recoveryController.signal)
    if (!disposed) archivePassword.value = generatedPassword
  } catch (error) {
    if (!disposed) recoveryError.value = error?.response?.status === 401 ? 'Current password is wrong' : getApiErrorMessage(error, 'Recovery export failed')
  } finally {
    recoveryPassword.value = ''
    showRecoveryPassword.value = false
    recoveryBusy.value = false
    recoveryController = null
  }
}
onMounted(loadDebug)
onMounted(loadTimezones)
onBeforeUnmount(() => {
  disposed = true
  recoveryController?.abort()
  token.value = ''
  resetPassword()
  resetRecovery()
})
</script>
