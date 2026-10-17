<template>
  <q-dialog v-model="dialogVisible" :persistent="submitting">
    <q-card class="db-dialog-card-sm">
      <q-card-section><div class="text-h6">Perform backup</div></q-card-section>
      <q-form @submit="submitBackup">
        <q-card-section class="q-gutter-sm">
          <div v-if="jobs.length === 0" role="status">No backup jobs configured for this agent. Add a job on the Backup Jobs page first.</div>
          <q-select v-else outlined :dense="!$q.platform.has.touch" hide-bottom-space
            v-model="selectedJobId" :options="jobOptions" label="Backup job" emit-value map-options
            :disable="submitting || jobId != null" :rules="[val => !!val || 'Required']" />
          <q-checkbox v-model="showAllRepositories" dense label="Show all repositories" :disable="submitting" />
          <q-select outlined :dense="!$q.platform.has.touch" hide-bottom-space
            v-model="selectedRepositoryId" :options="repositoryOptions" label="Target Repository" emit-value map-options
            :disable="submitting" :rules="[val => !!val || 'Required']" />
        </q-card-section>
        <q-card-actions align="right" class="q-pa-md">
          <q-btn flat no-caps label="Cancel" :disable="submitting" v-close-popup />
          <q-btn unelevated no-caps label="Start Backup" type="submit" color="primary" :loading="submitting" :disable="!canStart" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useJobStore } from 'stores/job'
import { useRepositoryStore } from 'stores/repository'
import { useUserStore } from 'stores/user'
import { useOperationStore } from 'stores/operation'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'

const props = defineProps({
  agent: { type: Object, default: null },
  jobId: { type: Number, default: null },
})
const dialogVisible = defineModel({ type: Boolean, required: true })
const emit = defineEmits(['started'])
const $q = useQuasar()
const jobStore = useJobStore()
const repositoryStore = useRepositoryStore()
const userStore = useUserStore()
const operationStore = useOperationStore()
const submitting = ref(false)
const selectedJobId = ref(null)
const selectedRepositoryId = ref(null)
const showAllRepositories = ref(false)

const jobs = computed(() => jobStore.agentJobs.find(agent => agent.id === props.agent?.id)?.jobs || [])
const jobOptions = computed(() => jobs.value.map(job => ({
  label: job.name, value: job.id, disable: job.last_operation?.state === 'running',
})))
const repositoryOptions = computed(() => (showAllRepositories.value ? repositoryStore.repositories : props.agent?.repositories || []).map(repository => ({
  label: `${repository.name} (${repository.location})`, value: repository.id,
})))
const canStart = computed(() => Boolean(props.agent?.online)
  && jobOptions.value.some(option => option.value === selectedJobId.value && !option.disable)
  && repositoryOptions.value.some(option => option.value === selectedRepositoryId.value))

watch(dialogVisible, visible => {
  if (!visible) return
  showAllRepositories.value = false
  selectedJobId.value = props.jobId ?? jobOptions.value.find(option => !option.disable)?.value ?? null
  selectedRepositoryId.value = repositoryOptions.value[0]?.value || null
}, { immediate: true })

watch(repositoryOptions, options => {
  if (!options.some(option => option.value === selectedRepositoryId.value)) selectedRepositoryId.value = options[0]?.value || null
})

async function submitBackup() {
  if (!dialogVisible.value || !canStart.value || submitting.value) return
  const agentId = props.agent.id
  const jobId = selectedJobId.value
  const repositoryId = selectedRepositoryId.value
  submitting.value = true
  try {
    const result = await userStore.withRecoveryKey(recoveryKey => operationStore.startBackupJob({ jobId, repositoryId, recoveryKey }))
    dialogVisible.value = false
    $q.notify({ message: result.msg || 'Backup job started', color: result.dispatch_status === 'unknown' ? 'warning' : 'positive' })
    emit('started', { ...result, agent_id: agentId })
  } catch (error) {
    if (!shouldIgnoreApiError(error)) $q.notify({ message: getApiErrorMessage(error), color: 'negative' })
  } finally {
    submitting.value = false
  }
}

defineOptions({ name: 'RunBackupDialog' })
</script>
