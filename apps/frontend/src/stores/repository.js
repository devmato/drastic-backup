import { ref } from 'vue'
import { defineStore } from 'pinia'
import * as repositoryApi from 'src/api/repositories'

export const useRepositoryStore = defineStore('repository', () => {
  const repositories = ref([])
  const loading = ref(false)

  async function loadRepositories() {
    loading.value = true
    try {
      repositories.value = await repositoryApi.listRepositories()
    } finally {
      loading.value = false
    }
  }

  async function createRepository(data) {
    const result = await repositoryApi.createRepository(data)
    await loadRepositories()
    return result
  }

  async function updateRepository(repositoryId, data) {
    const result = await repositoryApi.updateRepository(repositoryId, data)
    await loadRepositories()
    return result
  }

  async function deleteRepository(repositoryId) {
    await repositoryApi.deleteRepository(repositoryId)
    await loadRepositories()
  }

  return { repositories, loading, loadRepositories, createRepository, updateRepository, deleteRepository,
    revealRepositoryPassword: repositoryApi.revealRepositoryPassword, getUnlockAgents: repositoryApi.getUnlockAgents }
})
