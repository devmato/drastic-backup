<template>
  <q-dialog v-model="dialogVisible" persistent>
    <q-card class="db-dialog-card-md">
      <q-card-section><div class="text-h6">{{ retention ? 'Edit Retention Policy' : 'Add Retention Policy' }}</div></q-card-section>
      <q-form @submit="submitForm">
        <q-card-section class="q-gutter-md">
          <q-input v-model="form.name" outlined :dense="!$q.platform.has.touch" hide-bottom-space label="Name" maxlength="255"
            :disable="submitting" :rules="[val => !!val?.trim() || 'Required']" />
          <div class="row items-center justify-between q-gutter-sm">
            <div class="text-subtitle1">Retention rules</div>
            <q-btn flat no-caps color="primary" icon="add" label="Add rule" :disable="submitting || form.rules.length >= 100" @click="editRule(null)" />
          </div>
          <q-list v-if="form.rules.length" bordered separator>
            <q-item v-for="(rule, index) in form.rules" :key="index">
              <q-item-section>{{ describeRetentionRule(rule) }}</q-item-section>
              <q-item-section side>
                <div class="row no-wrap">
                  <TableActionButton icon="edit" label="Edit rule" :disable="submitting" @click="editRule(index)" />
                  <TableActionButton icon="delete" label="Remove rule" color="negative" :disable="submitting" @click="form.rules.splice(index, 1)" />
                </div>
              </q-item-section>
            </q-item>
          </q-list>
          <div v-else class="text-caption">Add at least one rule.</div>
          <div class="text-caption">A backup is kept if any rule matches. The last confirmed complete backup is always kept.</div>
          <div class="text-caption">Calendar time zone: {{ timezone }}</div>
          <div v-if="error" role="alert" class="text-negative">{{ error }}</div>
        </q-card-section>
        <q-card-actions align="right" class="q-pa-md">
          <q-btn flat no-caps label="Cancel" :disable="submitting" v-close-popup />
          <q-btn unelevated no-caps label="Save" type="submit" color="primary" :loading="submitting" :disable="submitting || !form.rules.length" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
  <RetentionRuleDialog v-model="ruleDialogVisible" :rule="editedIndex === null ? null : form.rules[editedIndex]" :timezone="timezone" @submit="applyRule" />
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import TableActionButton from 'components/TableActionButton.vue'
import RetentionRuleDialog from 'components/retentions/RetentionRuleDialog.vue'
import { useUserStore } from 'stores/user'
import { describeRetentionRule } from 'src/utils/retention-rules'

const props = defineProps({ retention: { type: Object, default: null }, submitting: { type: Boolean, default: false } })
const emit = defineEmits(['submit'])
const dialogVisible = defineModel({ type: Boolean, required: true })
const userStore = useUserStore()
const timezone = computed(() => userStore.user?.schedule_timezone || 'UTC (standard)')
const form = reactive({ name: '', rules: [] })
const ruleDialogVisible = ref(false)
const editedIndex = ref(null)
const error = ref('')

watch(dialogVisible, open => {
  if (!open) return
  form.name = props.retention?.name || ''
  form.rules = (props.retention?.rules || []).map(rule => ({ ...rule }))
  error.value = ''
})

function editRule(index) {
  editedIndex.value = index
  ruleDialogVisible.value = true
}

function applyRule(rule) {
  if (editedIndex.value === null) form.rules.push(rule)
  else form.rules[editedIndex.value] = rule
  error.value = ''
}

function submitForm() {
  if (!form.rules.length) {
    error.value = 'Add at least one retention rule.'
    return
  }
  emit('submit', { name: form.name.trim(), rules: form.rules.map(rule => ({ ...rule })) })
}
</script>
