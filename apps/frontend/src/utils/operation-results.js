/** Report groups, dashboard counts and URL filters shared by report views. */
import { backupStates } from './backup-results.js'

export const operationStates = backupStates.filter(state => state.value !== 'never')
export const reportGroups = [
  { value: 'backup', label: 'Backup', color: 'indigo-4' },
  { value: 'restore', label: 'Restore', color: 'purple-4' },
  { value: 'agent_update', label: 'Agent update', color: 'teal-4' },
  { value: 'other', label: 'Other', color: 'blue-grey-4' },
]

export function lastSevenDays(until = new Date()) {
  return { since: new Date(until.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString(), until: until.toISOString() }
}

export function last24Hours(until = new Date()) {
  return { since: new Date(until.getTime() - 24 * 60 * 60 * 1000).toISOString(), until: until.toISOString() }
}

export function summarizeOperationResults(counts) {
  return reportGroups.map(group => {
    const rows = counts.filter(row => group.value === 'other'
      ? !reportGroups.slice(0, 3).some(item => item.value === row.type)
      : row.type === group.value)
    const total = rows.reduce((sum, row) => sum + row.count, 0)
    const segments = operationStates.map(state => {
      const count = rows.filter(row => row.state === state.value).reduce((sum, row) => sum + row.count, 0)
      return { ...state, count }
    })
    return { ...group, total, segments }
  })
}

export function reportListRoute(period, type = null, state = null) {
  return {
    path: '/agents/operations',
    query: {
      ...period,
      ...(type === 'other' ? { group: 'other' } : type ? { type } : {}),
      ...(state ? { state } : {}),
    },
  }
}

export function operationFilters(query) {
  return Object.fromEntries(['type', 'group', 'state', 'agent_id', 'job_id', 'since', 'until']
    .filter(key => typeof query[key] === 'string' && query[key])
    .map(key => [key, query[key]]))
}
