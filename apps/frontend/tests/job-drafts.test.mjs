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

test('Proxmox job drafts preserve exclusions when editing and saving', () => {
  const source = readFileSync(new URL('../src/components/jobs/JobManageDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const config = { selection_mode: 'all', guest_ids: [], exclude_guest_ids: [101], backup_mode: 'native_cbt', fleecing_storage: 'local-lvm' }
  const props = { editingJob: { name: 'VMs', type: 'proxmox', config, actions: [], schedules: [] } }
  let saved
  const dialog = runInNewContext(`${source}\n;({ resetForm, submitForm, jobForm })`, {
    computed, reactive, ref,
    defineProps: () => props,
    defineEmits: () => (event, value) => { assert.equal(event, 'save'); saved = value },
    defineModel: () => ref(true),
    defineOptions: () => {},
    watch: () => {},
    useQuasar: () => ({ notify: () => assert.fail('unexpected validation error') }),
    useAgentStore: () => ({ agents: [] }),
  })
  dialog.resetForm()
  dialog.jobForm.config.exclude_guest_ids.push(102)
  dialog.submitForm()
  assert.equal(JSON.stringify(saved.config.exclude_guest_ids), '[101,102]')
  assert.deepEqual(config.exclude_guest_ids, [101])
  assert.equal(saved.config.backup_mode, 'native_cbt')
  assert.equal(saved.config.fleecing_storage, 'local-lvm')
  dialog.jobForm.config.fleecing_storage = ''
  dialog.submitForm()
  assert.equal(saved.config.fleecing_storage, '')
  assert.equal(config.fleecing_storage, 'local-lvm')
})

test('job submission validates general fields even when another section is active', () => {
  const source = readFileSync(new URL('../src/components/jobs/JobManageDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const saved = []
  const notices = []
  const dialog = runInNewContext(`${source}\n;({ submitForm, jobForm, activeSection })`, {
    computed, reactive, ref,
    defineProps: () => ({ editingJob: null }),
    defineEmits: () => (event, value) => { assert.equal(event, 'save'); saved.push(value) },
    defineModel: () => ref(true),
    defineOptions: () => {},
    watch: () => {},
    useQuasar: () => ({ notify: notice => notices.push(notice) }),
    useAgentStore: () => ({ agents: [] }),
  })
  for (const [type, config] of [
    ['file', { paths: [{ path: '/data', group: 'folder' }] }],
    ['proxmox', { selection_mode: 'all' }],
    ['truenas', { datasets: ['tank'] }],
  ]) {
    Object.assign(dialog.jobForm, { type, config })
    for (const section of ['entries', 'actions', 'schedules']) {
      for (const name of ['', ' \t ']) {
        dialog.jobForm.name = name
        dialog.activeSection.value = section
        const saveCount = saved.length
        const noticeCount = notices.length
        dialog.submitForm()
        assert.equal(saved.length, saveCount)
        assert.equal(notices.length, noticeCount + 1)
        assert.match(notices.at(-1).message, /job name/i)
        assert.equal(dialog.activeSection.value, 'general')
      }
    }
    dialog.jobForm.name = 'Backup'
    dialog.activeSection.value = 'entries'
    const saveCount = saved.length
    const noticeCount = notices.length
    dialog.submitForm()
    assert.equal(saved.length, saveCount + 1)
    assert.equal(saved.at(-1).name, 'Backup')
    assert.equal(notices.length, noticeCount)
  }
  dialog.jobForm.type = ''
  dialog.activeSection.value = 'schedules'
  dialog.submitForm()
  assert.equal(saved.length, 3)
  assert.match(notices.at(-1).message, /job type/i)
  assert.equal(dialog.activeSection.value, 'general')
})

test('Proxmox guest lists transfer selections and retain missing IDs across modes and discovery failures', async () => {
  const source = readFileSync(new URL('../src/components/jobs/forms/ProxmoxBackupJobForm.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const original = { selection_mode: 'include', guest_ids: [101, 999] }
  const props = reactive({ modelValue: original, agentId: 1, agentOnline: true })
  let fail = false
  const panel = runInNewContext(`${source}\n;({ loadGuests, loadError, selectedIds, selectedGuests, availableGuests, updateConfig, updateSelectedIds })`, {
    computed, ref,
    defineProps: () => props,
    defineEmits: () => (event, value) => { assert.equal(event, 'update:modelValue'); props.modelValue = value },
    defineOptions: () => {},
    watch: () => {},
    useAgentStore: () => ({ agents: [] }),
    useJobStore: () => ({ getProxmoxGuests: async () => {
      if (fail) throw new Error('Discovery failed')
      return [{ vmid: 101, name: 'first' }, { vmid: 102, name: 'second' }]
    } }),
    shouldIgnoreApiError: () => false,
    getApiErrorMessage: error => error.message,
  })
  const ids = list => Array.from(list.value, guest => guest.vmid)
  await panel.loadGuests()
  assert.deepEqual(ids(panel.availableGuests), [102])
  assert.deepEqual(ids(panel.selectedGuests), [101, 999])
  assert.equal(panel.selectedGuests.value[0].name, 'first')
  panel.updateSelectedIds([101, 102, 999])
  assert.deepEqual(ids(panel.availableGuests), [])
  panel.updateSelectedIds([102, 999])
  assert.deepEqual(ids(panel.availableGuests), [101])

  panel.updateConfig({ selection_mode: 'all' })
  assert.deepEqual(ids(panel.selectedGuests), [])
  assert.deepEqual(ids(panel.availableGuests), [101, 102])
  panel.updateSelectedIds([101])
  assert.deepEqual(ids(panel.availableGuests), [102])
  panel.updateConfig({ selection_mode: 'include' })
  assert.deepEqual(ids(panel.selectedGuests), [102, 999])
  panel.updateConfig({ selection_mode: 'all' })
  assert.deepEqual(ids(panel.selectedGuests), [101])

  fail = true
  await panel.loadGuests()
  assert.equal(panel.loadError.value, 'Discovery failed')
  assert.deepEqual(ids(panel.selectedGuests), [101])
  props.agentOnline = false
  await panel.loadGuests()
  assert.deepEqual(ids(panel.selectedGuests), [101])
  panel.updateSelectedIds([])
  assert.deepEqual(ids(panel.selectedGuests), [])
  assert.deepEqual(original, { selection_mode: 'include', guest_ids: [101, 999] })
})
