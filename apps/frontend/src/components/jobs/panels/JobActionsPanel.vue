<template>
  <div>
    <q-table
      :rows="modelActions"
      :columns="actionColumns"
      row-key="id"
      flat
      dense
      :rows-per-page-options="[0]"
      hide-bottom
      no-data-label="No actions configured"
    >
      <template v-slot:body-cell-details="props">
        <q-td :props="props">
          <q-badge v-if="props.row.data && props.row.data.command" color="blue" :label="props.row.data.command" class="q-mr-xs" />
          <q-badge v-if="props.row.data && props.row.data.container" color="grey" :label="props.row.data.container" class="q-mr-xs" />
          <q-badge v-if="props.row.data && props.row.data.action" :color="props.row.data.action === 'stop' ? 'red' : 'green'" :label="props.row.data.action" />
        </q-td>
      </template>
      <template v-slot:body-cell-actions="props">
        <q-td :props="props">
          <q-btn flat dense icon="edit" @click="showActionDialog(props.row)" />
          <q-btn flat dense icon="delete" color="red" @click="confirmDeleteAction(props.row)" />
        </q-td>
      </template>
    </q-table>

    <div v-if="modelActions.length === 0" class="text-grey q-pa-sm">No actions configured</div>
    <div class="q-mt-sm">
      <q-btn :disable="disabled" color="green" icon="add" label="Add action" size="sm" @click="showActionDialog(null)" />
    </div>

    <JobActionManageDialog
      v-model="actionDialogVisible"
      :action="editingAction"
      @submit="onActionSubmit"
    />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useQuasar } from 'quasar'
import JobActionManageDialog from 'components/jobs/JobActionManageDialog.vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const $q = useQuasar()

const actionDialogVisible = ref(false)
const editingAction = ref(null)

const modelActions = computed(() => props.modelValue)

const moduleOptions = [
  { label: 'Execute command on agent', value: 'command' },
  { label: 'Control docker container', value: 'docker' },
]

const actionColumns = [
  { name: 'hook', label: 'Hook', field: row => row.hook.charAt(0).toUpperCase() + row.hook.slice(1), align: 'left' },
  { name: 'module_text', label: 'Module', field: row => row.module_text || getModuleLabel(row.module), align: 'left' },
  { name: 'details', label: 'Details', field: 'data', align: 'left' },
  { name: 'actions', label: '', field: 'id', align: 'right' },
]

function getModuleLabel(module) {
  return moduleOptions.find(option => option.value === module)?.label || module
}

function cloneAction(action) {
  return {
    ...action,
    data: { ...(action.data || {}) },
  }
}

function showActionDialog(action) {
  editingAction.value = action
  actionDialogVisible.value = true
}

function onActionSubmit(data) {
  if (editingAction.value) {
    emit('update:modelValue', props.modelValue.map(action => action.id === editingAction.value.id ? { ...cloneAction(action), ...data } : cloneAction(action)))
  } else {
    emit('update:modelValue', [
      ...props.modelValue.map(cloneAction),
      { id: `draft-${Date.now()}`, ...data },
    ])
  }

  actionDialogVisible.value = false
}

function confirmDeleteAction(action) {
  $q.dialog({ title: 'Delete Action', message: 'Delete this action?', cancel: true }).onOk(() => {
    emit('update:modelValue', props.modelValue.filter(item => item.id !== action.id).map(cloneAction))
  })
}

defineOptions({ name: 'JobActionsPanel' })
</script>
