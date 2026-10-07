import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, reactive, ref } from 'vue'
import { describeTiming, scheduleTiming } from '../src/utils/schedule.js'

function script(component) {
  return readFileSync(new URL(`../src/components/${component}.vue`, import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
}

for (const timeOnly of [false, true]) {
  test(`shared schedule dialog preserves drafts and emits ${timeOnly ? 'chain times' : 'job backup settings'}`, () => {
    const original = { hour: '2', minute: '30', day_of_week: [1, 3], enabled: false,
      repository_id: 2, retention_id: 7, config: { repository_check: { enabled: true, read_data: '100%' } } }
    const before = JSON.stringify(original)
    let saved
    const dialog = runInNewContext(`${script('ScheduleManageDialog')}
      ;({ resetForm, submitForm, form })`, {
      computed, reactive, ref, scheduleTiming, describeTiming, watch: () => {}, onBeforeUnmount: () => {}, defineOptions: () => {},
      defineProps: () => ({ schedule: original, timeOnly, timezone: timeOnly ? 'UTC' : '',
        repositories: [{ id: 2, name: 'NAS' }], allRepositories: [], retentions: [] }),
      defineModel: () => ref(true),
      defineEmits: () => (event, value) => { assert.equal(event, 'submit'); saved = value },
      useQuasar: () => ({ notify: notice => assert.fail(notice.message) }),
      getApiErrorMessage: error => error.message,
    })
    dialog.resetForm()
    dialog.form.hour = '5'
    dialog.form.weekdays.push(5)
    assert.equal(JSON.stringify(original), before, 'editing/cancelling must not mutate saved settings')
    dialog.submitForm()
    assert.equal(saved.timing.hour, 5)
    assert.equal(saved.enabled, false)
    assert.equal(JSON.stringify(saved.timing.weekdays), '[1,3,5]')
    if (timeOnly) {
      assert.deepEqual(Object.keys(saved).sort(), ['cron_description', 'enabled', 'timing'])
    } else {
      assert.equal(saved.repository_id, 2)
      assert.equal(saved.retention_id, 7)
      assert.equal(saved.config.repository_check.read_data, '100%')
    }
    assert.equal(JSON.stringify(original), before)
  })
}

test('shared schedule list updates its preview immediately and omits job columns for chains', () => {
  const original = { id: 1, hour: '2', minute: '0', day_of_week: [1], enabled: true,
    cron_string: '0 2 * * 1', cron_description: 'Old description' }
  const props = { modelValue: [original], timeOnly: true, timezone: 'UTC' }
  const panel = runInNewContext(`${script('SchedulesPanel')}
    ;({ showScheduleDialog, onScheduleSubmit, scheduleColumns, getCronDescription })`, {
    computed, ref, scheduleTiming, describeTiming, onMounted: () => {}, defineOptions: () => {},
    defineProps: () => props,
    defineEmits: () => (event, value) => { assert.equal(event, 'update:modelValue'); props.modelValue = value },
    useRetentionStore: () => ({ retentions: [] }), useQuasar: () => ({}),
  })
  assert.deepEqual(Array.from(panel.scheduleColumns.value, column => column.name),
    ['enabled', 'cron_description', 'actions'])
  panel.showScheduleDialog(original)
  panel.onScheduleSubmit({ timing: { type: 'weekly', hour: 4, minute: 15, weekdays: [0, 6] }, cron_description: 'Sun, Sat at 04:15', enabled: false })
  assert.equal(props.modelValue[0].timing.hour, 4)
  assert.equal(props.modelValue[0].cron_description, 'Sun, Sat at 04:15')
  assert.equal(original.cron_string, '0 2 * * 1')
})

test('chain edits clone multiple schedules and save only their time settings', async () => {
  const original = { id: 3, name: 'Nightly', start_timeout_minutes: 60,
    schedules: [{ enabled: true, cron_string: '0 2 * * 1,2,3,4,5' },
      { enabled: false, cron_string: '0 4 * * 0,6' }],
    steps: [{ job_id: 1, repository_id: 2, retention_id: 7, config: {} }] }
  const before = JSON.stringify(original)
  let saved
  const panel = runInNewContext(`${script('jobs/BackupChainsPanel')}
    ;({ editChain, save, form })`, {
    computed, reactive, ref, scheduleTiming, watch: () => {}, onMounted: () => {}, onBeforeUnmount: () => {},
    defineProps: () => ({ agentJobs: [], repositories: [], agents: [] }), defineEmits: () => () => {},
    useRoute: () => ({ query: {} }), useRouter: () => ({}), useQuasar: () => ({ notify: () => {} }),
    useChainStore: () => ({ saveChain: async (id, payload) => { assert.equal(id, 3); saved = payload } }),
    useRetentionStore: () => ({ retentions: [] }),
    useUserStore: () => ({ withRecoveryKey: operation => operation('test-key') }),
    getApiErrorMessage: error => error.message, shouldIgnoreApiError: () => false,
  })
  panel.editChain(original)
  panel.form.schedules[0].timing.hour = 6
  panel.form.schedules[0].timing.weekdays.push(6)
  assert.equal(JSON.stringify(original), before)
  await panel.save()
  assert.equal(saved.schedules.length, 2)
  assert.equal(saved.schedules[0].timing.hour, 6)
  assert.equal(saved.schedules[1].enabled, false)
  assert.deepEqual(Object.keys(saved.schedules[0]).sort(), ['enabled', 'timing'])
  assert.equal(saved.steps[0].repository_id, 2)
  assert.equal(saved.steps[0].retention_id, 7)
  assert.equal(JSON.stringify(original), before)
})
