import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from 'boot/axios'

export const useAgentStore = defineStore('agent', () => {
  const agents = ref([])
  const loading = ref(false)

  async function loadAgents() {
    loading.value = true
    try {
      const response = await api.get('/agents/')
      agents.value = response.data
    } finally {
      loading.value = false
    }
  }

  async function deleteAgent(agentId) {
    await api.delete(`/agents/${agentId}`)
    await loadAgents()
  }

  async function updateAgentRepositories(agentId, repositoryIds, recoveryKey = null) {
    const payload = { repository_ids: repositoryIds }
    if (recoveryKey) {
      payload.recovery_key = recoveryKey
    }
    await api.put(`/agents/${agentId}/repositories`, payload)
    await loadAgents()
  }

  async function syncAgent(agentId, recoveryKey = null) {
    const payload = {}
    if (recoveryKey) {
      payload.recovery_key = recoveryKey
    }
    await api.post(`/agents/${agentId}/sync`, payload)
    await loadAgents()
  }

  async function getAgentOperations(agentId, params = {}) {
    const query = new URLSearchParams(params).toString()
    const response = await api.get(`/agents/${agentId}/operations?${query}`)
    return response.data
  }

  async function deleteOperation(operationId) {
    await api.delete(`/agents/operations/${operationId}`)
  }

  async function getOperation(operationId) {
    const response = await api.get(`/agents/operations/${operationId}`)
    return response.data
  }

  async function getInstallOptions() {
    const response = await api.get('/agents/install-options')
    return response.data
  }

  return { agents, loading, loadAgents, deleteAgent, updateAgentRepositories, syncAgent, getAgentOperations, deleteOperation, getOperation, getInstallOptions }
})
