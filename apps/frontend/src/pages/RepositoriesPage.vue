<template>
  <q-page class="q-pa-md">
    <PageHeader title="Repositories" description="Manage storage destinations for your backups.">
      <template #actions>
        <q-btn unelevated no-caps no-wrap color="primary" icon="add" label="Add Repository" @click="showCreateRepository" />
      </template>
    </PageHeader>
    <q-card v-if="repositoryStore.repositories.length === 0 && !repositoryStore.loading" flat bordered>
      <EmptyState icon="inventory_2" title="No repositories configured yet" description="Add a repository to choose where your backups will be stored." />
    </q-card>
    <q-table
      v-else
      hide-no-data
      :rows="repositoryStore.repositories"
      :columns="columns"
      :loading="repositoryStore.loading"
      :hide-header="repositoryStore.repositories.length === 0"
      :table-header-class="$q.dark.isActive ? 'bg-dark text-grey-4' : 'bg-grey-1 text-grey-7'"
      row-key="id"
      flat bordered
      :rows-per-page-options="[0]"
    >
      <template v-slot:body-cell-actions="props">
        <q-td :props="props">
          <TableActionButton icon="lock_open" label="Unlock repository" @click="showUnlock(props.row)" />
          <TableActionButton icon="fact_check" label="Check repository" @click="showCheck(props.row)" />
          <TableActionButton icon="visibility" label="Reveal repository password" @click="showReveal(props.row)" />
          <TableActionButton icon="edit" label="Edit repository" @click="showEditRepository(props.row)" />
          <TableActionButton icon="delete" label="Delete repository" color="negative" :loading="deletingRepoId === props.row.id" :disable="deletingRepoId !== null" @click="confirmDelete(props.row)" />
        </q-td>
      </template>
    </q-table>

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
            <q-select outlined v-model="unlockAgentId" :options="unlockAgents" option-label="display_name" option-value="id" emit-value map-options label="Select Agent" :rules="[val => !!val || 'Required']" :disable="unlocking" />
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Cancel" :disable="unlocking" v-close-popup />
            <q-btn unelevated no-caps label="Unlock" type="submit" color="primary" :loading="unlocking" :disable="unlocking" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <!-- Check Dialog -->
    <q-dialog v-model="showCheckDialog">
      <q-card class="db-dialog-card-sm">
        <q-card-section>
          <div class="text-h6">Check Repository</div>
          <div class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Run restic check on an online agent with access to this repository.</div>
        </q-card-section>
        <q-form @submit="onCheckSubmit">
          <q-card-section class="q-gutter-sm">
            <q-select outlined v-model="checkAgentId" :options="checkAgents" option-label="display_name" option-value="id" emit-value map-options label="Select Agent" :rules="[val => !!val || 'Required']" :disable="checking" />
            <q-input outlined v-model="checkReadDataSubset" label="Read data subset" hint="Optional, e.g. 1/10 or 5%" :disable="checking" />
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Cancel" :disable="checking" v-close-popup />
            <q-btn unelevated no-caps label="Start Check" type="submit" color="primary" :loading="checking" :disable="checking" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <!-- Reveal Password Dialog -->
    <q-dialog v-model="showRevealDialog" @hide="resetRevealDialog">
      <q-card class="db-dialog-card-sm">
        <q-card-section>
          <div class="text-h6">Reveal Repository Password</div>
          <div v-if="!revealedPassword" class="text-caption" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">Enter your account password to reveal the restic repository password.</div>
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
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Close" :disable="revealing" v-close-popup />
            <q-btn v-if="!revealedPassword" unelevated no-caps label="Reveal" type="submit" color="primary" :loading="revealing" :disable="revealing" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup>
import PageHeader from 'components/PageHeader.vue'
import EmptyState from 'components/EmptyState.vue'
import TableActionButton from 'components/TableActionButton.vue'
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
