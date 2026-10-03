export const backupStates = [
  { value: 'success', label: 'Successful', color: 'green-5', icon: 'check_circle' },
  { value: 'failed', label: 'Failed', color: 'red-5', icon: 'error' },
  { value: 'warning', label: 'Warning', color: 'orange-5', icon: 'warning' },
  { value: 'cancelled', label: 'Cancelled', color: 'grey-7', icon: 'cancel' },
  { value: 'running', label: 'Running', color: 'blue-4', icon: 'pending' },
  { value: 'never', label: 'Never run', color: 'blue-grey-4', icon: 'schedule' },
]

export function getBackupState(job) {
  return job.last_operation?.state || 'never'
}

export function getBackupStateColor(state) {
  return backupStates.find(item => item.value === state)?.color || 'grey-6'
}

export function summarizeBackupResults(jobs) {
  let start = 0
  return backupStates.map(state => {
    const count = jobs.filter(job => getBackupState(job) === state.value).length
    const fraction = jobs.length ? count / jobs.length : 0
    const segment = { ...state, count, fraction, start }
    start += fraction
    return segment
  })
}

export function filterAgentJobs(agentJobs, state) {
  if (!state) return agentJobs
  return agentJobs.map(agent => ({
    ...agent,
    jobs: agent.jobs.filter(job => state === 'attention'
      ? ['warning', 'failed'].includes(getBackupState(job))
      : getBackupState(job) === state),
  })).filter(agent => agent.jobs.length > 0)
}
