import { ref } from 'vue'
import { defineStore } from 'pinia'
import * as retentionApi from 'src/api/retentions'

export const useRetentionStore = defineStore('retention', () => {
  const retentions = ref([])
  const loading = ref(false)

  async function loadRetentions() {
    loading.value = true
    try {
      retentions.value = await retentionApi.listRetentions()
    } finally {
      loading.value = false
    }
  }

  async function createRetention(data) {
    await retentionApi.createRetention(data)
    await loadRetentions()
  }

  async function updateRetention(retentionId, data) {
    await retentionApi.updateRetention(retentionId, data)
    await loadRetentions()
  }

  async function deleteRetention(retentionId) {
    await retentionApi.deleteRetention(retentionId)
    await loadRetentions()
  }

  return { retentions, loading, loadRetentions, createRetention, updateRetention, deleteRetention }
})
