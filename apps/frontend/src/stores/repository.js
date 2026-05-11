import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from 'boot/axios'

export const useRepositoryStore = defineStore('repository', () => {
  const repositories = ref([])
  const loading = ref(false)

  async function loadRepositories() {
    loading.value = true
    try {
      const response = await api.get('/repositories/')
      repositories.value = response.data
    } finally {
      loading.value = false
    }
  }

  async function createRepository(data) {
    const response = await api.post('/repositories/', data)
    await loadRepositories()
    return response.data
  }

  async function updateRepository(repositoryId, data) {
    const response = await api.put(`/repositories/${repositoryId}`, data)
    await loadRepositories()
    return response.data
  }

  async function deleteRepository(repositoryId) {
    await api.delete(`/repositories/${repositoryId}`)
    await loadRepositories()
  }

  async function unlockRepository(repositoryId, agentId) {
    const response = await api.post(`/repositories/${repositoryId}/unlock`, { agent_id: agentId })
    return response.data
  }

  async function checkRepository(repositoryId, agentId, readDataSubset = null) {
    const response = await api.post(`/repositories/${repositoryId}/check`, {
      agent_id: agentId,
      read_data_subset: readDataSubset || null,
    })
    return response.data
  }

  async function revealRepositoryPassword(repositoryId, accountPassword) {
    const response = await api.post(`/repositories/${repositoryId}/reveal-password`, {
      account_password: accountPassword,
    })
    return response.data.password
  }

  async function getUnlockAgents(repositoryId) {
    const response = await api.get(`/repositories/${repositoryId}/unlock_agents`)
    return response.data
  }

  return { repositories, loading, loadRepositories, createRepository, updateRepository, deleteRepository, unlockRepository, checkRepository, revealRepositoryPassword, getUnlockAgents }
})
