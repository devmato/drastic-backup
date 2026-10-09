<template>
  <div>
    <div v-if="store.chains.length" class="row justify-end q-mb-md">
      <q-btn unelevated no-caps color="primary" icon="add" label="Add Backup Chain" :disable="!jobs.length" @click="editChain()" />
    </div>
    <q-card v-if="loadError" flat bordered class="q-mb-md">
      <EmptyState icon="error_outline" title="Could not load backup chains" :description="loadError">
        <q-btn outline no-caps color="primary" icon="refresh" label="Retry" :loading="loading" @click="retryLoad" />
      </EmptyState>
    </q-card>
    <q-card v-else-if="!store.chains.length && !loading" flat bordered>
      <EmptyState icon="playlist_play" title="No backup chains" description="Back up your jobs in a fixed order.">
        <q-btn unelevated no-caps color="primary" icon="add" label="Add Backup Chain" :disable="!jobs.length" @click="editChain()" />
      </EmptyState>
    </q-card>
    <q-card v-for="chain in store.chains" :key="chain.id" flat bordered class="q-mb-md">
      <q-card-section>
        <div class="row items-center q-col-gutter-sm">
          <div class="col-12 col-sm">
            <div class="text-subtitle1 text-weight-medium">{{ chain.name }} <q-badge :color="chain.enabled ? 'positive' : 'grey'" text-color="black" :label="chain.enabled ? 'Scheduled' : 'Manual only'" /></div>
            <div class="text-body2">{{ chain.steps.length }} steps · {{ chain.schedules.length }} schedules</div>
            <div v-for="(schedule, index) in chain.schedules" :key="index" class="text-body2">{{ schedule.cron_description }} UTC{{ schedule.enabled ? '' : ' · Disabled' }}</div>
            <div v-if="chain.last_run" class="text-body2 q-mt-xs">
              Latest: <q-badge :color="stateColor(chain.last_run.state)" :text-color="['success', 'warning'].includes(chain.last_run.state) ? 'black' : 'white'" :label="stateLabel(chain.last_run.state)" /> · {{ formatDate(chain.last_run.started) }}
            </div>
            <div v-if="chain.active_run_id" class="text-body2 q-mt-xs">{{ activeStepLabel(chain) }}</div>
          </div>
          <div class="col-12 col-sm-auto row items-center q-gutter-xs">
            <q-btn v-if="chain.active_run_id" flat no-caps icon="cancel" color="negative" label="Cancel Chain" @click="confirmCancel(chain)" />
            <q-btn v-else flat no-caps icon="play_arrow" color="primary" label="Start Chain" :loading="busyId === chain.id" @click="perform(() => store.startChain(chain.id), chain.id)" />
            <TableActionButton icon="receipt_long" label="Chain history" @click="openHistory(chain)" />
            <TableActionButton icon="edit" label="Edit chain" @click="editChain(chain)" />
            <TableActionButton icon="delete" label="Delete chain" color="negative" :disable="!!chain.active_run_id" @click="confirmDelete(chain)" />
          </div>
        </div>
      </q-card-section>
      <q-separator />
      <q-list dense separator>
        <q-item v-for="(step, index) in chain.steps" :key="`${step.job_id}:${step.repository_id}`">
          <q-item-section side>{{ index + 1 }}.</q-item-section>
          <q-item-section>
            <q-item-label>{{ step.job_name }} · {{ step.agent_name }}</q-item-label>
            <q-item-label caption>{{ step.repository_name }} · Retention: {{ step.retention_name || 'None' }}</q-item-label>
          </q-item-section>
        </q-item>
      </q-list>
    </q-card>
    <q-inner-loading :showing="loading" />

    <q-dialog v-model="editing" persistent :maximized="$q.screen.lt.sm">
      <q-card class="app-dialog-wide db-job-form-dialog column no-wrap overflow-hidden">
        <q-card-section class="col-auto"><div class="text-h6">{{ editingId ? 'Edit Backup Chain' : 'Add Backup Chain' }}</div></q-card-section>
        <q-form class="column no-wrap col" @submit="save">
          <q-card-section class="col-shrink scroll q-pt-none">
            <div class="q-gutter-y-md">
              <q-input v-model="form.name" outlined hide-bottom-space :dense="!$q.platform.has.touch" label="Chain Name" :rules="[value => !!value?.trim() || 'Required']" maxlength="255" />
              <q-input v-model.number="form.start_timeout_minutes" outlined :dense="!$q.platform.has.touch" type="number" label="Maximum wait for each job to start (minutes)" hint="Offline or busy jobs are skipped after this time. Running or unconfirmed jobs are never skipped just because connectivity is lost." :rules="[value => Number.isInteger(value) && value >= 1 && value <= 1440 || 'Use 1–1440 minutes']" />
              <div class="text-subtitle1 q-mt-md">Jobs in execution order</div>
              <q-card v-for="(step, index) in form.steps" :key="step.key" flat bordered>
                <q-card-section>
                  <div class="row items-center q-mb-sm">
                    <div class="col text-subtitle2">Step {{ index + 1 }}</div>
                    <q-btn flat round dense icon="arrow_upward" :disable="index === 0" :aria-label="`Move step ${index + 1} up`" @click="moveStep(index, -1)" />
                    <q-btn flat round dense icon="arrow_downward" :disable="index === form.steps.length - 1" :aria-label="`Move step ${index + 1} down`" @click="moveStep(index, 1)" />
                    <q-btn flat round dense icon="delete" color="negative" :aria-label="`Remove step ${index + 1}`" @click="form.steps.splice(index, 1)" />
                  </div>
                  <div class="row q-col-gutter-md">
                    <div class="col-12"><q-select v-model="step.job_id" outlined hide-bottom-space :dense="!$q.platform.has.touch" :options="jobOptions" label="Backup Job / Agent" emit-value map-options :rules="[value => !!value || 'Required']" @update:model-value="selectJob(step)" /></div>
                    <div class="col-12 col-sm-6"><q-select v-model="step.repository_id" outlined hide-bottom-space :dense="!$q.platform.has.touch" :options="repositoryOptions" label="Repository" emit-value map-options :rules="[value => !!value || 'Required', value => !form.steps.some(other => other.key !== step.key && other.job_id === step.job_id && other.repository_id === value) || 'This job already has a step for this repository']" /></div>
                    <div class="col-12 col-sm-6"><q-select v-model="step.retention_id" outlined :dense="!$q.platform.has.touch" :options="retentionOptions" label="Retention Policy" emit-value map-options /></div>
                    <div class="col-12"><q-toggle v-model="step.config.repository_check.enabled" label="Check repository after successful backup" /></div>
                    <div v-if="step.config.repository_check.enabled" class="col-12"><q-input v-model="step.config.repository_check.read_data" outlined :dense="!$q.platform.has.touch" label="Read data" hint="Leave empty for a basic check; e.g. 1/10, 5% or 100%." /></div>
                    <div v-if="selectedJob(step)?.schedules?.length" class="col-12 text-caption">This job also has independent schedules. Adding it to this chain keeps those schedules active.</div>
                  </div>
                </q-card-section>
              </q-card>
              <q-btn outline no-caps icon="add" color="primary" label="Add Job" :disable="!jobs.length || form.steps.length >= 100" @click="form.steps.push(newStep())" />
              <div class="text-caption">Jobs require agent protocol 11 or newer. Retention applies to this job's backups in the selected repository, including backups made outside this chain.</div>
              <q-separator class="q-my-md" />
              <div class="text-subtitle1">Schedules</div>
              <SchedulesPanel v-model="form.schedules" time-only timezone="UTC" />
              <div class="text-caption">Each enabled schedule starts the entire chain. Without enabled schedules, run it manually with Start Chain.</div>
            </div>
          </q-card-section>
          <q-card-actions align="right" class="col-auto q-pa-md">
            <q-btn flat no-caps label="Cancel" :disable="saving" @click="editing = false" />
            <q-btn unelevated no-caps color="primary" label="Save" type="submit" :loading="saving" :disable="!form.steps.length" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <q-dialog v-model="historyVisible" :maximized="$q.screen.lt.sm">
      <q-card class="app-dialog-wide">
        <q-card-section class="row items-center">
          <div class="col text-h6">{{ historyChain?.name }} — History</div>
          <q-btn flat round icon="close" aria-label="Close history" v-close-popup />
        </q-card-section>
        <q-list separator>
          <q-expansion-item v-for="run in history" :key="run.id" :default-opened="run.state === 'running'">
            <template #header>
              <q-item-section><q-item-label>{{ formatDate(run.started) }}</q-item-label><q-item-label caption>Run #{{ run.id }}{{ run.cancel_requested ? ' · Cancellation requested' : '' }}</q-item-label></q-item-section>
              <q-item-section side><q-badge :color="stateColor(run.state)" :text-color="['success', 'warning'].includes(run.state) ? 'black' : 'white'" :label="stateLabel(run.state)" /></q-item-section>
            </template>
            <q-list dense separator>
              <q-item v-for="(step, index) in run.steps" :key="index">
                <q-item-section side>{{ index + 1 }}.</q-item-section>
                <q-item-section>
                  <q-item-label>{{ step.job_name }} · {{ step.agent_name }}</q-item-label>
                  <q-item-label caption>{{ step.repository_name }} · Retention: {{ step.retention_name || 'None' }}</q-item-label>
                  <q-item-label v-if="step.reason" caption>{{ step.reason }}</q-item-label>
                  <q-item-label v-if="step.cleanup_pending" caption>Retention / cleanup pending retry</q-item-label>
                  <q-item-label v-if="step.start_deadline && ['waiting', 'dispatching'].includes(step.state)" caption>Start deadline: {{ formatDate(step.start_deadline) }}</q-item-label>
                  <q-btn v-if="step.operation_id" flat dense no-caps color="primary" icon="receipt_long" label="Job Protocol" :to="`/agents/${step.agent_id}/operations/${step.operation_id}`" />
                </q-item-section>
                <q-item-section side><q-badge :color="stateColor(step.state)" :text-color="['success', 'warning'].includes(step.state) ? 'black' : 'white'" :label="stateLabel(step.state)" /></q-item-section>
              </q-item>
            </q-list>
          </q-expansion-item>
        </q-list>
        <q-card-section v-if="!history.length" class="text-body2">No runs yet.</q-card-section>
        <q-card-actions align="right">
          <q-btn v-if="moreHistory" flat no-caps label="Older Runs" :loading="historyLoading" @click="loadOlderRuns" />
          <q-btn flat no-caps label="Close" v-close-popup />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuasar } from 'quasar'
