import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, reactive, ref } from 'vue'
import { getBackupState, getBackupStateColor } from '../src/utils/backup-results.js'
import { lastSevenDays, operationFilters, operationStates, reportListRoute, summarizeOperationResults } from '../src/utils/operation-results.js'

test('report groups aggregate every type and link each status with the same period', () => {
  const types = ['backup', 'restore', 'agent_update', 'retention', 'repository_check', 'repository_unlock', 'repository_stats', 'sync', 'command']
  const counts = types.flatMap(type => operationStates.map((state, index) => ({ type, state: state.value, count: index + 1 })))
  const groups = summarizeOperationResults(counts)
  const period = lastSevenDays(new Date('2026-10-10T12:34:56.789Z'))
  assert.deepEqual(period, { since: '2026-10-03T12:34:56.789Z', until: '2026-10-10T12:34:56.789Z' })
  assert.equal(groups.reduce((sum, group) => sum + group.total, 0), counts.reduce((sum, row) => sum + row.count, 0))
  for (const group of groups) {
    assert.equal(group.total, group.value === 'other' ? 90 : 15)
    assert.equal(group.segments.reduce((sum, segment) => sum + segment.count, 0), group.total)
    for (const segment of group.segments) {
      assert.equal(getBackupStateColor(segment.value), segment.color)
      const link = reportListRoute(period, group.value, segment.value)
      assert.equal(link.path, '/agents/operations')
      assert.deepEqual(operationFilters(link.query), link.query)
      assert.equal(link.query.state, segment.value)
      assert.equal(link.query.since, period.since)
      assert.equal(link.query.until, period.until)
      assert.equal(group.value === 'other' ? link.query.group : link.query.type, group.value)
    }
  }
  assert.deepEqual(reportListRoute(period, null, 'attention').query, { ...period, state: 'attention' })
  assert.deepEqual(operationFilters({ state: 'failed', scope: 'all', unrelated: 'x', job_id: null }), { state: 'failed' })
  assert.ok(summarizeOperationResults([]).every(group => group.total === 0 && group.segments.every(segment => segment.count === 0)))
  assert.ok(!operationStates.some(state => state.value === 'never'))
  assert.equal(getBackupStateColor('unknown'), 'grey-6')
})

test('status and type charts partition the same reports while first-backup guidance follows job configuration', () => {
  const source = readFileSync(new URL('../src/pages/IndexPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const agents = reactive({ agents: [] })
  const jobs = reactive({ agentJobs: [] })
  const { setupPrompt, reportCharts, reportCounts, reportPeriod } = runInNewContext(`${source}\n;({ setupPrompt, reportCharts, reportCounts, reportPeriod })`, {
    computed, ref, getBackupState, lastSevenDays, operationStates, reportListRoute, summarizeOperationResults,
    useAgentStore: () => agents,
    useJobStore: () => jobs,
    onMounted: () => {},
    defineOptions: () => {},
    getApiErrorMessage: () => 'error',
  })
  assert.ok(reportCharts.value.every(chart => chart.segments.every(segment => segment.count === 0)))
  const period = lastSevenDays(new Date('2026-10-10T12:34:56.789Z'))
  reportPeriod.value = period
  reportCounts.value = [
    { type: 'backup', state: 'success', count: 4 }, { type: 'backup', state: 'failed', count: 2 },
    { type: 'restore', state: 'warning', count: 3 }, { type: 'agent_update', state: 'success', count: 1 },
    { type: 'retention', state: 'failed', count: 2 }, { type: 'sync', state: 'running', count: 1 },
  ]
  const [statusChart, typeChart] = reportCharts.value
  for (const chart of reportCharts.value) {
    assert.equal(chart.segments.reduce((sum, segment) => sum + segment.count, 0), 13)
    for (const segment of chart.segments) {
      assert.equal(segment.to.path, '/agents/operations')
      assert.equal(segment.to.query.since, period.since)
      assert.equal(segment.to.query.until, period.until)
    }
  }
  assert.equal(statusChart.segments.find(segment => segment.value === 'success').count, 5)
  assert.equal(statusChart.segments.find(segment => segment.value === 'failed').count, 4)
  assert.equal(statusChart.segments.find(segment => segment.value === 'warning').count, 3)
  assert.equal(statusChart.segments.find(segment => segment.value === 'cancelled').count, 0)
  assert.ok(statusChart.segments.every(segment => segment.to.query.state === segment.value && !segment.to.query.type && !segment.to.query.group))
  assert.equal(typeChart.segments.find(segment => segment.value === 'backup').count, 6)
  assert.equal(typeChart.segments.find(segment => segment.value === 'other').count, 3)
  assert.ok(typeChart.segments.every(segment => !segment.to.query.state && (segment.to.query.type || segment.to.query.group) === segment.value))
  assert.equal(setupPrompt.value.to, '/agents/install')
  agents.agents.push({ id: 1, online: false })
  assert.equal(setupPrompt.value.to, '/agents')
  agents.agents.push({ id: 2, online: true })
  assert.equal(setupPrompt.value.to.path, '/jobs')
  assert.equal(setupPrompt.value.to.query.add_for_agent, '2')
  agents.agents[1].online = false
  assert.equal(setupPrompt.value.to, '/agents')
  jobs.agentJobs.push({ id: 1, jobs: [{ id: 1, last_operation: null }] })
  assert.equal(setupPrompt.value, null)
})

test('report filters persist in URLs and background reloads use applied filters', async () => {
  const source = readFileSync(new URL('../src/pages/AgentReportsPage.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const period = lastSevenDays(new Date('2026-10-10T12:34:56.789Z'))
  const route = reactive({ params: {}, query: { ...period, group: 'other', state: 'failed', agent_id: '2', job_id: '3' } })
  const requests = [], links = []
  const page = runInNewContext(`${source}\n;({ syncFiltersFromRoute, filterType, filterState, filterSince, filterUntil, applyFilters, loadOperations, showOperation, operations })`, {
    computed, ref, operationStates, operationFilters, lastSevenDays,
    useRoute: () => route,
    useRouter: () => ({ push: async link => { links.push(JSON.parse(JSON.stringify(link))); if (!link.path) route.query = link.query } }),
    useQuasar: () => ({ notify: () => assert.fail('unexpected error') }),
    useAgentStore: () => ({ agents: [] }),
    listOperations: async params => { requests.push(JSON.parse(JSON.stringify(params))); return [{ id: 7, agent_id: 2 }] },
    createQueuedReload: callback => callback,
    subscribeToSocketEvents: async () => () => {},
    watch: () => {}, onMounted: () => {}, onBeforeUnmount: () => {}, defineOptions: () => {},
  })
  page.syncFiltersFromRoute()
  assert.equal(page.filterType.value, 'other')
  assert.equal(page.filterSince.value, period.since.slice(0, -1))
  assert.equal(page.filterUntil.value, period.until.slice(0, -1))
  page.filterState.value = 'warning'
  await page.loadOperations(true)
  assert.equal(requests[0].state, 'failed')
  await page.applyFilters()
  assert.deepEqual(links[0].query, { group: 'other', state: 'warning', agent_id: '2', job_id: '3', ...period })
  page.showOperation(page.operations.value[0])
  assert.equal(links[1].path, '/agents/2/operations/7')
  assert.equal(links[1].query.scope, 'all')
  assert.equal(links[1].query.group, 'other')
  route.params.agentId = '1'
  await page.loadOperations()
  assert.equal(requests[1].agent_id, '1')
})
