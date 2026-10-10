import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'

function createRestoreDialog(type = 'file') {
  const source = readFileSync(new URL('../src/components/restore/RestoreDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const repository = { id: 2, name: 'Backups', location: '/backups' }
  const props = { job: { id: 3, agent_id: 1, type }, agents: [
    { id: 1, display_name: 'Source agent', online: true, repositories: [repository] },
    { id: 4, display_name: 'Other agent', online: true, repositories: [repository] },
  ], repositories: [repository] }
  const requests = []
  const dialog = runInNewContext(`${source}\n;({ resetDialog, snapshots, selectedSnapshotId, activeSection,
    sections, canGoNext, onFormSubmit, submitRestore, selectedAgentId, selectedMode, includePaths,
    overwritePolicy, overwriteConfirmed, crossAgentConfirmed, proxmoxSelection, proxmoxPanel })`, {
    computed, ref, watch: () => {}, defineProps: () => props,
    defineModel: () => ref(false), defineEmits: () => () => {}, defineOptions: () => {},
    useQuasar: () => ({ notify: () => {} }),
    useRestoreStore: () => ({}),
    useOperationStore: () => ({ startRestore: async data => { requests.push(data); return { operation_id: 5 } } }),
    shouldIgnoreApiError: () => false, getApiErrorMessage: error => error.message,
  })
  dialog.resetDialog()
  dialog.snapshots.value = [{ id: 'snapshot', proxmox_archive: true }]
  dialog.selectedSnapshotId.value = 'snapshot'
  return { dialog, requests }
}

test('Proxmox restore selection hides unsupported snapshots while archive restore keeps them', () => {
  const source = readFileSync(new URL('../src/components/restore/RestoreDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const dialog = runInNewContext(`${source}\n;({ snapshots, selectedMode, snapshotOptions })`, {
    computed, ref, defineProps: () => ({ job: { type: 'proxmox' }, agents: [], repositories: [] }),
    defineModel: () => ref(false), defineEmits: () => () => {}, defineOptions: () => {},
    useQuasar: () => ({}), useRestoreStore: () => ({}), useOperationStore: () => ({}), watch: () => {},
  })
  dialog.snapshots.value = [
    { id: 'tar', proxmox_archive: true }, { id: 'native', proxmox_archive: true },
    { id: 'legacy-vma', proxmox_archive: false }, { id: 'manifest', proxmox_archive: false },
    { id: 'failed', proxmox_archive: true, restore_error: 'Backup failed' },
  ]
  for (const mode of ['proxmox_vm', 'proxmox_files']) {
    dialog.selectedMode.value = mode
    assert.deepEqual([...dialog.snapshotOptions.value].map(snapshot => snapshot.value), ['tar', 'native'])
  }
  dialog.selectedMode.value = 'plain_file'
  assert.equal(dialog.snapshotOptions.value.length, 5)
})

test('file-browser preparation shows terminal failures and resets status on retry', async () => {
  const source = readFileSync(new URL('../src/components/restore/ProxmoxRestorePanel.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  let operation = { state: 'running', data: {} }
  const operations = { startRestore: async () => ({ operation_id: 1 }) }
  const panel = runInNewContext(`${source}\n;({ prepare, pollPreparation, preparing, phase, error })`, {
    computed, ref, defineProps: () => ({ source: {}, mode: 'proxmox_files' }), defineModel: () => ref({}),
    useRestoreStore: () => ({}), useOperationStore: () => operations,
    useAgentStore: () => ({ getOperation: async () => operation }),
    onMounted: () => {}, onBeforeUnmount: () => {}, defineExpose: () => {}, setTimeout: () => 0,
    getApiErrorMessage: error => error.message,
  })
  await panel.prepare()
  assert.equal(panel.preparing.value, true)
  operation = { state: 'failed', data: { restore_phase: 'Opening backup disks on demand' },
    logs: [{ level: 'error', message: 'Repository unavailable' }] }
  await panel.pollPreparation()
  assert.equal(panel.preparing.value, false)
  assert.equal(panel.phase.value, 'Preparation failed')
  assert.equal(panel.error.value, 'Repository unavailable')
  operation = { state: 'running', data: {} }
  await panel.prepare()
  assert.equal(panel.phase.value, 'Preparing guest file access…')
  assert.equal(panel.error.value, '')
  operations.startRestore = async () => { throw new Error('Agent offline') }
  await panel.prepare()
  assert.equal(panel.preparing.value, false)
  assert.equal(panel.phase.value, 'Preparation failed')
  assert.equal(panel.error.value, 'Agent offline')
})

test('file restores share steps and retain selections and guest sessions when navigating', async () => {
  for (const type of ['file', 'truenas', 'proxmox']) {
    const { dialog, requests } = createRestoreDialog(type)
    if (type === 'proxmox') dialog.selectedMode.value = 'proxmox_files'
    assert.deepEqual([...dialog.sections.value].map(section => section.name), ['source', 'selection', 'target'])
    dialog.onFormSubmit()
    assert.equal(dialog.activeSection.value, 'selection')
    assert.equal(dialog.canGoNext.value, false)
    await dialog.submitRestore()
    assert.equal(requests.length, 0)
    dialog.includePaths.value = ['/documents']
    if (type === 'proxmox') {
      dialog.proxmoxSelection.value = { session_id: 'session', volume: '/dev/sda1', include_paths: ['/documents'] }
    }
    dialog.onFormSubmit()
    assert.equal(dialog.activeSection.value, 'target')
    dialog.activeSection.value = 'selection'
    assert.deepEqual([...dialog.includePaths.value], ['/documents'])
    if (type === 'proxmox') assert.equal(dialog.proxmoxSelection.value.session_id, 'session')
  }
})

test('restore start requires file confirmations or a ready VM target', async () => {
  const { dialog, requests } = createRestoreDialog()
  dialog.selectedAgentId.value = 4
  dialog.onFormSubmit()
  dialog.includePaths.value = ['/documents']
  dialog.onFormSubmit()
  dialog.overwritePolicy.value = 'overwrite'
  await dialog.onFormSubmit()
  assert.equal(requests.length, 0)
  dialog.overwriteConfirmed.value = true
  await dialog.onFormSubmit()
  assert.equal(requests.length, 0)
  dialog.crossAgentConfirmed.value = true
  await dialog.onFormSubmit()
  assert.equal(requests.length, 1)
  assert.equal(requests[0].agent_id, 4)
  assert.equal(requests[0].overwrite_policy, 'overwrite')
  assert.deepEqual([...requests[0].include_paths], ['/documents'])

  const vm = createRestoreDialog('proxmox')
  assert.deepEqual([...vm.dialog.sections.value].map(section => section.name), ['source', 'target'])
  vm.dialog.onFormSubmit()
  vm.dialog.proxmoxPanel.value = { canSubmit: false }
  await vm.dialog.onFormSubmit()
  assert.equal(vm.requests.length, 0)
  vm.dialog.proxmoxSelection.value = { vmid: 101, storage: 'local' }
  vm.dialog.proxmoxPanel.value = { canSubmit: true, handOff: () => {} }
  await vm.dialog.onFormSubmit()
  assert.equal(vm.requests[0].mode, 'proxmox_vm')
  assert.equal(vm.requests[0].restore_location, null)
})
