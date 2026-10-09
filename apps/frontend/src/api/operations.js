import { api } from 'boot/axios'

export async function startBackupJob({ jobId, repositoryId, recoveryKey = null, options = {} }) {
  const response = await api.post(`/jobs/${jobId}/run`, {
    repository_id: repositoryId,
    recovery_key: recoveryKey,
    options,
  })
  return response.data
}

export async function startRestore(data) {
  const response = await api.post('/restores/', data)
  return response.data
}

export async function cancelRestore(operationId) {
  const response = await api.post(`/restores/${operationId}/cancel`)
  return response.data
}

export async function startRepositoryCheck({ repositoryId, agentId, readDataSubset = null }) {
  const response = await api.post(`/repositories/${repositoryId}/check`, {
    agent_id: agentId,
    read_data_subset: readDataSubset || null,
  })
  return response.data
}

export async function startRepositoryUnlock({ repositoryId, agentId }) {
  const response = await api.post(`/repositories/${repositoryId}/unlock`, { agent_id: agentId })
  return response.data
}
