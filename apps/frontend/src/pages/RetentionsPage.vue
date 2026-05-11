<template>
  <q-page class="q-pa-md">
    <div class="text-h5 q-mb-md">Retention Policies</div>
    <q-table
      :rows="retentionStore.retentions"
      :columns="columns"
      :loading="retentionStore.loading"
      row-key="id"
      flat bordered
      :rows-per-page-options="[0]"
      no-data-label="No retention policies configured"
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
          <q-btn flat dense icon="edit" @click="showManageDialog(props.row)" />
          <q-btn flat dense icon="delete" color="red" @click="confirmDelete(props.row)" />
        </q-td>
      </template>
    </q-table>

    <q-page-sticky position="bottom-right" :offset="[35, 35]">
      <q-btn fab icon="add" color="primary" @click="showManageDialog(null)" />
    </q-page-sticky>

    <RetentionManageDialog
      v-model="manageDialogVisible"
      :retention="managedRetention"
      :submitting="managing"
      @submit="onManageSubmit"
    />
  </q-page>
</template>

<script setup>
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
    await retentionStore.deleteRetention(retention.id)
    $q.notify({ message: 'Retention deleted', color: 'green', position: 'top' })
  })
}

onMounted(() => retentionStore.loadRetentions())

defineOptions({ name: 'RetentionsPage' })
</script>
