<template>
  <q-page class="q-pa-md">
    <PageHeader title="Settings" description="Account security and diagnostic access." />
    <div class="column q-gutter-md">
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
            <q-input outlined readonly :model-value="debug.mcp_url" label="MCP endpoint (Streamable HTTP)">
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
            <q-input v-model="currentPassword" outlined autofocus :type="showPasswords ? 'text' : 'password'" label="Current Password" autocomplete="current-password" :rules="[required]" :disable="passwordBusy" />
            <q-input v-model="newPassword" outlined :type="showPasswords ? 'text' : 'password'" label="New Password" autocomplete="new-password" :rules="[required, val => val.length >= 8 || 'Password must be at least 8 characters']" :disable="passwordBusy">
              <template #append><q-btn flat round :icon="showPasswords ? 'visibility_off' : 'visibility'" aria-label="Toggle password visibility" @click="showPasswords = !showPasswords" /></template>
            </q-input>
            <q-input v-model="confirmPassword" outlined :type="showPasswords ? 'text' : 'password'" label="Confirm New Password" autocomplete="new-password" :rules="[required, val => val === newPassword || 'Passwords do not match']" :disable="passwordBusy" />
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
          <div class="text-caption">Create an offline recovery package for this installation.</div>
        </q-card-section>
        <q-form @submit="submitRecovery">
          <q-card-section class="q-gutter-md">
            <q-banner rounded class="bg-orange-1 text-orange-10">
              <div class="text-weight-bold">This ZIP and its recovery.html file contain plaintext repository passwords and provider credentials.</div>
              <div class="q-mt-sm">Move the export directly into encrypted storage such as Vaultwarden. Do not leave it in Downloads.</div>
            </q-banner>
            <div class="text-body2">External SSH keys, Proxmox storage and configuration, and native storage dependencies are not included. Preserve anything required by those systems separately.</div>
            <q-input v-model="recoveryPassword" outlined autofocus :type="showRecoveryPassword ? 'text' : 'password'" label="Current Account Password" autocomplete="current-password" :rules="[required]" :disable="recoveryBusy">
              <template #append><q-btn flat round :icon="showRecoveryPassword ? 'visibility_off' : 'visibility'" aria-label="Toggle password visibility" @click="showRecoveryPassword = !showRecoveryPassword" /></template>
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
          <q-input outlined readonly :model-value="token" label="Bearer token" type="password">
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

const $q = useQuasar()
const router = useRouter()
const userStore = useUserStore()
const required = val => !!val || 'Required'
const debug = ref(null)
const debugBusy = ref(false)
const debugError = ref('')
const token = ref('')
let disposed = false

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
function resetRecovery() {
  recoveryPassword.value = recoveryError.value = ''
  showRecoveryPassword.value = false
}
async function submitRecovery() {
  recoveryBusy.value = true
  recoveryError.value = ''
  try {
    await userStore.downloadRecoveryExport(recoveryPassword.value)
    recoveryOpen.value = false
    resetRecovery()
    $q.notify({ message: 'Recovery export downloaded', color: 'positive' })
  } catch (error) {
    recoveryError.value = error?.response?.status === 401 ? 'Current password is wrong' : getApiErrorMessage(error, 'Recovery export failed')
  } finally { recoveryBusy.value = false }
}
onMounted(loadDebug)
onBeforeUnmount(() => {
  disposed = true
  token.value = ''
  resetPassword()
  resetRecovery()
})
</script>
