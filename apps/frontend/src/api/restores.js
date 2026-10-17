import { api } from 'boot/axios'

export async function proxmoxAction(data) {
  const response = await api.post('/restores/proxmox', data, { timeout: 125000 })
  return response.data
}

export async function getSnapshots({ jobId, agentId, repositoryId }) {
  const params = new URLSearchParams({
    agent_id: agentId,
    repository_id: repositoryId,
  })
  if (jobId != null) params.set('job_id', jobId)
  const response = await api.get(`/restores/snapshots?${params}`)
  return response.data.snapshots || []
}

export async function deleteRestorePoint({ agentId, repositoryId, snapshotId, snapshotIds }) {
  return (await api.post('/restores/snapshots/delete', {
    agent_id: agentId, repository_id: repositoryId, snapshot_id: snapshotId, snapshot_ids: snapshotIds,
  })).data
}

export async function getEntries({ agentId, repositoryId, snapshotId, path = '/' }) {
  const params = new URLSearchParams({
    agent_id: agentId,
    repository_id: repositoryId,
    snapshot_id: snapshotId,
    path,
  })
  const response = await api.get(`/restores/entries?${params}`)
  return response.data.entries || []
}
