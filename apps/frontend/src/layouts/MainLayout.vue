<template>
  <q-layout view="hHh Lpr lff">
    <q-header class="bg-primary text-white">
      <q-toolbar class="q-px-md">
        <q-btn flat dense round icon="menu" aria-label="Menu" @click="toggleLeftDrawer" />
        <q-toolbar-title class="row items-center no-wrap q-gutter-sm">
          <span class="ellipsis">dRastic Backup</span>
          <q-badge
            v-if="environmentBadgeLabel"
            :color="environmentBadgeColor"
            :label="environmentBadgeLabel"
            text-color="white"
            class="col-auto gt-xs text-weight-bold"
          />
        </q-toolbar-title>

        <q-btn flat dense round icon="menu_book" href="/docs/" target="_blank" aria-label="Documentation" class="q-mr-sm">
          <q-tooltip>Documentation</q-tooltip>
        </q-btn>

        <q-btn-dropdown flat no-caps v-if="userStore.loggedIn" aria-label="User menu">
          <template v-slot:label>
            <div class="row items-center no-wrap">
              <q-icon left name="account_circle" />
              <div class="text-center gt-sm">{{ userStore.user.name }}</div>
            </div>
          </template>
          <q-list>
            <q-item clickable v-close-popup @click="toggleTheme">
              <q-item-section avatar><q-icon :name="$q.dark.isActive ? 'light_mode' : 'dark_mode'" /></q-item-section>
              <q-item-section>
                <q-item-label>{{ $q.dark.isActive ? 'Light mode' : 'Dark mode' }}</q-item-label>
              </q-item-section>
            </q-item>
            <q-separator />
            <q-item clickable v-close-popup @click="openChangePasswordDialog">
              <q-item-section avatar><q-icon name="key" /></q-item-section>
              <q-item-section><q-item-label>Change Password</q-item-label></q-item-section>
            </q-item>
            <q-item clickable v-close-popup @click="openRecoveryExportDialog">
              <q-item-section avatar><q-icon name="folder_zip" /></q-item-section>
              <q-item-section><q-item-label>Download Recovery Export</q-item-label></q-item-section>
            </q-item>
            <q-separator />
            <q-item clickable v-close-popup @click="logout()">
              <q-item-section avatar><q-icon name="lock" /></q-item-section>
              <q-item-section><q-item-label>Logout</q-item-label></q-item-section>
            </q-item>
          </q-list>
        </q-btn-dropdown>
      </q-toolbar>
    </q-header>

    <q-drawer v-model="leftDrawerOpen" show-if-above :width="250" bordered dark>
      <q-scroll-area class="fit">
        <q-list padding dark>
          <DrawerLink to="/" icon="dashboard" label="Dashboard" />
          <q-item-label header class="text-grey-5 text-subtitle2 q-px-md q-pt-md q-pb-xs">
            Infrastructure
          </q-item-label>
          <DrawerLink to="/agents" icon="desktop_windows" label="Agents" />
          <DrawerLink to="/repositories" icon="inventory_2" label="Repositories" />

          <q-item-label header class="text-grey-5 text-subtitle2 q-px-md q-pt-lg q-pb-xs">
            Backup
          </q-item-label>
          <DrawerLink to="/jobs" icon="backup" label="Jobs" />
          <DrawerLink to="/retentions" icon="recycling" label="Retention Policies" />

          <q-item-label header class="text-grey-5 text-subtitle2 q-px-md q-pt-lg q-pb-xs">
            Settings
          </q-item-label>
          <DrawerLink to="/notifications" icon="notifications" label="Notifications" />
        </q-list>
      </q-scroll-area>
    </q-drawer>

    <q-page-container>
      <router-view />
    </q-page-container>

    <q-dialog v-model="changePasswordDialogOpen" persistent>
      <q-card class="db-dialog-card-sm">
        <q-card-section>
          <div class="text-h6">Change Password</div>
          <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
            Enter your current password and choose a new one. You will be signed out afterwards.
          </div>
        </q-card-section>

        <q-form ref="changePasswordForm" @submit="submitChangePassword">
          <q-card-section class="q-gutter-sm">
            <q-input
              outlined
              autofocus
              v-model="changeCurrentPassword"
              :type="showChangePasswords ? 'text' : 'password'"
              label="Current Password"
              autocomplete="current-password"
              lazy-rules
              :rules="[val => !!val || 'Required']"
              :disable="changePasswordSubmitting"
            >
              <template #prepend><q-icon name="lock" /></template>
            </q-input>

            <q-input
              outlined
              v-model="changeNewPassword"
              :type="showChangePasswords ? 'text' : 'password'"
              label="New Password"
              autocomplete="new-password"
              lazy-rules
              :rules="[
                val => !!val || 'Required',
                val => String(val || '').length >= 8 || 'Password must be at least 8 characters'
              ]"
              :disable="changePasswordSubmitting"
            >
              <template #prepend><q-icon name="vpn_key" /></template>
              <template #append>
                <q-icon
                  :name="showChangePasswords ? 'visibility_off' : 'visibility'"
                  class="cursor-pointer"
                  @click="showChangePasswords = !showChangePasswords"
                />
              </template>
            </q-input>

            <q-input
              outlined
              v-model="changeConfirmPassword"
              :type="showChangePasswords ? 'text' : 'password'"
              label="Confirm New Password"
              autocomplete="new-password"
              lazy-rules
              :rules="[
                val => !!val || 'Required',
                val => val === changeNewPassword || 'Passwords do not match'
              ]"
              :disable="changePasswordSubmitting"
            >
              <template #prepend><q-icon name="verified_user" /></template>
            </q-input>

            <q-banner v-if="changePasswordError" dense rounded class="bg-red-1 text-red-10">
              {{ changePasswordError }}
            </q-banner>
          </q-card-section>

          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Cancel" :disable="changePasswordSubmitting" @click="closeChangePasswordDialog" />
            <q-btn unelevated no-caps label="Change Password" type="submit" color="primary" :loading="changePasswordSubmitting" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <q-dialog v-model="recoveryExportDialogOpen" persistent>
      <q-card class="db-dialog-card-sm">
        <q-card-section>
          <div class="text-h6">Download Recovery Export</div>
          <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
            Create an offline recovery package for this installation.
          </div>
        </q-card-section>

        <q-form ref="recoveryExportForm" @submit="submitRecoveryExport">
          <q-card-section class="q-gutter-md">
            <q-banner rounded class="bg-orange-1 text-orange-10">
              <div class="text-weight-bold">
                This ZIP and its recovery.html file contain plaintext repository passwords and provider credentials.
              </div>
              <div class="q-mt-sm">
                Move the export directly into encrypted storage such as Vaultwarden. Do not leave it in Downloads.
              </div>
            </q-banner>

            <div class="text-body2 text-grey-8">
              External SSH keys, Proxmox storage and configuration, and native storage dependencies are not included.
              Preserve anything required by those systems separately.
            </div>

            <q-input
              outlined
              autofocus
              v-model="recoveryExportPassword"
              :type="showRecoveryExportPassword ? 'text' : 'password'"
              label="Current Account Password"
              autocomplete="current-password"
              lazy-rules
              :rules="[val => !!val || 'Required']"
              :disable="recoveryExportSubmitting"
            >
              <template #prepend><q-icon name="lock" /></template>
              <template #append>
                <q-icon
                  :name="showRecoveryExportPassword ? 'visibility_off' : 'visibility'"
                  class="cursor-pointer"
                  @click="showRecoveryExportPassword = !showRecoveryExportPassword"
                />
              </template>
            </q-input>

            <q-banner v-if="recoveryExportError" dense rounded class="bg-red-1 text-red-10">
              {{ recoveryExportError }}
            </q-banner>
          </q-card-section>

          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Cancel" :disable="recoveryExportSubmitting" @click="closeRecoveryExportDialog" />
            <q-btn unelevated no-caps label="Download ZIP" type="submit" color="primary" :loading="recoveryExportSubmitting" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <ReauthenticationDialog />
  </q-layout>
