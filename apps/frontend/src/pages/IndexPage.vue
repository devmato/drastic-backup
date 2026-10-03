<template>
  <q-page class="q-pa-md">
    <PageHeader title="Dashboard" description="Your backup workspace, at a glance.">
      <template #actions>
        <q-btn flat round color="primary" icon="refresh" aria-label="Refresh dashboard" :loading="loading" @click="loadDashboard">
          <q-tooltip>Refresh dashboard</q-tooltip>
        </q-btn>
        <q-btn flat no-caps no-wrap color="primary" icon="add" label="Add Agent" to="/agents/install" />
      </template>
    </PageHeader>

    <q-banner v-if="loadError" rounded class="bg-red-1 text-red-10 q-mb-lg" role="alert">
      {{ loadError }}
      <template #action><q-btn flat no-caps label="Retry" @click="loadDashboard" /></template>
    </q-banner>
    <div v-if="loading" class="flex flex-center q-py-xl"><q-spinner color="primary" size="40px" aria-label="Loading dashboard" /></div>

    <template v-else-if="!loadError">
      <div class="row q-col-gutter-md q-mb-md">
        <div v-for="metric in metrics" :key="metric.label" class="col-6 col-lg-3">
          <q-card flat bordered class="full-height">
            <q-item clickable :to="metric.to" class="full-height rounded-borders q-pa-md">
              <q-item-section top>
                <q-item-label class="text-body2" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">{{ metric.label }}</q-item-label>
                <q-item-label class="text-h4 text-weight-medium q-mt-sm">{{ metric.value }}</q-item-label>
              </q-item-section>
              <q-item-section v-if="$q.screen.gt.xs" side top>
                <q-icon :name="metric.icon" :color="metric.color" size="sm" />
              </q-item-section>
              <q-tooltip>{{ metric.description }}</q-tooltip>
            </q-item>
          </q-card>
        </div>
      </div>

      <div class="row items-start q-col-gutter-md">
        <div class="col-12 col-md-5 col-lg-4">
          <q-card flat bordered>
            <q-card-section>
              <div class="row items-center no-wrap">
                <q-icon name="donut_large" color="grey-6" size="sm" class="q-mr-sm" />
                <h2 class="text-subtitle1 text-weight-medium q-my-none">Current Backup Status</h2>
              </div>
              <div class="text-caption q-mt-xs" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Latest result per job</div>
            </q-card-section>
            <q-card-section class="q-pt-none"><BackupResultsChart :jobs="jobs" /></q-card-section>
          </q-card>
        </div>
        <div class="col-12 col-md-7 col-lg-8">
          <q-card v-if="setupPrompt" flat bordered>
            <q-card-section>
              <h2 class="text-subtitle1 text-weight-medium q-my-none">Getting Started</h2>
              <div class="text-body2 q-mt-xs" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Connect a system, choose your files and start backing up.</div>
            </q-card-section>
            <q-card-section>
              <q-list>
                <q-item class="q-px-none q-pt-none">
                  <q-item-section side>
                    <q-icon :name="onlineAgents.length ? 'check_circle_outline' : 'desktop_windows'" :color="onlineAgents.length ? 'positive' : 'grey-6'" />
                  </q-item-section>
                  <q-item-section>
                    <q-item-label class="text-weight-medium">{{ onlineAgents.length ? 'Agent connected' : 'Connect an agent' }}</q-item-label>
                    <q-item-label caption>{{ onlineAgents.length ? 'Your system is ready for backup configuration.' : 'Install an agent on the system you want to back up.' }}</q-item-label>
                  </q-item-section>
                </q-item>
                <q-item class="q-px-none q-mt-md">
                  <q-item-section side><q-icon :name="setupPrompt.icon" color="grey-6" /></q-item-section>
                  <q-item-section>
                    <q-item-label class="text-weight-medium">{{ setupPrompt.title }}</q-item-label>
                    <q-item-label caption>{{ setupPrompt.description }}</q-item-label>
                  </q-item-section>
                </q-item>
              </q-list>
            </q-card-section>
            <q-card-actions class="q-px-md q-pb-md q-pt-none">
              <q-btn unelevated no-caps no-wrap color="primary" :label="setupPrompt.label" :to="setupPrompt.to" />
            </q-card-actions>
          </q-card>
          <q-card v-else flat bordered>
            <q-card-section>
              <h2 class="text-subtitle1 text-weight-medium q-my-none">Needs Attention</h2>
              <div class="text-body2 q-mt-xs" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Offline agents, backup issues and jobs awaiting their first run.</div>
            </q-card-section>
            <EmptyState v-if="attentionItems.length === 0" icon="check_circle_outline" title="Nothing needs attention" description="No offline agents or outstanding backup issues." />
            <q-list v-else separator>
              <q-item v-for="item in attentionItems" :key="item.key" clickable :to="item.to" class="q-py-md">
                <q-item-section side><q-icon :name="item.icon" :color="item.color" /></q-item-section>
                <q-item-section>
                  <q-item-label class="ellipsis">{{ item.label }}</q-item-label>
                  <q-item-label caption>{{ item.description }}</q-item-label>
                </q-item-section>
                <q-item-section side><q-icon name="chevron_right" /></q-item-section>
              </q-item>
            </q-list>
          </q-card>
        </div>
      </div>
    </template>
  </q-page>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import PageHeader from 'components/PageHeader.vue'
