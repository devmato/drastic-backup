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
