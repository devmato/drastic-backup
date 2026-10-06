import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'

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