</template>

<script setup>
import { computed, ref, onBeforeMount, onBeforeUnmount, onMounted } from 'vue'
import DrawerLink from 'components/DrawerLink.vue'
import ReauthenticationDialog from 'components/auth/ReauthenticationDialog.vue'
import { useRouter } from 'vue-router'
import { useUserStore } from 'stores/user'
import { useAgentStore } from 'stores/agent'
import { useJobStore } from 'stores/job'
import { useQuasar } from 'quasar'
import { subscribeToSocketEvents } from 'src/utils/socket'
import { createQueuedReload } from 'src/utils/queued-reload'
import { getApiErrorMessage } from 'src/utils/api-error'

const router = useRouter()
const userStore = useUserStore()
const agentStore = useAgentStore()
const jobStore = useJobStore()
const $q = useQuasar()
function toggleTheme() {
  $q.dark.toggle()
  try {
    localStorage.setItem('drastic-theme', $q.dark.isActive ? 'dark' : 'light')
  } catch {
    // Without browser storage, the selection still applies for this session.
  }
}

let stopRealtimeSocketListener = null
let realtimeSyncStarted = false
let disposed = false

const changePasswordDialogOpen = ref(false)
const changePasswordSubmitting = ref(false)
const changePasswordError = ref('')
const changePasswordForm = ref(null)
const changeCurrentPassword = ref('')
const changeNewPassword = ref('')
const changeConfirmPassword = ref('')
const showChangePasswords = ref(false)
const recoveryExportDialogOpen = ref(false)
const recoveryExportSubmitting = ref(false)
const recoveryExportError = ref('')
const recoveryExportForm = ref(null)
const recoveryExportPassword = ref('')
const showRecoveryExportPassword = ref(false)

