import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'

function createRestoreDialog(type = 'file', protocol = 17) {
  const source = readFileSync(new URL('../src/components/restore/RestoreDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const repository = { id: 2, name: 'Backups', location: '/backups' }
  const props = { job: { id: 3, agent_id: 1, type }, agents: [
    { id: 1, display_name: 'Source agent', online: true, protocol_version: protocol, repositories: [repository] },
    { id: 4, display_name: 'Other agent', online: true, repositories: [repository] },
  ], repositories: [repository] }
  const requests = []
  let confirm
  const visible = ref(true)
  const dialog = runInNewContext(`${source}\n;({ resetDialog, snapshots, selectedSnapshotId, activeSection,
    sections, canGoNext, onFormSubmit, submitRestore, selectedAgentId, selectedMode, includePaths,
    overwritePolicy, overwriteConfirmed, proxmoxSelection, proxmoxPanel, advancedOpen,
    ownershipByName, skipXattrs, supportsRestoreMetadataOptions, selectedPointId, restorePoints, confirmDeletePoint })`, {
    computed, ref, watch: () => {}, defineProps: () => props,
    defineModel: () => visible, defineEmits: () => () => {}, defineOptions: () => {},
    useQuasar: () => ({ notify: () => {}, dialog: () => ({ onOk: callback => { confirm = callback } }) }),
    useRestoreStore: () => ({ deleteRestorePoint: async data => { requests.push(data); return { operation_id: 6 } } }),
    useOperationStore: () => ({ startRestore: async data => { requests.push(data); return { operation_id: 5 } } }),
    shouldIgnoreApiError: () => false, getApiErrorMessage: error => error.message,
  })
  dialog.resetDialog()
  dialog.snapshots.value = [{ id: 'snapshot', proxmox_archive: true }]
  dialog.selectedSnapshotId.value = 'snapshot'
  return { dialog, requests, confirm: () => confirm(), visible }
}

test('repository restore derives supported modes from each snapshot without a live job', () => {
  const source = readFileSync(new URL('../src/components/restore/RestoreDialog.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const dialog = runInNewContext(`${source}\n;({ snapshots, selectedSnapshotId, modeOptions })`, {
    computed, ref, defineProps: () => ({ job: null, agents: [], repositories: [] }),
    defineModel: () => ref(false), defineEmits: () => () => {}, defineOptions: () => {},
    useQuasar: () => ({}), useRestoreStore: () => ({}), useOperationStore: () => ({}), watch: () => {},
  })
  dialog.snapshots.value = [
    { id: 'tar', proxmox_archive: true }, { id: 'native', proxmox_archive: true },
    { id: 'legacy-vma', proxmox_archive: false }, { id: 'manifest', proxmox_archive: false },
    { id: 'failed', proxmox_archive: true, restore_error: 'Backup failed' },
  ]
  for (const id of ['tar', 'native', 'legacy-vma', 'manifest', 'failed']) {
    dialog.selectedSnapshotId.value = id
    assert.deepEqual([...dialog.modeOptions.value].map(mode => mode.value), ['tar', 'native'].includes(id)
      ? ['proxmox_vm', 'proxmox_files', 'plain_file'] : ['plain_file'])
  }
})

test('restore points group run components and deletion confirms exactly that group', async () => {
  const { dialog, requests, confirm, visible } = createRestoreDialog('proxmox', 22)
  dialog.snapshots.value = [
    { id: 'a', restore_point_id: 'run:job:operation', proxmox_archive: true },
    { id: 'b', restore_point_id: 'run:job:operation', proxmox_archive: true },
    { id: 'foreign', restore_point_id: 'snapshot:foreign' },
  ]
  dialog.selectedPointId.value = 'run:job:operation'
  assert.equal(dialog.restorePoints.value.length, 2)
  assert.equal(dialog.selectedSnapshotId.value, 'a')
  dialog.selectedSnapshotId.value = 'b'
  dialog.confirmDeletePoint()
  assert.equal(requests.length, 0)
  dialog.selectedPointId.value = 'snapshot:foreign'
  await confirm()
  assert.equal(requests.length, 0, 'stale confirmation must not delete another selection')
  dialog.selectedPointId.value = 'run:job:operation'
  dialog.confirmDeletePoint()
  await confirm()
  assert.deepEqual([...requests[0].snapshotIds], ['a', 'b'])
  assert.equal(visible.value, false, 'progress is handed to the existing operation view')
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

test('restore to another agent only requires overwrite confirmation or a ready VM target', async () => {
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
  assert.equal(requests.length, 1)
  assert.equal(requests[0].agent_id, 4)
  assert.equal(requests[0].overwrite_policy, 'overwrite')
  assert.deepEqual([...requests[0].include_paths], ['/documents'])

  const vm = createRestoreDialog('proxmox')
  vm.dialog.selectedAgentId.value = 4
  assert.deepEqual([...vm.dialog.sections.value].map(section => section.name), ['source', 'target'])
  vm.dialog.onFormSubmit()
  vm.dialog.proxmoxPanel.value = { canSubmit: false }
  await vm.dialog.onFormSubmit()
  assert.equal(vm.requests.length, 0)
  vm.dialog.proxmoxSelection.value = { vmid: 101, storage: 'local' }
  vm.dialog.proxmoxPanel.value = { canSubmit: true, handOff: () => {} }
  await vm.dialog.onFormSubmit()
  assert.equal(vm.requests[0].mode, 'proxmox_vm')
  assert.equal(vm.requests[0].agent_id, 4)
  assert.equal(vm.requests[0].restore_location, null)
})

test('Restic metadata options are opt-in, independent and only sent to supported plain file restores', async () => {
  for (const mode of ['plain_file', 'proxmox_files', 'proxmox_vm']) {
    for (const protocol of [16, 17]) {
      const { dialog, requests } = createRestoreDialog('proxmox', protocol)
      dialog.selectedMode.value = mode
      dialog.activeSection.value = 'target'
      dialog.includePaths.value = ['/documents']
      dialog.proxmoxSelection.value = { session_id: 'session', volume: '/dev/sda1', include_paths: ['/documents'] }
      dialog.proxmoxPanel.value = { canSubmit: true, handOff: () => {} }
      assert.equal(dialog.advancedOpen.value, false)
      assert.equal(dialog.ownershipByName.value, false)
      assert.equal(dialog.skipXattrs.value, false)
      assert.equal(dialog.supportsRestoreMetadataOptions.value, protocol === 17)
      await dialog.submitRestore()
      assert.ok(!requests[0].ownership_by_name && !requests[0].skip_xattrs)
      for (const [ownership, xattrs] of [[true, false], [false, true], [true, true]]) {
        dialog.ownershipByName.value = ownership
        dialog.skipXattrs.value = xattrs
        await dialog.submitRestore()
        const sent = requests.at(-1)
        const supported = protocol === 17 && mode === 'plain_file'
        assert.equal(sent.ownership_by_name, supported ? ownership : undefined)
        assert.equal(sent.skip_xattrs, supported ? xattrs : undefined)
      }
      dialog.resetDialog()
      assert.equal(dialog.ownershipByName.value, false)
      assert.equal(dialog.skipXattrs.value, false)
    }
  }
})
