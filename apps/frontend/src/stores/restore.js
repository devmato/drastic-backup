import { defineStore } from 'pinia'
import { api } from 'boot/axios'

export const useRestoreStore = defineStore('restore', () => {
  async function getSnapshots({ jobId, agentId, repositoryId }) {
    const params = new URLSearchParams({
      job_id: jobId,
      agent_id: agentId,
      repository_id: repositoryId,
    })
    const response = await api.get(`/restores/snapshots?${params}`)
    return response.data.snapshots || []
  }

  async function getEntries({ agentId, repositoryId, snapshotId, path = '/' }) {
    const params = new URLSearchParams({
      agent_id: agentId,
      repository_id: repositoryId,
      snapshot_id: snapshotId,
      path,
    })
    const response = await api.get(`/restores/entries?${params}`)
    return response.data.entries || []
  }

  return { getSnapshots, getEntries }
})
