import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'

test('background operation updates stay quiet and preserve data on failure', async () => {
  const source = readFileSync(new URL('../src/pages/AgentOperationDetailPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const route = { params: { operationId: '1', agentId: '1' }, query: {} }
  let resolveRequest
  const store = { getOperation: () => new Promise(resolve => { resolveRequest = resolve }) }
  const page = runInNewContext(`${source}\n;({ operation, loading, loadOperation, queueOperationRefresh })`, {
    computed, ref,
    useRoute: () => route,
    useAgentStore: () => store,
    useQuasar: () => ({ notify: () => assert.fail('background error notification') }),
    subscribeToSocketEvents: async () => () => {},
    watch: () => {},
    onBeforeUnmount: () => {},
    defineOptions: () => {},
    shouldIgnoreApiError: () => false,
    getApiErrorMessage: () => 'error',
  })
  const initialLoad = page.loadOperation()
  assert.equal(page.loading.value, true)
  resolveRequest({ id: 1, state: 'running', data: {} })
  await initialLoad
  assert.equal(page.loading.value, false)

  const refresh = page.queueOperationRefresh()
  assert.equal(page.loading.value, false)
  resolveRequest({ id: 1, state: 'running', data: { bytes_processed: 512 } })
  await refresh
  assert.equal(page.operation.value.data.bytes_processed, 512)

  store.getOperation = async () => { throw new Error('temporary failure') }
  const previousOperation = page.operation.value
  await page.queueOperationRefresh()
  assert.equal(page.operation.value, previousOperation)
})