const queueAgentReload = createQueuedReload(() => agentStore.loadAgents(), 0)
const queueJobReload = createQueuedReload(() => jobStore.loadJobs(), 0)

async function startRealtimeSync() {
  if (disposed || realtimeSyncStarted || !userStore.loggedIn) {
    return
  }

  realtimeSyncStarted = true
  const stopListener = await subscribeToSocketEvents((payload) => {
    const eventName = payload?.name || ''

    if (eventName.startsWith('agentstate') || eventName === 'agentsupdate') {
      void queueAgentReload()
      void queueJobReload()
      return
    }

    if (eventName.startsWith('jobstate') || eventName === 'jobsupdate') {
      void queueJobReload()
    }
  })
  if (disposed) stopListener()
  else stopRealtimeSocketListener = stopListener
}

async function logout() {
  await userStore.logout()
  router.push('/login')
}

function resetChangePasswordDialog() {
  changeCurrentPassword.value = ''
  changeNewPassword.value = ''
  changeConfirmPassword.value = ''
  changePasswordError.value = ''
  showChangePasswords.value = false
  changePasswordForm.value?.resetValidation?.()
}

function openChangePasswordDialog() {
  resetChangePasswordDialog()
  changePasswordDialogOpen.value = true
}

function closeChangePasswordDialog() {
  if (changePasswordSubmitting.value) {
    return
  }
  changePasswordDialogOpen.value = false
  resetChangePasswordDialog()
}

async function submitChangePassword() {
  changePasswordError.value = ''
  changePasswordSubmitting.value = true
  try {
    await userStore.changePassword(changeCurrentPassword.value, changeNewPassword.value)
    changePasswordDialogOpen.value = false
    resetChangePasswordDialog()
    $q.notify({ message: 'Password changed. Please sign in again.', color: 'green', position: 'top' })
    router.push('/login')
  } catch (error) {
    changePasswordError.value = error?.response?.status === 401
      ? 'Current password is wrong'
      : getApiErrorMessage(error, 'Password change failed')
  } finally {
    changePasswordSubmitting.value = false
  }
}

function resetRecoveryExportDialog() {
  recoveryExportPassword.value = ''
  recoveryExportError.value = ''
  showRecoveryExportPassword.value = false
  recoveryExportForm.value?.resetValidation?.()
}

function openRecoveryExportDialog() {
  resetRecoveryExportDialog()
  recoveryExportDialogOpen.value = true
}

function closeRecoveryExportDialog() {
  if (recoveryExportSubmitting.value) {
    return
  }
  recoveryExportDialogOpen.value = false
  resetRecoveryExportDialog()
}

async function submitRecoveryExport() {
  recoveryExportError.value = ''
  recoveryExportSubmitting.value = true
  try {
    await userStore.downloadRecoveryExport(recoveryExportPassword.value)
    recoveryExportDialogOpen.value = false
    resetRecoveryExportDialog()
    $q.notify({ message: 'Recovery export downloaded', color: 'green', position: 'top' })
  } catch (error) {
    recoveryExportError.value = error?.response?.status === 401
      ? 'Current password is wrong'
      : getApiErrorMessage(error, 'Recovery export failed')
  } finally {
    recoveryExportSubmitting.value = false
  }
}

onBeforeMount(async () => {
  if (!userStore.loggedIn) {
    await userStore.getUser()
  }

  await startRealtimeSync()
})

onMounted(() => {
  startRealtimeSync()
})

onBeforeUnmount(() => {
  disposed = true
  queueAgentReload.cancel()
  queueJobReload.cancel()
  if (stopRealtimeSocketListener) {
    stopRealtimeSocketListener()
  }
})

defineOptions({ name: 'MainLayout' })

const leftDrawerOpen = ref(false)
const environmentBadgeLabel = computed(() => {
  const env = String(userStore.environment || '').trim().toLowerCase()
  if (env === 'dev') {
    return 'DEV'
  }
  if (env === 'test') {
    return 'TEST'
  }
  return ''
})
const environmentBadgeColor = computed(() => {
  if (environmentBadgeLabel.value === 'DEV') {
    return 'red-7'
  }
  if (environmentBadgeLabel.value === 'TEST') {
    return 'orange-7'
  }
  return ''
})

function toggleLeftDrawer() {
  leftDrawerOpen.value = !leftDrawerOpen.value
}
</script>