import EmptyState from 'components/EmptyState.vue'
import BackupResultsChart from 'components/BackupResultsChart.vue'
import { useAgentStore } from 'stores/agent'
import { useJobStore } from 'stores/job'
import { backupStates, getBackupState } from 'src/utils/backup-results'
import { getApiErrorMessage } from 'src/utils/api-error'

const agentStore = useAgentStore()
const jobStore = useJobStore()
const loading = ref(true)
const loadError = ref('')
const jobs = computed(() => jobStore.agentJobs.flatMap(agent => agent.jobs))
const onlineAgents = computed(() => agentStore.agents.filter(agent => agent.online))
const setupPrompt = computed(() => {
  if (jobs.value.length > 0) return null
  const onlineAgent = onlineAgents.value[0]
  if (onlineAgent) {
    return { icon: 'backup', title: 'Create your first backup job', description: 'Choose what to back up and where to store it.', label: 'Set Up Backup', to: { path: '/jobs', query: { add_for_agent: String(onlineAgent.id) } } }
  }
  if (agentStore.agents.length > 0) {
    return { icon: 'desktop_access_disabled', title: 'Bring your agent online to get started.', description: 'Your agents are offline. Connect an agent before setting up your first backup.', label: 'View Agents', to: '/agents' }
  }
  return { icon: 'desktop_windows', title: 'Your first backup starts with an agent.', description: 'Connect a system to choose what to back up. No backup jobs are configured yet.', label: 'Add Agent', to: '/agents/install' }
})
const running = computed(() => jobs.value.filter(job => getBackupState(job) === 'running').length)
const issues = computed(() => jobs.value.filter(job => ['warning', 'failed'].includes(getBackupState(job))).length)
const metrics = computed(() => [
  { label: 'Agents online', value: `${onlineAgents.value.length} / ${agentStore.agents.length}`, description: 'Connected / registered systems', icon: 'desktop_windows', color: 'grey-6', to: '/agents' },
  { label: 'Backup jobs', value: jobs.value.length, description: 'Configured across all agents', icon: 'backup', color: 'grey-6', to: '/jobs' },
  { label: 'Running backups', value: running.value, description: 'Jobs currently in progress', icon: 'sync', color: 'grey-6', to: { path: '/jobs', query: { last_state: 'running' } } },
  { label: 'Warnings / failures', value: issues.value, description: 'Based on each job’s latest backup', icon: 'error_outline', color: issues.value ? 'negative' : 'grey-6', to: { path: '/jobs', query: { last_state: 'attention' } } },
])
const attentionItems = computed(() => [
  ...jobs.value.filter(job => ['warning', 'failed'].includes(getBackupState(job))).map(job => {
    const state = backupStates.find(state => state.value === getBackupState(job))
    return { key: `job-${job.id}`, label: job.name, description: `Latest backup: ${state.label}`, icon: state.icon, color: state.color, to: `/agents/${job.agent_id}/operations/${job.last_operation.id}` }
  }),
  ...agentStore.agents.filter(agent => !agent.online).map(agent => ({ key: `agent-${agent.id}`, label: agent.display_name, description: 'Agent is offline', icon: 'desktop_access_disabled', color: 'negative', to: `/agents/${agent.id}` })),
  ...jobs.value.filter(job => getBackupState(job) === 'never').map(job => ({ key: `job-${job.id}`, label: job.name, description: 'No backup has run yet', icon: 'schedule', color: 'blue-grey-4', to: { path: '/jobs', query: { last_state: 'never' } } })),
])

async function loadDashboard() {
  loading.value = true
  loadError.value = ''
  try {
    await Promise.all([agentStore.loadAgents(), jobStore.loadJobs()])
  } catch (error) {
    loadError.value = getApiErrorMessage(error, 'Could not load dashboard')
  } finally {
    loading.value = false
  }
}

onMounted(loadDashboard)

defineOptions({ name: 'IndexPage' })
</script>
