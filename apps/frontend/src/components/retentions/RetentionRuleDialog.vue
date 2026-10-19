<template>
  <q-dialog v-model="visible" persistent>
    <q-card class="db-dialog-card-md">
      <q-card-section><div class="text-h6">{{ rule ? 'Edit retention rule' : 'Add retention rule' }}</div></q-card-section>
      <q-form @submit="save">
        <q-card-section class="q-gutter-md">
          <q-select v-model="selection" :options="ruleTypes" emit-value map-options outlined :dense="!$q.platform.has.touch" label="Type" />
          <q-input v-if="kind === 'last' || kind === 'calendar'" v-model.number="form.count" type="number" min="1" max="2147483647"
            outlined :dense="!$q.platform.has.touch" hide-bottom-space :label="kind === 'last' ? 'Backups' : `Number of ${period}s`"
            :rules="[value => validInteger(value, 1, 2147483647) || 'Use a positive whole number']" />
          <template v-if="kind === 'recent'">
            <q-input v-for="[field, max] in [['days', 365000], ['weeks', 52000], ['months', 12000]]" :key="field"
              v-model.number="form[field]" type="number" min="0" :max="max" outlined :dense="!$q.platform.has.touch" hide-bottom-space
              :label="field[0].toUpperCase() + field.slice(1)" :rules="[value => validInteger(value, 0, max) || `Use 0–${max}`]" />
          </template>
          <q-input v-if="kind === 'since'" v-model="form.date" type="date" outlined :dense="!$q.platform.has.touch"
            label="Date" hide-bottom-space :rules="[value => !!value || 'Required']" />
          <q-select v-if="kind === 'calendar' && period === 'week'" v-model="form.weekday" :options="weekdayOptions" emit-value map-options
            outlined :dense="!$q.platform.has.touch" label="Day of week" />
          <q-input v-if="kind === 'calendar' && period === 'month'" v-model.number="form.day" type="number" min="1" max="31"
            outlined :dense="!$q.platform.has.touch" hide-bottom-space label="Day of month" :rules="[value => validInteger(value, 1, 31) || 'Use 1–31']" />
          <q-select v-if="kind === 'calendar' && period === 'year'" v-model="form.month" :options="monthOptions" emit-value map-options
            outlined :dense="!$q.platform.has.touch" label="Month" />
          <div class="text-caption">Calendar time zone: {{ timezone }}</div>
          <div v-if="kind === 'calendar'" class="text-caption">
            Keeps the first matching backup in each calendar period, including the current period. Missing days are not replaced by another day.
          </div>
          <div v-else-if="kind === 'last' && period" class="text-caption">Keeps the latest backup in each {{ period }} until the selected count is reached, even across gaps.</div>
          <div v-else-if="kind === 'recent'" class="text-caption">The combined period is measured back from the retention run, using calendar months and local days.</div>
          <div v-else-if="kind === 'since'" class="text-caption">Keeps backups after the start of this date in the account time zone.</div>
          <div v-if="error" role="alert" class="text-negative">{{ error }}</div>
        </q-card-section>
        <q-card-actions align="right" class="q-pa-md">
          <q-btn flat no-caps label="Cancel" v-close-popup />
          <q-btn unelevated no-caps color="primary" label="Apply" type="submit" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { buildRetentionRule, months, ruleTypes, weekdays } from 'src/utils/retention-rules'

const props = defineProps({ rule: { type: Object, default: null }, timezone: { type: String, required: true } })
const emit = defineEmits(['submit'])
const visible = defineModel({ type: Boolean, required: true })
const selection = ref('last')
const kind = computed(() => selection.value.split(':')[0])
const period = computed(() => selection.value.split(':')[1])
const form = reactive({})
const error = ref('')
const weekdayOptions = weekdays.map((label, value) => ({ label, value }))
const monthOptions = months.map((label, index) => ({ label, value: index + 1 }))
const validInteger = (value, min, max) => Number.isInteger(value) && value >= min && value <= max

watch(visible, open => {
  if (!open) return
  const rule = props.rule
  selection.value = rule ? `${rule.type}${rule.period ? `:${rule.period}` : ''}` : 'last'
  Object.assign(form, { count: 1, days: 0, weeks: 0, months: 0, date: '', weekday: 6, day: 1, month: 1 }, rule)
  error.value = ''
})

function save() {
  if (kind.value === 'recent' && ![form.days, form.weeks, form.months].some(value => value > 0)) {
    error.value = 'Specify at least one positive period.'
    return
  }
  emit('submit', buildRetentionRule(selection.value, form))
  visible.value = false
}
</script>
