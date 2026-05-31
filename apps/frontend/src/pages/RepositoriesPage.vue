<template>
  <q-page class="q-pa-md">
    <div class="text-h5 q-mb-md">Repositories</div>
    <q-table
      :rows="repositoryStore.repositories"
      :columns="columns"
      :loading="repositoryStore.loading"
      row-key="id"
      flat bordered
      :rows-per-page-options="[0]"
      no-data-label="No repositories configured"
    >
      <template v-slot:body-cell-actions="props">
        <q-td :props="props">
          <q-btn flat dense icon="lock_open" @click="showUnlock(props.row)">
            <q-tooltip>Unlock repository</q-tooltip>
          </q-btn>
          <q-btn flat dense icon="fact_check" color="primary" @click="showCheck(props.row)">
            <q-tooltip>Check repository</q-tooltip>
          </q-btn>
          <q-btn flat dense icon="visibility" color="grey-8" @click="showReveal(props.row)">
            <q-tooltip>Reveal repository password</q-tooltip>
          </q-btn>
          <q-btn flat dense icon="edit" color="grey-8" @click="showEditRepository(props.row)">
            <q-tooltip>Edit repository</q-tooltip>
          </q-btn>
          <q-btn flat dense icon="delete" color="red" :loading="deletingRepoId === props.row.id" :disable="deletingRepoId !== null" @click="confirmDelete(props.row)">
            <q-tooltip>Delete repository</q-tooltip>
          </q-btn>
        </q-td>
      </template>
    </q-table>

    <q-page-sticky position="bottom-right" :offset="[35, 35]">
      <q-btn fab icon="add" color="primary" @click="showCreateRepository" />
    </q-page-sticky>

    <RepositoryManageDialog
      v-model="showManageDialog"
      :repository="managedRepository"
      :submitting="managing"
      @submit="onManageSubmit"
    />

    <!-- Unlock Dialog -->
    <q-dialog v-model="showUnlockDialog">
      <q-card class="db-dialog-card-sm">
        <q-card-section><div class="text-h6">Unlock Repository</div></q-card-section>
        <q-form @submit="onUnlockSubmit">
          <q-card-section>
            <q-select outlined v-model="unlockAgentId" :options="unlockAgents" option-label="hostname" option-value="id" emit-value map-options label="Select Agent" :rules="[val => !!val || 'Required']" :disable="unlocking" />
          </q-card-section>
          <q-card-actions align="right">
            <q-btn flat label="Cancel" :disable="unlocking" v-close-popup />
            <q-btn label="Unlock" type="submit" color="primary" :loading="unlocking" :disable="unlocking" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <!-- Check Dialog -->
    <q-dialog v-model="showCheckDialog">
      <q-card class="db-dialog-card-sm">
        <q-card-section>
          <div class="text-h6">Check Repository</div>
          <div class="text-caption text-grey-7">Run restic check on an online agent with access to this repository.</div>
        </q-card-section>
        <q-form @submit="onCheckSubmit">
          <q-card-section class="q-gutter-sm">
            <q-select outlined v-model="checkAgentId" :options="checkAgents" option-label="hostname" option-value="id" emit-value map-options label="Select Agent" :rules="[val => !!val || 'Required']" :disable="checking" />
            <q-input outlined v-model="checkReadDataSubset" label="Read data subset" hint="Optional, e.g. 1/10 or 5%" :disable="checking" />
          </q-card-section>
          <q-card-actions align="right">
            <q-btn flat label="Cancel" :disable="checking" v-close-popup />
            <q-btn label="Start Check" type="submit" color="primary" :loading="checking" :disable="checking" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <!-- Reveal Password Dialog -->
    <q-dialog v-model="showRevealDialog" @hide="resetRevealDialog">
      <q-card class="db-dialog-card-sm">
        <q-card-section>
          <div class="text-h6">Reveal Repository Password</div>
          <div v-if="!revealedPassword" class="text-caption text-grey-7">Enter your account password to reveal the restic repository password.</div>
        </q-card-section>
        <q-form @submit="onRevealSubmit">
          <q-card-section class="q-gutter-sm">
            <q-input
              v-if="!revealedPassword"
              outlined
              v-model="revealAccountPassword"
              type="password"
              label="Account Password"
              autocomplete="current-password"
              :rules="[val => !!val || 'Required']"
              :disable="revealing"
            />
            <q-input
              v-if="revealedPassword"
              readonly
              outlined
              :model-value="revealedPassword"
              type="text"
              label="Repository Password"
            >
              <template #append>
                <q-btn flat dense icon="content_copy" @click="copyRevealedPassword">
                  <q-tooltip>Copy password</q-tooltip>
                </q-btn>
              </template>
            </q-input>
            <q-banner v-if="revealedPassword" dense rounded class="bg-amber-1 text-amber-10">
              Store this password securely if you need to import the repository into another dRastic instance.
            </q-banner>
          </q-card-section>
          <q-card-actions align="right">
            <q-btn flat label="Close" :disable="revealing" v-close-popup />
            <q-btn v-if="!revealedPassword" label="Reveal" type="submit" color="primary" :loading="revealing" :disable="revealing" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { copyToClipboard, useQuasar } from 'quasar'
