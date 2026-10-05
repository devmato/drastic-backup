import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { ref } from 'vue'
import { format } from 'quasar'

test('repository sizes use raw-data for all repository types and retain numeric sorting', () => {
  const source = readFileSync(new URL('../src/pages/RepositoriesPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const { columns } = runInNewContext(`${source}\n;({ columns })`, {
    ref, format,
    useQuasar: () => ({}),
    useRepositoryStore: () => ({}),
    useUserStore: () => ({}),
    useOperationStore: () => ({}),
    onMounted: () => {},
    defineOptions: () => {},
  })
  const size = columns.find(column => column.name === 'size')
  assert.equal(size.sortable, true)
  for (const kind of ['native', 'custom']) {
    const field = stats => size.field({ kind, stats })
    assert.equal(field({ mode: 'raw-data', total_size: 1024 }), 1024)
    assert.equal(size.format(field({ mode: 'raw-data', total_size: 0 })), '0.0B')
    assert.equal(size.format(field({ mode: 'raw-data', total_size: 1024 })), '1.0KB')
    for (const stats of [undefined, null, {}, { total_size: 1024 }, { mode: 'restore-size', total_size: 1024 }, { mode: 'raw-data' }]) {
      assert.equal(field(stats), null)
      assert.equal(size.format(field(stats)), '—')
    }
  }
})
