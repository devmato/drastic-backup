import { defineStore } from 'pinia'
import { api } from 'boot/axios'

export const useRestoreStore = defineStore('restore', () => {
  async function getOptions(jobId) {
    const response = await api.get(`/restores/options?job_id=${jobId}`)
    return response.data
  }

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

  async function startRestore(data) {
    const response = await api.post('/restores/', data)
    return response.data
  }

  async function cancelRestore(reportId) {
    const response = await api.post(`/restores/${reportId}/cancel`)
    return response.data
  }

  return { getOptions, getSnapshots, getEntries, startRestore, cancelRestore }
})