import { useRepositoryStore } from 'stores/repository'
import { useUserStore } from 'stores/user'
import { useOperationStore } from 'stores/operation'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import RepositoryManageDialog from 'components/repositories/RepositoryManageDialog.vue'

const $q = useQuasar()
const repositoryStore = useRepositoryStore()
const userStore = useUserStore()
const operationStore = useOperationStore()

const showManageDialog = ref(false)
const showUnlockDialog = ref(false)
const showCheckDialog = ref(false)
const showRevealDialog = ref(false)
const managedRepository = ref(null)
const unlockAgents = ref([])
const unlockAgentId = ref(null)
const unlockRepoId = ref(null)
const checkAgents = ref([])
const checkAgentId = ref(null)
const checkRepoId = ref(null)
const checkReadDataSubset = ref('')
const revealRepoId = ref(null)
const revealAccountPassword = ref('')
const revealedPassword = ref('')
const managing = ref(false)
const unlocking = ref(false)
const checking = ref(false)
const revealing = ref(false)
const deletingRepoId = ref(null)

const columns = [
  { name: 'id', label: '#', field: 'id', align: 'left', sortable: true },
  { name: 'name', label: 'Name', field: 'name', align: 'left', sortable: true },
  { name: 'kind', label: 'Type', field: 'kind', align: 'left', sortable: true },
  { name: 'restic_id', label: 'Restic-ID', field: 'restic_id', align: 'left' },
  { name: 'location', label: 'Location', field: row => row.kind === 'native' ? row.repository_path || row.location : row.location, align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

function showCreateRepository() {
  managedRepository.value = null
  showManageDialog.value = true
}

function showEditRepository(repository) {
  managedRepository.value = repository
  showManageDialog.value = true
}

async function onManageSubmit(payload) {
  const isCreate = !managedRepository.value
  const needsRecoveryKey = isCreate || Boolean(payload.password)

  managing.value = true
  try {
    const submitRepository = async (recoveryKey = null) => {
      const requestPayload = { ...payload }
      if (recoveryKey) {
        requestPayload.recovery_key = recoveryKey
      }

      if (isCreate) {
        await repositoryStore.createRepository(requestPayload)
        $q.notify({ message: 'Repository created', color: 'green', position: 'top' })
      } else {
        await repositoryStore.updateRepository(managedRepository.value.id, requestPayload)
        $q.notify({ message: 'Repository updated', color: 'green', position: 'top' })
      }
    }

    if (needsRecoveryKey) {
      await userStore.withRecoveryKey(submitRepository, { require: true })
    } else {
      await submitRepository()
    }

    showManageDialog.value = false
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  } finally {
    managing.value = false
  }
}

function showReveal(repo) {
  revealRepoId.value = repo.id
  showRevealDialog.value = true
}

function resetRevealDialog() {
  revealRepoId.value = null
  revealAccountPassword.value = ''
  revealedPassword.value = ''
  revealing.value = false
}

async function onRevealSubmit() {
  revealing.value = true
  try {
    revealedPassword.value = await repositoryStore.revealRepositoryPassword(revealRepoId.value, revealAccountPassword.value)
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  } finally {
    revealing.value = false
  }
}

async function copyRevealedPassword() {
  try {
    await copyToClipboard(revealedPassword.value)
    $q.notify({ message: 'Password copied', color: 'green', position: 'top' })
  } catch {
    $q.notify({ message: 'Could not copy password', color: 'red', position: 'top' })
  }
}

function confirmDelete(repo) {
  const isNative = repo.kind === 'native'

  const dialogOptions = {
    title: isNative ? 'Delete Managed Repository' : 'Delete Repository',
    message: isNative
      ? `Delete repository ${repo.name}? This removes the configuration and permanently deletes all data in the integrated rest-server. Type the repository name to continue.`
      : `Remove repository: ${repo.name}? This only removes it from the configuration.`,
    cancel: true,
    persistent: isNative,
  }

  if (isNative) {
    dialogOptions.prompt = {
      model: '',
      type: 'text',
      label: repo.name,
      isValid: value => value === repo.name,
    }
  }

  $q.dialog(dialogOptions).onOk(async () => {
    deletingRepoId.value = repo.id
    try {
      await repositoryStore.deleteRepository(repo.id)
      $q.notify({ message: 'Repository deleted', color: 'green', position: 'top' })
    } catch (e) {
      if (shouldIgnoreApiError(e)) return
      $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
    } finally {
      deletingRepoId.value = null
    }
  })
}

async function showUnlock(repo) {
  try {
    unlockAgents.value = await repositoryStore.getUnlockAgents(repo.id)
    if (unlockAgents.value.length === 0) {
      $q.notify({ message: 'No online agents with this repository available', color: 'orange', position: 'top' })
      return
    }
    unlockRepoId.value = repo.id
    unlockAgentId.value = null
    showUnlockDialog.value = true
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Error loading agents'), color: 'red', position: 'top' })
  }
}

async function showCheck(repo) {
  try {
    checkAgents.value = await repositoryStore.getUnlockAgents(repo.id)
    if (checkAgents.value.length === 0) {
      $q.notify({ message: 'No online agents with this repository available', color: 'orange', position: 'top' })
      return
    }
    checkRepoId.value = repo.id
    checkAgentId.value = null
    checkReadDataSubset.value = ''
    showCheckDialog.value = true
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Error loading agents'), color: 'red', position: 'top' })
  }
}

async function onUnlockSubmit() {
  unlocking.value = true
  try {
    const result = await operationStore.startRepositoryUnlock({ repositoryId: unlockRepoId.value, agentId: unlockAgentId.value })
    showUnlockDialog.value = false
    $q.notify({ message: result.msg, color: 'green', position: 'top' })
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  } finally {
    unlocking.value = false
  }
}

async function onCheckSubmit() {
  checking.value = true
  try {
    const result = await operationStore.startRepositoryCheck({
      repositoryId: checkRepoId.value,
      agentId: checkAgentId.value,
      readDataSubset: checkReadDataSubset.value.trim(),
    })
    showCheckDialog.value = false
    $q.notify({ message: result.msg || 'Repository check started', color: 'green', position: 'top' })
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  } finally {
    checking.value = false
  }
}

onMounted(() => {
  repositoryStore.loadRepositories()
})

defineOptions({ name: 'RepositoriesPage' })
</script>
