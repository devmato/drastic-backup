<template>
  <q-page class="q-pa-md">
    <div class="text-h5 q-mb-md">Notification Settings</div>
    <q-table
      :rows="notificationStore.configs"
      :columns="columns"
      :loading="notificationStore.loading"
      row-key="id"
      flat bordered
      :rows-per-page-options="[0]"
      no-data-label="No notifications configured"
    >
      <template v-slot:body-cell-operation_types="props">
        <q-td :props="props">
          <q-badge v-for="label in props.row.operation_type_labels" :key="label" class="q-mr-xs" :label="label" />
        </q-td>
      </template>
      <template v-slot:body-cell-operation_states="props">
        <q-td :props="props">
          <q-badge v-for="label in props.row.operation_state_labels" :key="label" class="q-mr-xs" outline :label="label" :color="stateColor(label.toLowerCase())" />
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

    <NotificationManageDialog
      v-model="manageDialogVisible"
      :config="managedConfig"
      :options="notificationStore.options"
      :submitting="managing"
      :testing="testing"
      @submit="onManageSubmit"
      @test="testUrl"
    />
  </q-page>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useQuasar } from 'quasar'
import { useNotificationStore } from 'stores/notification'
import { getApiErrorMessage, shouldIgnoreApiError } from 'src/utils/api-error'
import NotificationManageDialog from 'components/notifications/NotificationManageDialog.vue'

const $q = useQuasar()
const notificationStore = useNotificationStore()

const manageDialogVisible = ref(false)
const managedConfig = ref(null)
const managing = ref(false)
const testing = ref(false)

const columns = [
  { name: 'id', label: '#', field: 'id', align: 'left', sortable: true },
  { name: 'url', label: 'URL', field: 'url', align: 'left' },
  { name: 'operation_types', label: 'Operation Types', field: 'operation_type_labels', align: 'left' },
  { name: 'operation_states', label: 'Operation States', field: 'operation_state_labels', align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

function stateColor(state) {
  const colors = { running: 'blue', success: 'green', warning: 'orange', failed: 'red', cancelled: 'grey' }
  return colors[state] || 'grey'
}

function showManageDialog(config) {
  managedConfig.value = config
  manageDialogVisible.value = true
}

async function onManageSubmit(payload) {
  managing.value = true
  try {
    if (managedConfig.value) {
      await notificationStore.updateConfig(managedConfig.value.id, payload)
    } else {
      await notificationStore.createConfig(payload)
    }
    manageDialogVisible.value = false
    $q.notify({ message: 'Notification config saved', color: 'green', position: 'top' })
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e), color: 'red', position: 'top' })
  } finally {
    managing.value = false
  }
}

function confirmDelete(config) {
  $q.dialog({
    title: 'Delete Notification Config',
    message: 'Delete this notification config?',
    cancel: true,
  }).onOk(async () => {
    await notificationStore.deleteConfig(config.id)
    $q.notify({ message: 'Notification config deleted', color: 'green', position: 'top' })
  })
}

async function testUrl(url) {
  if (!url) return
  testing.value = true
  try {
    const result = await notificationStore.testNotification(url)
    $q.notify({ message: result.msg, color: result.success ? 'green' : 'red', position: 'top' })
  } catch (e) {
    if (shouldIgnoreApiError(e)) return
    $q.notify({ message: getApiErrorMessage(e, 'Test failed'), color: 'red', position: 'top' })
  } finally {
    testing.value = false
  }
}

onMounted(async () => {
  await notificationStore.loadOptions()
  await notificationStore.loadConfigs()
})

defineOptions({ name: 'NotificationsPage' })
</script>
