<template>
  <q-page class="q-pa-md">
    <PageHeader title="Retention Policies" description="Define how long backup snapshots are kept.">
      <template #actions>
        <q-btn unelevated no-caps no-wrap color="primary" icon="add" label="Add Retention Policy" @click="showManageDialog(null)" />
      </template>
    </PageHeader>
    <q-card v-if="retentionStore.retentions.length === 0 && !retentionStore.loading" flat bordered>
      <EmptyState icon="recycling" title="No retention policies configured yet" description="Add a policy to manage snapshot retention for your backup schedules." />
    </q-card>
    <q-table
      v-else
      hide-no-data
      :rows="retentionStore.retentions"
      :columns="columns"
      :loading="retentionStore.loading"
      :hide-header="retentionStore.retentions.length === 0"
      :table-header-class="$q.dark.isActive ? 'bg-dark text-grey-4' : 'bg-grey-1 text-grey-7'"
      row-key="id"
      flat bordered
      :rows-per-page-options="[0]"
    >
      <template v-slot:body-cell-policy="props">
        <q-td :props="props">
          <span v-if="props.row.rtype === 'count'">
            <strong>Last:</strong> {{ props.row.keep_last }} snapshots
          </span>
          <span v-else>
            <strong>Hourly:</strong> {{ props.row.keep_hourly || 'Forever' }}
            <strong>Weekly:</strong> {{ props.row.keep_weekly || 'Forever' }}
            <strong>Monthly:</strong> {{ props.row.keep_monthly || 'Forever' }}
            <strong>Yearly:</strong> {{ props.row.keep_yearly || 'Forever' }}
          </span>
        </q-td>
      </template>
      <template v-slot:body-cell-actions="props">
        <q-td :props="props">
          <TableActionButton icon="edit" label="Edit retention policy" @click="showManageDialog(props.row)" />
          <TableActionButton icon="delete" label="Delete retention policy" color="negative" @click="confirmDelete(props.row)" />
        </q-td>
      </template>
    </q-table>

    <RetentionManageDialog
      v-model="manageDialogVisible"
      :retention="managedRetention"
      :submitting="managing"
      @submit="onManageSubmit"
    />
  </q-page>
</template>

<script setup>
import PageHeader from 'components/PageHeader.vue'
import EmptyState from 'components/EmptyState.vue'
import TableActionButton from 'components/TableActionButton.vue'
import { ref, onMounted } from 'vue'
import { useQuasar } from 'quasar'
import { useRetentionStore } from 'stores/retention'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import RetentionManageDialog from 'components/retentions/RetentionManageDialog.vue'

const $q = useQuasar()
const retentionStore = useRetentionStore()

const manageDialogVisible = ref(false)
const managedRetention = ref(null)
const managing = ref(false)

const columns = [
  { name: 'id', label: '#', field: 'id', align: 'left', sortable: true },
  { name: 'name', label: 'Name', field: 'name', align: 'left', sortable: true },
  { name: 'rtype', label: 'Type', field: row => row.rtype === 'count' ? 'Count' : 'Date', align: 'left' },
  { name: 'policy', label: 'Policy', field: 'id', align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

function showManageDialog(retention) {
  managedRetention.value = retention
  manageDialogVisible.value = true
}

async function onManageSubmit(payload) {
  managing.value = true
  try {
    if (managedRetention.value) {
      await retentionStore.updateRetention(managedRetention.value.id, payload)
    } else {
      await retentionStore.createRetention(payload)
    }
    manageDialogVisible.value = false
    $q.notify({ message: 'Retention saved', color: 'green', position: 'top' })
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  } finally {
    managing.value = false
  }
}

function confirmDelete(retention) {
  $q.dialog({
    title: 'Delete Retention Policy',
    message: `Remove retention policy "${retention.name}"? This will unassign it from all jobs.`,
    cancel: true,
  }).onOk(async () => {
    try {
      await retentionStore.deleteRetention(retention.id)
      $q.notify({ message: 'Retention deleted', color: 'green', position: 'top' })
    } catch (error) {
      if (shouldIgnoreApiError(error)) return
      $q.notify({ message: getApiErrorMessage(error), color: 'negative', position: 'top' })
    }
  })
}

onMounted(() => retentionStore.loadRetentions())

defineOptions({ name: 'RetentionsPage' })
</script>
