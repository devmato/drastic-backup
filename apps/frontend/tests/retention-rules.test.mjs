import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { buildRetentionRule, describeRetentionRule, ruleTypes } from '../src/utils/retention-rules.js'

test('all rule editor types emit only applicable API fields and have readable summaries', () => {
  const form = { count: 4, days: 7, weeks: 0, months: 1, date: '2026-01-01', weekday: 6, day: 1, month: 2 }
  for (const { value } of ruleTypes) {
    const rule = buildRetentionRule(value, form)
    assert.ok(describeRetentionRule(rule).length > 10)
    const keys = rule.type === 'recent' ? ['days', 'months', 'type', 'weeks']
      : rule.type === 'since' ? ['date', 'type']
        : ['count', 'type', ...(rule.period ? ['period'] : []),
          ...(rule.type === 'calendar' ? ({ week: ['weekday'], month: ['day'], year: ['month'] }[rule.period] || []) : [])]
    assert.deepEqual(Object.keys(rule).sort(), keys.sort())
  }
  assert.equal(ruleTypes.length, 12)
  assert.equal(describeRetentionRule(buildRetentionRule('calendar:week', form)), 'One backup each week on Sunday, for the last 4 weeks')
  assert.equal(describeRetentionRule(buildRetentionRule('recent', form)), 'All backups in the last 7 days and 1 month')
})

test('policy editor drafts add, edit and remove rules without modifying the saved policy', async () => {
  const source = readFileSync(new URL('../src/components/retentions/RetentionManageDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const original = { name: 'Daily', rules: [{ type: 'last', count: 2 }] }
  const props = reactive({ retention: original })
  const visible = ref(false), submissions = []
  const page = runInNewContext(`${source}\n;({ form, timezone, editRule, applyRule, submitForm })`, {
    computed, reactive, ref, watch, defineProps: () => props, defineModel: () => visible,
    defineEmits: () => (name, payload) => submissions.push(payload),
    useUserStore: () => ({ user: { schedule_timezone: null } }),
  })
  visible.value = true
  await nextTick()
  assert.equal(page.timezone.value, 'UTC (standard)')
  page.editRule(null)
  page.applyRule({ type: 'calendar', period: 'week', weekday: 6, count: 4 })
  page.editRule(0)
  page.applyRule({ type: 'last', count: 7 })
  assert.deepEqual(original.rules, [{ type: 'last', count: 2 }])
  page.submitForm()
  assert.equal(submissions[0].rules.length, 2)
  assert.equal(submissions[0].rules[0].count, 7)
  page.form.rules.splice(0, 2)
  page.submitForm()
  assert.equal(submissions.length, 1, 'empty policies must not be submitted')
  visible.value = false
  await nextTick()
  visible.value = true
  await nextTick()
  assert.equal(page.form.rules.length, 1, 'reopening discards unsaved changes')
  assert.equal(page.form.rules[0].count, 2)
})