import EmptyState from 'components/EmptyState.vue'
import TableActionButton from 'components/TableActionButton.vue'
import SchedulesPanel from 'components/SchedulesPanel.vue'
import { useChainStore } from 'stores/chain'
import { useRetentionStore } from 'stores/retention'
import { useUserStore } from 'stores/user'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import { scheduleTiming } from 'src/utils/schedule'

const props = defineProps({
  agentJobs: { type: Array, default: () => [] },
  agents: { type: Array, default: () => [] },
  repositories: { type: Array, default: () => [] },
})
const emit = defineEmits(['changed'])
const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const store = useChainStore()
const retentionStore = useRetentionStore()
const userStore = useUserStore()
const jobs = computed(() => props.agentJobs.flatMap(agent => agent.jobs.map(job => ({ ...job, agent_name: agent.display_name }))))
const repositoryOptions = computed(() => props.repositories.map(repo => ({ label: repo.name, value: repo.id })))
const retentionOptions = computed(() => [{ label: 'None', value: null }, ...retentionStore.retentions.map(item => ({ label: item.name, value: item.id }))])
const loading = ref(true)
const loadError = ref('')
const busyId = ref(null)
const editing = ref(false)
const editingId = ref(null)
const saving = ref(false)
const form = reactive({ name: '', start_timeout_minutes: 60, steps: [], schedules: [] })
const historyVisible = ref(false)
const historyChain = ref(null)
const history = ref([])
const historyLoading = ref(false)
const moreHistory = ref(false)
let timer
let stopped = false
let stepKey = 0

