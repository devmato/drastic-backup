<template>
  <q-layout view="hHh Lpr lff">
    <q-header class="bg-primary text-white">
      <q-toolbar class="q-px-md">
        <q-btn flat dense round icon="menu" aria-label="Menu" @click="leftDrawerOpen = !leftDrawerOpen" />
        <q-toolbar-title class="row items-center no-wrap q-gutter-sm">
          <span class="ellipsis">dRastic Backup</span>
          <q-badge v-if="environmentBadgeLabel" :color="environmentBadgeLabel === 'DEV' ? 'red-7' : 'orange-7'" :label="environmentBadgeLabel" text-color="white" class="col-auto gt-xs text-weight-bold" />
        </q-toolbar-title>
        <q-btn flat dense round icon="menu_book" href="/docs/" target="_blank" aria-label="Documentation" class="q-mr-sm">
          <q-tooltip>Documentation</q-tooltip>
        </q-btn>
        <q-btn-dropdown v-if="userStore.loggedIn" flat no-caps aria-label="User menu">
          <template #label>
            <div class="row items-center no-wrap">
              <q-icon left name="account_circle" />
              <div class="text-center gt-sm">{{ userStore.user.name }}</div>
            </div>
          </template>
          <q-list>
            <q-item clickable v-close-popup @click="toggleTheme">
              <q-item-section avatar><q-icon :name="$q.dark.isActive ? 'light_mode' : 'dark_mode'" /></q-item-section>
              <q-item-section>{{ $q.dark.isActive ? 'Light mode' : 'Dark mode' }}</q-item-section>
            </q-item>
            <q-separator />
            <q-item clickable v-close-popup to="/settings">
              <q-item-section avatar><q-icon name="settings" /></q-item-section>
              <q-item-section>Settings</q-item-section>
            </q-item>
            <q-separator />
            <q-item clickable v-close-popup @click="logout">
              <q-item-section avatar><q-icon name="lock" /></q-item-section>
              <q-item-section>Logout</q-item-section>
            </q-item>
          </q-list>
        </q-btn-dropdown>
      </q-toolbar>
    </q-header>
    <q-drawer v-model="leftDrawerOpen" show-if-above :width="250" bordered dark>
      <q-scroll-area class="fit">
        <q-list padding dark>
          <DrawerLink to="/" icon="dashboard" label="Dashboard" />
          <q-item-label header class="text-grey-5 text-subtitle2 q-px-md q-pt-md q-pb-xs">Infrastructure</q-item-label>
          <DrawerLink to="/agents" icon="desktop_windows" label="Agents" />
          <DrawerLink to="/repositories" icon="inventory_2" label="Repositories" />
          <q-item-label header class="text-grey-5 text-subtitle2 q-px-md q-pt-lg q-pb-xs">Backup</q-item-label>
          <DrawerLink to="/jobs" icon="backup" label="Jobs" />
          <DrawerLink to="/retentions" icon="recycling" label="Retention Policies" />
          <q-item-label header class="text-grey-5 text-subtitle2 q-px-md q-pt-lg q-pb-xs">Settings</q-item-label>
          <DrawerLink to="/notifications" icon="notifications" label="Notifications" />
        </q-list>
      </q-scroll-area>
    </q-drawer>
    <q-page-container><router-view /></q-page-container>
    <ReauthenticationDialog />
  </q-layout>
</template>

<script setup>
import { computed, ref, onBeforeMount, onBeforeUnmount } from 'vue'
import DrawerLink from 'components/DrawerLink.vue'
import ReauthenticationDialog from 'components/auth/ReauthenticationDialog.vue'
import { useRouter } from 'vue-router'
import { useQuasar } from 'quasar'
import { useUserStore } from 'stores/user'
import { useAgentStore } from 'stores/agent'
import { useJobStore } from 'stores/job'
import { subscribeToSocketEvents } from 'src/utils/socket'
import { createQueuedReload } from 'src/utils/queued-reload'
import { useSessionKeepAlive } from 'src/composables/useSessionKeepAlive'

const router = useRouter()
const $q = useQuasar()
const userStore = useUserStore()
useSessionKeepAlive(userStore)
const agentStore = useAgentStore()
const jobStore = useJobStore()
const leftDrawerOpen = ref(false)
const environmentBadgeLabel = computed(() => {
  const env = String(userStore.environment || '').trim().toLowerCase()
  return ['dev', 'test'].includes(env) ? env.toUpperCase() : ''
})
let stopRealtimeSocketListener = null
let disposed = false
const queueAgentReload = createQueuedReload(() => agentStore.loadAgents(), 0)
const queueJobReload = createQueuedReload(() => jobStore.loadJobs(), 0)

function toggleTheme() {
  $q.dark.toggle()
  try { localStorage.setItem('drastic-theme', $q.dark.isActive ? 'dark' : 'light') } catch { /* Session-only preference. */ }
}

async function startRealtimeSync() {
  if (disposed || !userStore.loggedIn) return
  const stopListener = await subscribeToSocketEvents((payload) => {
    const eventName = payload?.name || ''
    if (eventName.startsWith('agentstate') || eventName === 'agentsupdate') {
      void queueAgentReload()
      void queueJobReload()
      return
    }
    if (eventName.startsWith('jobstate') || eventName === 'jobsupdate') void queueJobReload()
  })
  if (disposed) stopListener()
  else stopRealtimeSocketListener = stopListener
}

async function logout() {
  await userStore.logout()
  router.push('/login')
}

onBeforeMount(async () => {
  if (!userStore.loggedIn) await userStore.getUser()
  await startRealtimeSync()
})
onBeforeUnmount(() => {
  disposed = true
  queueAgentReload.cancel()
  queueJobReload.cancel()
  stopRealtimeSocketListener?.()
})
defineOptions({ name: 'MainLayout' })
</script>
