import { ref } from 'vue'
import { defineStore } from 'pinia'
import * as agentApi from 'src/api/agents'

export const useAgentStore = defineStore('agent', () => {
  const agents = ref([])
  const loading = ref(false)

  async function loadAgents() {
    loading.value = true
    try {
      agents.value = await agentApi.listAgents()
    } finally {
      loading.value = false
    }
  }

  async function deleteAgent(id) {
    await agentApi.deleteAgent(id)
    await loadAgents()
  }

  async function updateAgent(id, alias) {
    const updated = await agentApi.updateAgent(id, alias)
    agents.value = agents.value.map(agent => agent.id === id ? updated : agent)
  }

  async function updateAgentRepositories(id, repositoryIds, recoveryKey = null) {
    await agentApi.updateAgentRepositories(id, repositoryIds, recoveryKey)
    await loadAgents()
  }

  async function syncAgent(id, recoveryKey = null) {
    await agentApi.syncAgent(id, recoveryKey)
    await loadAgents()
  }

  async function runAction(id, action) {
    const result = await agentApi.runAction(id, action)
    // Updates are one-shot dispatches; a list reload would unnecessarily delay batch actions.
    if (action === 'rotate-ssh-key') await loadAgents()
    return result
  }

  async function deleteConnection(id, kind) {
    await agentApi.deleteConnection(id, kind)
    await loadAgents()
  }

  return {
    ...agentApi,
    agents, loading, loadAgents, deleteAgent, updateAgent, updateAgentRepositories,
    syncAgent, runAction, deleteConnection,
  }
})