function newStep(step = {}) {
  return { key: ++stepKey, job_id: step.job_id || null, repository_id: step.repository_id || null, retention_id: step.retention_id || null,
    config: { repository_check: { enabled: Boolean(step.config?.repository_check?.enabled), read_data: step.config?.repository_check?.read_data || null } } }
}
function selectedJob(step) { return jobs.value.find(job => job.id === step.job_id) }
function selectJob(step) {
  const job = selectedJob(step)
  step.repository_id = job?.agent_repositories?.[0]?.id || null
}
const jobOptions = computed(() => jobs.value.map(job => {
  const protocol = props.agents.find(agent => agent.id === job.agent_id)?.protocol_version || 0
  return { label: `${job.name} · ${job.agent_name}${protocol < 11 ? ' (update agent required)' : ''}`, value: job.id, disable: protocol < 11 }
}))
function moveStep(index, offset) {
  const [step] = form.steps.splice(index, 1)
  form.steps.splice(index + offset, 0, step)
}
function editChain(chain = null) {
  editingId.value = chain?.id || null
  Object.assign(form, { name: chain?.name || '', start_timeout_minutes: chain?.start_timeout_minutes || 60,
    schedules: (chain?.schedules || []).map((schedule, index) => ({ ...schedule, id: index + 1, timing: scheduleTiming(schedule) })),
    steps: chain ? chain.steps.map(newStep) : [newStep()] })
  editing.value = true
}
async function save() {
  saving.value = true
  try {
    await userStore.withRecoveryKey(recoveryKey => store.saveChain(editingId.value, {
      ...form, recovery_key: recoveryKey,
      schedules: form.schedules.map(({ timing, enabled }) => ({ timing, enabled })),
      steps: form.steps.map(({ job_id, repository_id, retention_id, config }) => ({ job_id, repository_id, retention_id, config })),
    }))
    editing.value = false
    emit('changed')
    $q.notify({ message: 'Backup chain saved', color: 'positive' })
  } catch (error) { showError(error) }
  finally { saving.value = false }
}
function showError(error) {
  if (!shouldIgnoreApiError(error)) $q.notify({ message: getApiErrorMessage(error), color: 'negative' })
}
async function perform(action, id) {
  busyId.value = id
  try { await action(); emit('changed') }
  catch (error) { showError(error) }
  finally { busyId.value = null }
}
function confirmDelete(chain) {
  $q.dialog({ title: 'Delete Backup Chain', message: `Delete "${chain.name}" and its chain history? Individual job protocols are retained.`, cancel: true }).onOk(() => perform(() => store.deleteChain(chain.id), chain.id))
}
function confirmCancel(chain) {
  $q.dialog({ title: 'Cancel Backup Chain', message: 'Request cancellation of the running job and stop all remaining steps?', cancel: true }).onOk(() => perform(() => store.cancelRun(chain.id, chain.active_run_id), chain.id))
}
function formatDate(value) { return value ? new Date(value).toLocaleString() : '—' }
function stateLabel(state) {
  return ({ pending: 'Pending', waiting: 'Waiting', dispatching: 'Start unconfirmed', running: 'Running', success: 'Success', warning: 'Warning', failed: 'Failed', cancelled: 'Cancelled', skipped: 'Skipped' })[state] || state
}
function stateColor(state) {
  return ({ success: 'positive', warning: 'warning', failed: 'negative', running: 'primary', dispatching: 'warning' })[state] || 'grey-7'
}
function activeStepLabel(chain) {
  if (chain.last_run?.id !== chain.active_run_id) return 'An earlier chain run is still active'
  if (chain.last_run.cancel_requested) return 'Cancellation requested; waiting for the active job to finish'
  const index = chain.last_run.steps.findIndex(step => ['waiting', 'dispatching', 'running'].includes(step.state))
  if (index < 0) return 'Preparing next step'
  const step = chain.last_run.steps[index]
  return `Step ${index + 1}/${chain.steps.length}: ${step.job_name} · ${stateLabel(step.state)}${step.reason ? ` · ${step.reason}` : ''}`
}
async function openHistory(chain) {
  historyChain.value = chain
  history.value = []
  historyVisible.value = true
  await refreshHistory()
}
async function refreshHistory() {
  if (!historyChain.value) return
  try {
    const runs = await store.getRuns(historyChain.value.id)
    const older = history.value.filter(run => !runs.some(item => item.id === run.id))
    history.value = [...runs, ...older]
    if (!older.length) moreHistory.value = runs.length === 25
  } catch (error) { loadError.value = getApiErrorMessage(error) }
}
async function loadOlderRuns() {
  historyLoading.value = true
  try {
    const runs = await store.getRuns(historyChain.value.id, history.value[history.value.length - 1]?.id)
    history.value.push(...runs)
    moreHistory.value = runs.length === 25
  } catch (error) { showError(error) }
  finally { historyLoading.value = false }
}
function openLinkedChain() {
  const chain = store.chains.find(item => String(item.id) === route.query.chain_id)
  if (chain && !editing.value) editChain(chain)
}
function retryLoad() {
  clearTimeout(timer)
  loading.value = true
  return refresh()
}
async function refresh() {
  try {
    await store.loadChains()
    loadError.value = ''
    if (historyVisible.value) await refreshHistory()
  } catch (error) { loadError.value = getApiErrorMessage(error, 'Please try again in a moment.') }
  finally { loading.value = false }
  if (!stopped) timer = setTimeout(refresh, 5000)
}
watch(() => route.query.chain_id, openLinkedChain)
watch(editing, async value => {
  if (!value && route.query.chain_id) await router.replace({ query: { ...route.query, chain_id: undefined } })
})
onMounted(async () => {
  await retentionStore.loadRetentions()
  await refresh()
  openLinkedChain()
})
onBeforeUnmount(() => { stopped = true; clearTimeout(timer) })
</script>
