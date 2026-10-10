import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, reactive, ref } from 'vue'
import { describeTiming, scheduleTiming } from '../src/utils/schedule.js'
import { hasTrueNASSelection, browserKey, browserPath, browserSelection, selectionConfig, isPathWithin } from '../src/utils/truenas-selection.js'
import { pathSelectionOptions } from '../src/utils/path-selection.js'

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
    const panelPath = kind === 'Schedule' ? '../src/components/SchedulesPanel.vue' : '../src/components/jobs/panels/JobActionsPanel.vue'
    const source = readFileSync(new URL(panelPath, import.meta.url), 'utf8')
      .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
    const panel = runInNewContext(`${source}\n;({ show: show${kind}Dialog, submit: on${kind}Submit, remove: confirmDelete${kind} })`, {
      computed, ref, scheduleTiming, describeTiming,
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

test('TrueNAS job drafts preserve path rules and patterns on save', () => {
  const source = readFileSync(new URL('../src/components/jobs/JobManageDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const props = { editingJob: null }
  let saved
  const dialog = runInNewContext(`${source}\n;({ cloneConfig, resetForm, submitForm, jobForm })`, {
    computed, reactive, ref, scheduleTiming, hasTrueNASSelection,
    defineProps: () => props,
    defineEmits: () => (event, value) => { assert.equal(event, 'save'); saved = value },
    defineModel: () => ref(true),
    defineOptions: () => {},
    watch: () => {},
    useQuasar: () => ({ notify: () => assert.fail('unexpected validation error') }),
    useAgentStore: () => ({ agents: [] }),
  })
  const config = {
    paths: [{ dataset: 'tank', path: '.', group: 'dataset' }],
    exclude_paths: [{ dataset: 'tank', path: 'cache', group: 'folder' }], exclude_patterns: ['cache/**'],
  }
  props.editingJob = { name: 'NAS', type: 'truenas', config, actions: [], schedules: [] }
  dialog.resetForm()
  dialog.submitForm()
  assert.deepEqual(JSON.parse(JSON.stringify(saved.config)), config)
  dialog.jobForm.config.paths[0].dataset = 'other'
  assert.equal(config.paths[0].dataset, 'tank')
  assert.equal(saved.config.paths[0].dataset, 'tank')
})

test('Proxmox job drafts preserve exclusions when editing and saving', () => {
  const source = readFileSync(new URL('../src/components/jobs/JobManageDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const config = { selection_mode: 'all', guest_ids: [], exclude_guest_ids: [101], backup_mode: 'native_cbt', fleecing_storage: 'local-lvm' }
  const props = { editingJob: { name: 'VMs', type: 'proxmox', config, actions: [], schedules: [] } }
  let saved
  const dialog = runInNewContext(`${source}\n;({ resetForm, submitForm, jobForm })`, {
    computed, reactive, ref, scheduleTiming, hasTrueNASSelection,
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
    computed, reactive, ref, scheduleTiming, hasTrueNASSelection,
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
    ['truenas', { paths: [{ dataset: 'tank', path: '.', group: 'dataset' }] }],
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

test('Proxmox host and VM browser preserves scope and supports inherited selection and exclusions', async () => {
  const script = name => readFileSync(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const original = { selection_mode: 'include', guest_ids: [101, 999], exclude_guest_ids: [101], backup_mode: 'native_cbt', fleecing_storage: 'local-thin' }
  const props = reactive({ modelValue: original, agentId: 1, agentOnline: true })
  const agent = reactive({ id: 1, hostname: 'pve.example', protocol_version: 14 })
  const known = [{ vmid: 101, name: 'first', node: 'pve' }, { vmid: 102, name: 'second', node: 'pve' }]
  let fail = false
  const panel = runInNewContext(`${script('jobs/forms/ProxmoxBackupJobForm')}\n;({ loadEntries, selection, updateConfig, updateSelection, refresh, backupModeOptions })`, {
    computed, ref,
    defineProps: () => props,
    defineEmits: () => (event, value) => { assert.equal(event, 'update:modelValue'); props.modelValue = value },
    defineOptions: () => {},
    watch: () => {},
    useAgentStore: () => ({ agents: [agent] }),
    useJobStore: () => ({ getProxmoxGuests: async () => {
      if (fail) throw new Error('Discovery failed')
      return known
    } }),
  })
  const browserProps = { mode: 'include-exclude', initialPath: '/', loadEntries: panel.loadEntries,
    get selection() { return panel.selection.value } }
  const context = {
    computed, ref, pathSelectionOptions, defineProps: () => browserProps, watch: () => {}, defineOptions: () => {},
    useQuasar: () => ({ notify: () => assert.fail('Unexpected browser error') }),
    shouldIgnoreApiError: () => false, getApiErrorMessage: error => error.message,
    defineEmits: () => (event, value) => { if (event === 'update:selection') panel.updateSelection(value) },
  }
  const browser = runInNewContext(`${script('PathBrowser')}\n;({ setInclude, setExclude, navigate, parentPath, entryRows })`, { ...context })
  const selected = runInNewContext(`${script('PathSelectionPanel')}\n;({ removePath, selectionRows })`, { ...context })
  const root = await panel.loadEntries('/')
  const host = root.entries[0]
  assert.equal(host.name, 'pve')
  assert.equal(host.icon, 'dns')
  await browser.navigate(host.path)
  assert.equal(browser.parentPath.value, '/')
  const vms = (await panel.loadEntries(host.path)).entries
  assert.deepEqual(Array.from(vms, vm => vm.vmid), [101, 102])
  assert.equal(browser.entryRows.value.find(vm => vm.vmid === 101).state, 'include', 'inactive exclusions do not change existing include jobs')
  assert.deepEqual(Array.from(selected.selectionRows.value, row => row.label), ['first (VM 101)', 'VM 999'])
  browser.setInclude(vms[1])
  assert.deepEqual(Array.from(props.modelValue.guest_ids), [101, 999, 102])
  browser.setExclude(vms[0])
  assert.deepEqual(Array.from(props.modelValue.guest_ids), [999, 102])
  selected.removePath('/host/999')
  assert.deepEqual(Array.from(props.modelValue.guest_ids), [102])
  browser.setInclude(host)
  assert.equal(props.modelValue.selection_mode, 'all')
  assert.deepEqual(Array.from(selected.selectionRows.value, row => row.icon), ['dns', 'computer'])
  browser.setExclude(vms[0])
  assert.equal(browser.entryRows.value.find(vm => vm.vmid === 101).state, 'exclude')
  assert.equal(browser.entryRows.value.find(vm => vm.vmid === 102).state, 'include')
  assert.deepEqual(Array.from(selected.selectionRows.value, row => [row.icon, row.state]), [['dns', 'include'], ['computer', 'include'], ['computer', 'exclude']])
  browser.setInclude(vms[0])
  assert.deepEqual(Array.from(props.modelValue.exclude_guest_ids), [])
  known.push({ vmid: 103, name: 'new', node: 'pve' })
  panel.refresh()
  await browser.navigate('/host')
  const newVM = browser.entryRows.value.find(vm => vm.vmid === 103)
  assert.equal(newVM.state, 'include', 'new VMs inherit the host selection')
  browser.setExclude(host)
  assert.equal(props.modelValue.selection_mode, 'include')
  assert.deepEqual(Array.from(props.modelValue.guest_ids), [102, 101], 'removing the host keeps explicit VM includes')
  browser.setInclude(newVM)
  assert.deepEqual(Array.from(props.modelValue.guest_ids), [102, 101, 103])
  assert.equal(props.modelValue.backup_mode, 'native_cbt')
  assert.equal(props.modelValue.fleecing_storage, 'local-thin')
  const allConfig = { ...original, selection_mode: 'all', exclude_guest_ids: [102, 999] }
  props.modelValue = allConfig
  panel.updateConfig({ backup_mode: 'snapshot' })
  assert.deepEqual(Array.from(props.modelValue.exclude_guest_ids), [102, 999])
  assert.deepEqual(Array.from(panel.selection.value.exclude_patterns, entry => entry.vmid), [102, 999])
  fail = true
  panel.refresh()
  await assert.rejects(panel.loadEntries('/'), /Discovery failed/)
  props.agentOnline = false
  assert.deepEqual(Array.from(panel.selection.value.exclude_patterns, entry => entry.vmid), [102, 999])
  selected.removePath('/host/999')
  assert.deepEqual(Array.from(props.modelValue.exclude_guest_ids), [102])
  agent.protocol_version = 7
  assert.equal(panel.backupModeOptions.value[1].disable, true)
  assert.deepEqual(original, { selection_mode: 'include', guest_ids: [101, 999], exclude_guest_ids: [101], backup_mode: 'native_cbt', fleecing_storage: 'local-thin' })
})

test('TrueNAS browses datasets, folders and files and retains selection after discovery errors', async () => {
  const source = readFileSync(new URL('../src/components/jobs/forms/TrueNASBackupJobForm.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const original = { paths: [{ dataset: 'tank', path: '.', group: 'dataset' }], exclude_paths: [], exclude_patterns: ['cache/**'] }
  const props = reactive({ modelValue: original, agentId: 1, agentOnline: true })
  let fail = false
  const known = ['tank', 'tank/data', 'tank/data/child', 'tank/locked'].map(id => ({
    id, path: `/host/mnt/${id}`, mountpoint: `/mnt/${id}`, available: !['tank', 'tank/locked'].includes(id),
  }))
  const requested = []
  const agent = { id: 1, protocol_version: 16 }
  const panel = runInNewContext(`${source}\n;({ loadEntries, selection, entryOptions, update, refresh })`, {
    computed, ref, browserKey, browserPath, browserSelection, selectionConfig, isPathWithin, pathSelectionOptions,
    defineProps: () => props,
    defineEmits: () => (event, value) => { assert.equal(event, 'update:modelValue'); props.modelValue = value },
    watch: () => {},
    useAgentStore: () => ({ agents: [agent], getTrueNASDatasets: async () => {
      if (fail) throw new Error('Discovery failed')
      return known
    } }),
    useJobStore: () => ({ getDirlist: async (_, path) => {
      requested.push(path)
      return { directories: [{ name: 'docs', file: false }, { name: 'readme.txt', file: true }, { name: '.zfs', file: false }, { name: 'child', file: false }] }
    } }),
  })
  const root = await panel.loadEntries('/')
  assert.equal(root.entries[0].icon, 'storage')
  const pool = await panel.loadEntries(root.entries[0].path)
  assert.deepEqual(Array.from(pool.entries, entry => entry.selectionEntry.dataset), ['tank/data', 'tank/locked'])
  assert.equal(requested.length, 0, 'unmounted pool still exposes child datasets')
  const contents = await panel.loadEntries(pool.entries[0].path)
  assert.deepEqual(Array.from(contents.entries, entry => [entry.name, entry.icon]), [
    ['docs', 'folder'], ['readme.txt', 'description'], ['child', 'storage'],
  ])
  const docs = contents.entries[0]
  assert.equal(panel.entryOptions(docs).state, 'include')
  const excluded = { dataset: 'tank/data', path: 'docs', group: 'folder' }
  panel.update({ exclude_paths: [excluded] })
  assert.equal(panel.entryOptions(docs).state, 'exclude')
  assert.equal(panel.entryOptions(docs).includeDisabled, false)
  const child = (await panel.loadEntries(docs.path)).entries[0]
  assert.equal(requested.at(-1), '/host/mnt/tank/data/docs')
  assert.equal(panel.entryOptions(child).state, 'exclude')
  assert.equal(panel.entryOptions(child).includeDisabled, false)
  agent.protocol_version = 15
  assert.equal(panel.entryOptions(child).includeDisabled, true)
  agent.protocol_version = 16
  panel.update({ paths: [...props.modelValue.paths, { dataset: 'tank/data', path: 'docs/docs', group: 'folder' }] })
  assert.equal(panel.entryOptions(child).state, 'include')
  assert.equal(panel.entryOptions(child).note, 'Explicitly included')
  assert.equal(panel.entryOptions(contents.entries[1]).state, 'include', 'unrelated files retain their inherited selection')
  const selected = JSON.stringify(panel.selection.value)
  fail = true
  panel.refresh()
  await assert.rejects(panel.loadEntries('/'), /Discovery failed/)
  assert.equal(JSON.stringify(panel.selection.value), selected)
  props.agentOnline = false
  assert.equal(JSON.stringify(panel.selection.value), selected)
  assert.deepEqual(Array.from(props.modelValue.exclude_patterns), ['cache/**'])
  assert.deepEqual(original.exclude_paths, [])
})

test('TrueNAS keeps a custom-mounted dataset distinct from a same-named folder', async () => {
  const script = name => readFileSync(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const known = [
    { id: 'tank', path: '/host/mnt/tank', mountpoint: '/mnt/tank', available: true },
    { id: 'tank/photos', path: '/host/mnt/archive', mountpoint: '/mnt/archive', available: true },
    { id: 'tank/normal', path: '/host/mnt/tank/normal', mountpoint: '/mnt/tank/normal', available: true },
  ]
  const props = reactive({ modelValue: { paths: [], exclude_paths: [] }, agentId: 1, agentOnline: true })
  const requested = []
  const panel = runInNewContext(`${script('jobs/forms/TrueNASBackupJobForm')}\n;({ loadEntries, selection, entryOptions, update })`, {
    computed, ref, browserKey, browserPath, browserSelection, selectionConfig, isPathWithin, pathSelectionOptions,
    defineProps: () => props,
    defineEmits: () => (_, value) => { props.modelValue = value },
    watch: () => {},
    useAgentStore: () => ({ agents: [], getTrueNASDatasets: async () => known }),
    useJobStore: () => ({ getDirlist: async (_, path) => {
      requested.push(path)
      return { directories: path === '/host/mnt/tank' ? [{ name: 'photos', file: false }, { name: 'normal', file: false }] : [] }
    } }),
  })
  const browserProps = { mode: 'include-exclude', initialPath: '/', loadEntries: panel.loadEntries,
    get selection() { return panel.selection.value } }
  const context = {
    computed, ref, pathSelectionOptions, defineProps: () => browserProps, watch: () => {}, defineOptions: () => {},
    useQuasar: () => ({ notify: () => assert.fail('Unexpected browser error') }),
    shouldIgnoreApiError: () => false, getApiErrorMessage: error => error.message,
    defineEmits: () => (event, value) => { if (event === 'update:selection') panel.update(selectionConfig(value)) },
  }
  const browser = runInNewContext(`${script('PathBrowser')}\n;({ navigate, setInclude, setExclude, parentPath, currentPathLabel })`, { ...context })
  const selected = runInNewContext(`${script('PathSelectionPanel')}\n;({ removePath, selectionRows })`, { ...context })
  const pool = browserKey({ dataset: 'tank', path: '.' })
  const listing = await panel.loadEntries(pool)
  const folder = listing.entries.find(entry => entry.name === 'photos' && entry.group === 'folder')
  const dataset = listing.entries.find(entry => entry.name === 'photos' && entry.group === 'dataset')
  assert.ok(folder && dataset, 'both sources must be visible')
  assert.notEqual(folder.path, dataset.path)
  assert.equal(listing.entries.filter(entry => entry.name === 'normal').length, 1, 'same source appears only once')

  for (const [entry, local] of [[folder, '/host/mnt/tank/photos'], [dataset, '/host/mnt/archive']]) {
    await browser.navigate(entry.path)
    assert.equal(requested.at(-1), local)
    assert.equal(browser.currentPathLabel.value, '/tank/photos', 'display labels stay readable')
    assert.equal(browser.parentPath.value, pool)
    await browser.navigate(browser.parentPath.value)
    assert.equal(requested.at(-1), '/host/mnt/tank')
    browser.setInclude(entry)
  }
  assert.deepEqual(JSON.parse(JSON.stringify(props.modelValue.paths)), [
    { dataset: 'tank', path: 'photos', group: 'folder' },
    { dataset: 'tank/photos', path: '.', group: 'dataset' },
  ])
  assert.deepEqual(Array.from(selected.selectionRows.value, row => row.icon), ['folder', 'storage'])
  browser.setExclude(folder)
  assert.equal(props.modelValue.paths[0].dataset, 'tank/photos')
  assert.equal(props.modelValue.paths.length, 1)
  assert.equal(panel.entryOptions(dataset).state, 'include')
  assert.equal(panel.entryOptions(folder).state, 'exclude')
  assert.equal(panel.entryOptions(folder).includeDisabled, false)
  selected.removePath(dataset.path)
  assert.equal(props.modelValue.paths.length, 0)
  assert.equal(props.modelValue.exclude_paths.length, 1, 'removing the dataset leaves the folder rule intact')
  browser.setInclude(folder)
  assert.equal(props.modelValue.exclude_paths.length, 0)
  assert.equal(props.modelValue.paths[0].dataset, 'tank')
})

test('TrueNAS selection round-trips all entry types and respects path boundaries', () => {
  const config = {
    paths: [{ dataset: 'tank', path: '.', group: 'dataset' }, { dataset: 'tank/data', path: 'docs/readme', group: 'file' }],
    exclude_paths: [{ dataset: 'tank/data', path: 'cache', group: 'folder' }],
  }
  assert.deepEqual(selectionConfig(browserSelection(config)), config)
  assert.equal(isPathWithin({ dataset: 'tank/data/new', path: '.', group: 'dataset' }, config.paths[0]), true)
  assert.equal(isPathWithin({ dataset: 'tank/data', path: 'cached/file', group: 'file' }, config.exclude_paths[0]), false)
  assert.equal(isPathWithin({ dataset: 'tank/data', path: 'cache/file', group: 'file' }, config.exclude_paths[0]), true)
  const selection = { paths: [{ path: '/data', group: 'folder' }, { path: '/data/media/Sony', group: 'folder' }],
    exclude_patterns: [{ path: '/data/media', group: 'folder' }, { path: '/data/media/Sony/cache', group: 'folder' }] }
  for (const [path, state] of [['/data/media/other', 'exclude'], ['/data/media/Sony/file', 'include'], ['/data/media/Sony/cache/file', 'exclude'], ['/data/medial/file', 'include']]) {
    assert.equal(pathSelectionOptions({ path, group: 'file' }, selection).state, state)
  }
  selection.paths.pop()
  assert.equal(pathSelectionOptions({ path: '/data/media/Sony/file', group: 'file' }, selection).state, 'exclude')
})

test('TrueNAS cannot submit a job whose explicit roots are all excluded', () => {
  const root = dataset => ({ dataset, path: '.', group: 'dataset' })
  assert.equal(hasTrueNASSelection({ paths: [root('tank/data')], exclude_paths: [root('tank')] }), true)
  assert.equal(hasTrueNASSelection({ paths: [root('tank/data')], exclude_paths: [root('tank/data')] }), false)
  assert.equal(hasTrueNASSelection({ paths: [root('tank/database')], exclude_paths: [root('tank/data')] }), true)
  const source = readFileSync(new URL('../src/components/jobs/JobManageDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const notices = []
  const dialog = runInNewContext(`${source}\n;({ jobForm, entriesConfigured, submitForm })`, {
    computed, reactive, ref, scheduleTiming, hasTrueNASSelection,
    defineProps: () => ({ editingJob: null }),
    defineEmits: () => () => assert.fail('excluded selection must not be saved'),
    defineModel: () => ref(true),
    defineOptions: () => {},
    watch: () => {},
    useQuasar: () => ({ notify: notice => notices.push(notice) }),
    useAgentStore: () => ({ agents: [] }),
  })
  Object.assign(dialog.jobForm, { name: 'NAS', type: 'truenas', config: { paths: [root('tank/data')], exclude_paths: [root('tank/data')] } })
  assert.equal(dialog.entriesConfigured.value, false)
  dialog.submitForm()
  assert.match(notices[0].message, /not excluded/)
})
