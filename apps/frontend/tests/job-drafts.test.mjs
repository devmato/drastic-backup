import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, reactive, ref } from 'vue'

for (const [kind, item, changes] of [
  ['Action', { module: 'command', hook: 'start', data: { command: 'original' } }, { data: { command: 'edited' } }],
  ['Schedule', { hour: '1', minute: '0', day_of_week: [1], repository_id: 1 }, { hour: '2' }],
]) {
  test(`${kind} drafts add, edit and delete through the parent model`, () => {
    const original = { id: 1, ...item }
    const props = { modelValue: [original] }
    const initial = JSON.stringify(props.modelValue)
    let confirm
    let updates = 0
    const source = readFileSync(new URL(`../src/components/jobs/panels/Job${kind}sPanel.vue`, import.meta.url), 'utf8')
      .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
    const panel = runInNewContext(`${source}\n;({ show: show${kind}Dialog, submit: on${kind}Submit, remove: confirmDelete${kind} })`, {
      computed, ref,
      defineProps: () => props,
      defineEmits: () => (event, value) => {
        assert.equal(event, 'update:modelValue')
        props.modelValue = value
        updates++
      },
      defineOptions: () => {},
      onMounted: () => {},
      useQuasar: () => ({ dialog: () => ({ onOk: callback => { confirm = callback } }) }),
      useRetentionStore: () => ({ retentions: [] }),
    })

    panel.show(null)
    panel.submit(item)
    assert.equal(props.modelValue.length, 2)
    const draftId = props.modelValue[1].id
    assert.match(draftId, /^draft-/)

    panel.show(props.modelValue[1])
    panel.submit({ ...item, ...changes })
    assert.equal(props.modelValue.length, 2)
    assert.equal(props.modelValue[1].id, draftId)
    for (const [key, value] of Object.entries(changes)) {
      assert.equal(JSON.stringify(props.modelValue[1][key]), JSON.stringify(value))
    }
    assert.equal(JSON.stringify([original]), initial)

    panel.remove(props.modelValue[1])
    assert.equal(props.modelValue.length, 2, 'deletion waits for confirmation')
    confirm()
    assert.equal(props.modelValue.length, 1)
    assert.equal(props.modelValue[0].id, original.id)
    assert.equal(updates, 3)
  })
}

test('TrueNAS job drafts default new jobs to children and preserve existing scope on save', () => {
  const source = readFileSync(new URL('../src/components/jobs/JobManageDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const props = { editingJob: null }
  let saved
  const dialog = runInNewContext(`${source}\n;({ cloneConfig, resetForm, submitForm, jobForm })`, {
    computed, reactive, ref,
    defineProps: () => props,
    defineEmits: () => (event, value) => { assert.equal(event, 'save'); saved = value },
    defineModel: () => ref(true),
    defineOptions: () => {},
    watch: () => {},
    useQuasar: () => ({ notify: () => assert.fail('unexpected validation error') }),
    useAgentStore: () => ({ agents: [] }),
  })
  assert.equal(dialog.cloneConfig('truenas').include_children, true)
  for (const configured of [undefined, false, true]) {
    const config = { datasets: ['tank'], exclude_patterns: ['cache/**'] }
    if (configured !== undefined) config.include_children = configured
    props.editingJob = { name: 'NAS', type: 'truenas', config, actions: [], schedules: [] }
    dialog.resetForm()
    assert.equal(dialog.jobForm.config.include_children, configured ?? false)
    dialog.submitForm()
    assert.equal(saved.config.include_children, configured ?? false)
    assert.notEqual(saved.config.datasets, config.datasets)
    dialog.jobForm.config.include_children = !(configured ?? false)
    dialog.submitForm()
    assert.equal(saved.config.include_children, !(configured ?? false))
    assert.equal(config.include_children, configured)
  }
})
