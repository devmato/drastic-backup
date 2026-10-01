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

  async function runAction(agentId, action) {
    const response = await api.post(`/agents/${agentId}/actions/${action}`)
    if (action === 'rotate-ssh-key') await loadAgents()
    return response.data
  }

  async function getAgentOperations(agentId, params = {}) {
    const query = new URLSearchParams(params).toString()
    const response = await api.get(`/agents/${agentId}/operations?${query}`)
    return response.data
  }

  async function getProxmoxSettings(agentId) {
    const response = await api.get(`/agents/${agentId}/proxmox-settings`)
    return response.data
  }

  async function updateProxmoxSettings(agentId, settings) {
    const response = await api.put(`/agents/${agentId}/proxmox-settings`, settings)
    return response.data
  }

  async function testProxmoxSettings(agentId, settings) {
    const response = await api.post(`/agents/${agentId}/proxmox-settings/test`, settings)
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

  return { agents, loading, loadAgents, deleteAgent, updateAgentRepositories, syncAgent, runAction, getAgentOperations, deleteOperation, getOperation, getInstallOptions, getProxmoxSettings, updateProxmoxSettings, testProxmoxSettings }
})
